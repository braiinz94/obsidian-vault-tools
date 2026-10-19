"""Répare les wikilinks cassés d'un coffre Obsidian.

Stratégies, dans l'ordre :
  1. Lien préfixé par un chemin ([[dossier/Note]]) dont le nom seul existe
     → réécrit en [[Note]].
  2. Méta-note générique ([[_MOC]], [[Plan]], [[Audit]]…) → suffixée par le
     cluster de la note qui la cite ([[Plan-<cluster>]]) si cette note existe.
  3. Correspondance approximative au-dessus du seuil (92 % par défaut)
     → appliquée automatiquement.
  4. Le reste est écrit dans un fichier JSON à trancher à la main, puis
     réappliqué avec --apply.

Seul le corps des notes est réécrit, jamais le frontmatter.

Usage :
    vault-links [coffre] --auto [--dry-run] [--out ambigus.json]
    vault-links [coffre] --apply decisions.json
"""

from __future__ import annotations

import argparse
import json
import re
from collections.abc import Callable
from pathlib import Path

from rapidfuzz import fuzz, process

from ._common import (
    WIKILINK,
    cluster_of,
    iter_markdown,
    notes_root,
    parse_csv,
    resolve_vault,
    split_frontmatter,
)

AUTO_THRESHOLD = 92.0
DEFAULT_META_NAMES = frozenset({"_MOC", "Audit", "Plan", "Debrief", "Schema", "Fact-Check-Log"})


def try_resolve(
    target: str,
    referer: Path,
    root: Path,
    names: set[str],
    meta_names: frozenset[str] = DEFAULT_META_NAMES,
) -> tuple[str | None, str]:
    """(nouvelle cible ou None, raison)."""
    orig = target.strip()
    bare = orig.rsplit("/", 1)[-1]

    if bare in names:
        return (bare, "nom-exact") if bare != orig else (None, "deja-ok")

    if bare in meta_names:
        cluster = cluster_of(referer, root)
        if cluster:
            suffixed = f"{bare}-{cluster}"
            if suffixed in names:
                return suffixed, f"meta-{cluster}"

    return None, "casse"


def collect_broken(files: list[Path], names: set[str]) -> dict[tuple[Path, str], int]:
    """(note, cible cassée) → nombre d'occurrences, corps seulement."""
    broken: dict[tuple[Path, str], int] = {}
    for f in files:
        body = split_frontmatter(f.read_text(encoding="utf-8", errors="replace"))[1]
        for m in WIKILINK.finditer(body):
            target = m.group(1).split("|")[0].split("#")[0].strip()
            if target in names:
                continue
            broken[(f, target)] = broken.get((f, target), 0) + 1
    return broken


def _replacer(new: str) -> Callable[[re.Match[str]], str]:
    def repl(m: re.Match[str]) -> str:
        return f"[[{new}{m.group(1)}"

    return repl


def apply_replacements(replacements: dict[tuple[Path, str], str]) -> int:
    """Réécrit les cibles dans le corps des notes. Renvoie le nombre de fichiers modifiés."""
    by_file: dict[Path, list[tuple[str, str]]] = {}
    for (path, old), new in replacements.items():
        by_file.setdefault(path, []).append((old, new))

    changed = 0
    for path, edits in by_file.items():
        fm, body = split_frontmatter(path.read_text(encoding="utf-8", errors="replace"))
        new_body = body
        for old, new in edits:
            pattern = re.compile(r"\[\[" + re.escape(old) + r"(\]|\||#)")
            # Remplacement par fonction : un « \ » dans la cible reste littéral
            new_body = pattern.sub(_replacer(new), new_body)
        if new_body != body:
            path.write_text(fm + new_body, encoding="utf-8")
            changed += 1
    return changed


def auto_mode(
    vault: Path,
    root: Path,
    out: Path,
    meta_names: frozenset[str] = DEFAULT_META_NAMES,
    threshold: float = AUTO_THRESHOLD,
    dry: bool = False,
) -> dict[str, int]:
    files = iter_markdown(root)
    names = {p.stem for p in files}
    broken = collect_broken(files, names)

    replacements: dict[tuple[Path, str], str] = {}
    ambiguous = []
    stats = {"auto": 0, "fuzzy": 0, "ambigus": 0}
    for (ref, target), count in broken.items():
        new, _reason = try_resolve(target, ref, root, names, meta_names)
        if new:
            replacements[(ref, target)] = new
            stats["auto"] += count
            continue
        candidates = process.extract(target, list(names), scorer=fuzz.ratio, limit=3)
        if candidates and candidates[0][1] >= threshold:
            replacements[(ref, target)] = candidates[0][0]
            stats["fuzzy"] += count
        else:
            ambiguous.append(
                {
                    "referer": ref.relative_to(vault).as_posix(),
                    "target": target,
                    "occurrences": count,
                    "candidates": [{"name": c[0], "score": round(c[1], 1)} for c in candidates],
                    "resolution": None,
                }
            )
    stats["ambigus"] = len(ambiguous)

    if not dry:
        apply_replacements(replacements)
    out.write_text(json.dumps(ambiguous, ensure_ascii=False, indent=2), encoding="utf-8")
    return stats


def apply_mode(vault: Path, mapping_file: Path) -> int:
    """Applique un JSON [{"referer", "target", "resolution"}] ; resolution vide = ignorée."""
    data = json.loads(mapping_file.read_text(encoding="utf-8"))
    replacements = {
        (vault / item["referer"], item["target"]): item["resolution"]
        for item in data
        if item.get("resolution")
    }
    apply_replacements(replacements)
    return len(replacements)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="vault-links", description=__doc__.splitlines()[0], allow_abbrev=False
    )
    parser.add_argument("vault", nargs="?", help="chemin du coffre (défaut : $OBSIDIAN_VAULT)")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--auto", action="store_true", help="résout ce qui peut l'être")
    mode.add_argument("--apply", type=Path, help="applique un fichier de décisions JSON")
    parser.add_argument("--dry-run", action="store_true", help="avec --auto : n'écrit aucune note")
    parser.add_argument("--notes-dir", help="sous-dossier des notes (défaut : wiki)")
    parser.add_argument(
        "--out", type=Path, default=Path("ambiguous-links.json"), help="fichier des cas ambigus"
    )
    parser.add_argument("--threshold", type=float, default=AUTO_THRESHOLD, help="seuil flou (%%)")
    parser.add_argument("--meta-names", help="méta-notes à suffixer, séparées par des virgules")
    args = parser.parse_args(argv)

    vault = resolve_vault(args.vault)
    if args.apply:
        print(f"Décisions appliquées : {apply_mode(vault, args.apply)}")
        return 0

    root = notes_root(vault, args.notes_dir)
    meta = parse_csv(args.meta_names) or DEFAULT_META_NAMES
    stats = auto_mode(vault, root, args.out, meta, args.threshold, dry=args.dry_run)
    print(f"Résolus automatiquement : {stats['auto']}")
    print(f"Résolus par approximation (>= {args.threshold:g} %) : {stats['fuzzy']}")
    print(f"Ambigus (à trancher) : {stats['ambigus']} → {args.out}")
    if args.dry_run:
        print("Mode : SIMULATION (aucune note modifiée)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
