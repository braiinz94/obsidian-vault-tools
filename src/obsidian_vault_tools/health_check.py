"""Bilan de santé qualitatif d'un coffre, au-delà du lint de base.

Six contrôles, en lecture seule :
  1. Notes `to-compile` vieillissantes (dossier brut, plus de 7 jours)
  2. Notes `to-verify` stagnantes (dossier des notes, plus de 30 jours)
  3. Clusters anémiques (moins de 5 notes) ou surchargés (plus de 50)
  4. Dérive du frontmatter (tags rares dans un cluster)
  5. Concepts isolés (au moins 3 liens communs sans lien mutuel)
  6. Titres quasi identiques (doublons probables)

Usage :
    vault-health [coffre] [--json] [--cluster NOM] [--notes-dir wiki] [--raw-dir raw]
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import Any

from ._common import (
    extract_wikilinks,
    iter_markdown,
    notes_root,
    parse_frontmatter,
    read_text,
    resolve_vault,
    safe,
)

Page = tuple[Path, dict, str]

COMPILE_MAX_DAYS = 7
VERIFY_MAX_DAYS = 30
CLUSTER_MIN = 5
CLUSTER_MAX = 50
RARE_TAG_SHARE = 0.2  # tag porté par moins de 20 % des occurrences du cluster
RARE_TAG_MAX = 3  # … et moins de 3 fois
MIN_COMMON_REFS = 3
PAIRWISE_LIMIT = 500  # au-delà, la comparaison tous contre tous est échantillonnée


def parse_date(v: object) -> date | None:
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    if isinstance(v, str):
        try:
            return datetime.strptime(v[:10], "%Y-%m-%d").date()
        except ValueError:
            return None
    return None


def as_tags(value: object) -> list[str]:
    if not value:
        return []
    if isinstance(value, str):
        return [t.strip() for t in value.split(",") if t.strip()]
    if isinstance(value, list):
        return [str(t) for t in value]
    return []


def load_pages(roots: list[Path], cluster: str | None = None) -> list[Page]:
    """(chemin, frontmatter, texte) des notes dont le frontmatter est valide."""
    pages = []
    seen: set[Path] = set()
    for root in roots:
        if not root.is_dir():
            continue
        for p in iter_markdown(root):
            if p in seen:
                continue
            seen.add(p)
            text = read_text(p)
            if text is None:
                continue
            fm = parse_frontmatter(text)
            if not isinstance(fm, dict):
                continue
            if cluster and fm.get("cluster") != cluster:
                continue
            pages.append((p, fm, text))
    return pages


def check_to_compile_aging(pages: list[Page], raw: Path, vault: Path, today: date) -> list[dict]:
    stale = []
    for p, fm, _ in pages:
        if not p.is_relative_to(raw) or "to-compile" not in as_tags(fm.get("tags")):
            continue
        d = parse_date(fm.get("date")) or parse_date(fm.get("clipped_at"))
        if d and (today - d).days > COMPILE_MAX_DAYS:
            stale.append(
                {
                    "path": p.relative_to(vault).as_posix(),
                    "days": (today - d).days,
                    "cluster": fm.get("cluster", "?"),
                }
            )
    return stale


def check_to_verify_stagnant(pages: list[Page], wiki: Path, vault: Path, today: date) -> list[dict]:
    stagnant = []
    for p, fm, _ in pages:
        if not p.is_relative_to(wiki):
            continue
        if (fm.get("statut") or fm.get("status")) != "to-verify":
            continue
        d = parse_date(fm.get("date"))
        if d and (today - d).days > VERIFY_MAX_DAYS:
            stagnant.append({"path": p.relative_to(vault).as_posix(), "days": (today - d).days})
    return stagnant


def check_cluster_distribution(pages: list[Page], wiki: Path) -> dict:
    by_cluster: Counter[str] = Counter()
    for p, fm, _ in pages:
        if p.is_relative_to(wiki) and fm.get("cluster"):
            by_cluster[str(fm["cluster"])] += 1
    return {
        "distribution": dict(by_cluster),
        "anemic": sorted(
            ((c, n) for c, n in by_cluster.items() if n < CLUSTER_MIN), key=lambda x: x[1]
        ),
        "overloaded": sorted(
            ((c, n) for c, n in by_cluster.items() if n > CLUSTER_MAX), key=lambda x: -x[1]
        ),
    }


def check_frontmatter_drift(pages: list[Page], wiki: Path) -> list[dict]:
    by_cluster_tags: defaultdict[str, Counter[str]] = defaultdict(Counter)
    for p, fm, _ in pages:
        if not p.is_relative_to(wiki) or not fm.get("cluster"):
            continue
        for t in as_tags(fm.get("tags")):
            by_cluster_tags[str(fm["cluster"])][t] += 1

    drift = []
    for cluster, tag_counts in by_cluster_tags.items():
        total = sum(tag_counts.values())
        # Tag porté par moins de 20 % des occurrences et moins de 3 fois : valeur aberrante possible
        rare = sorted(
            (
                (t, n)
                for t, n in tag_counts.items()
                if n / max(total, 1) < RARE_TAG_SHARE and n < RARE_TAG_MAX
            ),
            key=lambda x: (x[1], x[0]),
        )
        if rare:
            drift.append({"cluster": cluster, "rare_tags": rare[:5]})
    return drift


def check_isolated_concepts(pages: list[Page]) -> list[dict]:
    outgoing = {p.stem: extract_wikilinks(text) for p, _, text in pages}
    stems = list(outgoing)
    # Comparaison tous contre tous en O(n²) : au-delà de 500 notes, on se limite
    # aux notes peu liées (1 ou 2 liens sortants), les plus susceptibles d'être isolées.
    if len(stems) > PAIRWISE_LIMIT:
        stems = [s for s in stems if 0 < len(outgoing[s]) < MIN_COMMON_REFS][:200]

    isolated: list[dict[str, Any]] = []
    for i, s1 in enumerate(stems):
        for s2 in stems[i + 1 :]:
            if s2 in outgoing[s1] or s1 in outgoing[s2]:
                continue
            common = outgoing[s1] & outgoing[s2]
            if len(common) >= MIN_COMMON_REFS:
                isolated.append(
                    {"pages": (s1, s2), "common_refs": len(common), "sample": sorted(common)[:3]}
                )
    isolated.sort(key=lambda x: -x["common_refs"])
    return isolated[:20]


def check_duplicate_titles(pages: list[Page], wiki: Path, vault: Path) -> list[tuple[str, list]]:
    by_slug: defaultdict[str, list[str]] = defaultdict(list)
    for p, _, _ in pages:
        if p.is_relative_to(wiki):
            slug = re.sub(r"[^a-z0-9]", "", p.stem.lower())[:40]
            by_slug[slug].append(p.relative_to(vault).as_posix())
    return [(slug, paths) for slug, paths in by_slug.items() if len(paths) > 1]


def run(vault: Path, wiki: Path, raw: Path, cluster: str | None, today: date) -> dict:
    pages = load_pages([wiki, raw], cluster)
    return {
        "pages": len(pages),
        "to_compile_aging": check_to_compile_aging(pages, raw, vault, today),
        "to_verify_stagnant": check_to_verify_stagnant(pages, wiki, vault, today),
        "cluster_distribution": check_cluster_distribution(pages, wiki),
        "frontmatter_drift": check_frontmatter_drift(pages, wiki),
        "isolated_concepts": check_isolated_concepts(pages),
        "duplicate_titles": check_duplicate_titles(pages, wiki, vault),
    }


def total_issues(report: dict) -> int:
    dist = report["cluster_distribution"]
    return (
        len(report["to_compile_aging"])
        + len(report["to_verify_stagnant"])
        + len(report["frontmatter_drift"])
        + len(report["isolated_concepts"])
        + len(report["duplicate_titles"])
        + len(dist["anemic"])
        + len(dist["overloaded"])
    )


def _out(line: str = "") -> None:
    print(safe(line))


def print_report(vault: Path, r: dict) -> None:
    _out(f"# Bilan de santé — {vault}")
    _out(f"\n## Notes analysées (frontmatter valide) : {r['pages']}")

    _out(
        f"\n## 1. Notes `to-compile` vieillissantes (>{COMPILE_MAX_DAYS} j) : "
        f"{len(r['to_compile_aging'])}"
    )
    for item in r["to_compile_aging"][:10]:
        _out(f"  - {item['path']} (cluster={item['cluster']}, {item['days']} j)")

    _out(
        f"\n## 2. Notes `to-verify` stagnantes (>{VERIFY_MAX_DAYS} j) : "
        f"{len(r['to_verify_stagnant'])}"
    )
    for item in r["to_verify_stagnant"][:10]:
        _out(f"  - {item['path']} ({item['days']} j)")

    _out("\n## 3. Répartition des clusters")
    for c, n in sorted(r["cluster_distribution"]["distribution"].items(), key=lambda x: -x[1]):
        if n < CLUSTER_MIN:
            mark = " (anémique)"
        elif n > CLUSTER_MAX:
            mark = " (surchargé)"
        else:
            mark = ""
        _out(f"  - {c} : {n} notes{mark}")

    _out(f"\n## 4. Dérive du frontmatter (tags rares) : {len(r['frontmatter_drift'])} clusters")
    for item in r["frontmatter_drift"][:5]:
        rare = ", ".join(f"{t} ({n})" for t, n in item["rare_tags"][:3])
        _out(f"  - {item['cluster']} : {rare}")

    _out(
        "\n## 5. Concepts isolés (>= 3 liens communs, sans lien mutuel) : "
        f"{len(r['isolated_concepts'])}"
    )
    for item in r["isolated_concepts"][:10]:
        a, b = item["pages"]
        _out(f"  - [[{a}]] <-> [[{b}]] ({item['common_refs']} liens communs)")

    _out(f"\n## 6. Titres quasi identiques : {len(r['duplicate_titles'])}")
    for slug, paths in r["duplicate_titles"][:10]:
        _out(f"  - {slug} :")
        for p in paths:
            _out(f"      {p}")

    _out(f"\n---\nTotal des points relevés : {total_issues(r)}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="vault-health", description=__doc__.splitlines()[0])
    parser.add_argument("vault", nargs="?", help="chemin du coffre (défaut : $OBSIDIAN_VAULT)")
    parser.add_argument("--json", action="store_true", help="sortie JSON")
    parser.add_argument("--cluster", help="limite l'analyse à un cluster")
    parser.add_argument("--notes-dir", help="sous-dossier des notes (défaut : wiki)")
    parser.add_argument(
        "--raw-dir", default="raw", help="sous-dossier des notes brutes (défaut : raw)"
    )
    args = parser.parse_args(argv)

    vault = resolve_vault(args.vault)
    wiki = notes_root(vault, args.notes_dir)
    report = run(vault, wiki, vault / args.raw_dir, args.cluster, date.today())
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False, default=str))
    else:
        print_report(vault, report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
