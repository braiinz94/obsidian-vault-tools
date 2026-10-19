"""Répare les wikilinks cassés d'un coffre Obsidian.

Un lien est cassé quand Obsidian ne le résout pas : ni nom de note, ni chemin, ni pièce
jointe (sans tenir compte de la casse). Stratégies, dans l'ordre :
  1. Lien préfixé par un chemin faux ([[dossier/Note]]) dont le nom de note existe une
     seule fois dans le coffre → réécrit en [[Note]].
  2. Méta-note générique ([[_MOC]], [[Plan]], [[Audit]]…) → suffixée par le cluster de
     la note qui la cite ([[Plan-<cluster>]]) si cette note existe.
  3. Une seule note proche au-dessus du seuil (92 % par défaut) → appliquée.
  4. Le reste (aucun candidat, ou plusieurs ex aequo) part dans un fichier JSON à
     trancher à la main, puis réappliqué avec --apply.

Seul le corps des notes est réécrit, jamais le frontmatter ni le code. Chaque fichier est
remplacé de façon atomique, avec ses fins de ligne et son encodage d'origine.

Usage :
    vault-links [coffre] --auto [--dry-run] [--out ambigus.json]
    vault-links [coffre] --apply decisions.json
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

from rapidfuzz import fuzz, process

from ._common import (
    LinkIndex,
    VaultError,
    cluster_of,
    iter_links,
    iter_markdown,
    link_target,
    load_note,
    notes_root,
    parse_csv,
    read_text,
    resolve_vault,
    safe,
    save_note,
    split_frontmatter,
)

AUTO_THRESHOLD = 92.0
DEFAULT_META_NAMES = frozenset({"_MOC", "Audit", "Plan", "Debrief", "Schema", "Fact-Check-Log"})
# Une cible de remplacement est un nom de note : rien qui puisse fermer ou ouvrir un lien
_FORBIDDEN_IN_TARGET = re.compile(r"[\[\]|#^\r\n]")
Key = tuple[Path, str]


def try_resolve(
    target: str,
    referer: Path,
    root: Path,
    index: LinkIndex,
    meta_names: frozenset[str] = DEFAULT_META_NAMES,
) -> tuple[str | None, str]:
    """(nouvelle cible ou None, raison)."""
    bare = target.rsplit("/", 1)[-1]
    count = index.note_count(bare)
    if count == 1:
        return next(iter(index.notes_for(bare))), "nom-unique"
    if count > 1:
        return None, "nom-ambigu"

    if bare in meta_names:
        cluster = cluster_of(referer, root)
        if cluster:
            suffixed = f"{bare}-{cluster}"
            if index.note_count(suffixed) == 1:
                return next(iter(index.notes_for(suffixed))), f"meta-{cluster}"

    return None, "casse"


def collect_broken(files: list[Path], index: LinkIndex) -> dict[Key, int]:
    """(note, cible cassée) → nombre d'occurrences, dans le corps hors code."""
    broken: dict[Key, int] = {}
    for f in files:
        text = read_text(f)
        if text is None:
            continue
        for m in iter_links(split_frontmatter(text)[1]):
            target = link_target(m.group(1))
            if target and not index.exact(target):
                broken[(f, target)] = broken.get((f, target), 0) + 1
    return broken


def _rewrite(body: str, old: str, new: str) -> str:
    """Remplace la cible `old` par `new` dans les liens hors code, alias et titres conservés."""
    target_re = re.compile(r"^(\s*)" + re.escape(old) + r"(?=\s*(?:\\?\||#|$))", re.DOTALL)
    pieces, last = [], 0
    for m in iter_links(body):
        if link_target(m.group(1)) != old:
            continue
        inner = target_re.sub(lambda t: t.group(1) + new, m.group(1), count=1)
        pieces += [body[last : m.start(1)], inner]
        last = m.end(1)
    return "".join(pieces) + body[last:]


def apply_replacements(replacements: dict[Key, str]) -> int:
    """Réécrit les cibles dans le corps des notes. Renvoie le nombre de fichiers modifiés."""
    by_file: dict[Path, list[tuple[str, str]]] = {}
    for (path, old), new in replacements.items():
        by_file.setdefault(path, []).append((old, new))

    changed = 0
    for path, edits in by_file.items():
        note = load_note(path)
        if note is None:
            continue
        fm, body = split_frontmatter(note.text)
        new_body = body
        for old, new in edits:
            new_body = _rewrite(new_body, old, new)
        if new_body != body:
            save_note(note, fm + new_body)
            changed += 1
    return changed


def _fuzzy(target: str, names: list[str], threshold: float) -> tuple[str | None, list[Any]]:
    """Le seul candidat au-dessus du seuil, ou None s'il n'y en a aucun ou plusieurs."""
    candidates = process.extract(target, names, scorer=fuzz.ratio, limit=3)
    above = [c for c in candidates if c[1] >= threshold]
    return (above[0][0] if len(above) == 1 else None), candidates


def auto_mode(
    vault: Path,
    root: Path,
    out: Path,
    *,
    meta_names: frozenset[str] = DEFAULT_META_NAMES,
    threshold: float = AUTO_THRESHOLD,
    dry: bool = False,
) -> dict[str, int]:
    index = LinkIndex.build(vault)
    names = sorted({stem for stems in index.stems.values() for stem in stems})
    broken = collect_broken(iter_markdown(root), index)

    replacements: dict[Key, str] = {}
    ambiguous = []
    stats = {"auto": 0, "fuzzy": 0, "ambigus": 0, "fichiers": 0}
    for (ref, target), count in broken.items():
        new, _reason = try_resolve(target, ref, root, index, meta_names)
        if new:
            replacements[(ref, target)] = new
            stats["auto"] += count
            continue
        best, candidates = _fuzzy(target, names, threshold)
        if best and index.note_count(best) == 1:
            replacements[(ref, target)] = best
            stats["fuzzy"] += count
            continue
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
        stats["fichiers"] = apply_replacements(replacements)
    out.write_text(json.dumps(ambiguous, ensure_ascii=False, indent=2), encoding="utf-8")
    return stats


def _decision(vault: Path, item: object) -> tuple[Key, str] | None:
    """Valide une décision : note existante du coffre, cible et remplacement sains."""
    if not isinstance(item, dict):
        raise VaultError(f"décision illisible : {safe(item)!r}")
    referer, target, resolution = item.get("referer"), item.get("target"), item.get("resolution")
    if not resolution:
        return None
    if not (isinstance(referer, str) and isinstance(target, str) and isinstance(resolution, str)):
        raise VaultError(f"décision mal formée : {safe(item)!r}")
    path = (vault / referer).resolve()
    if not path.is_relative_to(vault) or path.suffix != ".md" or not path.is_file():
        raise VaultError(f"note hors du coffre ou introuvable : {safe(referer)}")
    if _FORBIDDEN_IN_TARGET.search(resolution) or not resolution.strip():
        raise VaultError(f"cible de remplacement invalide : {safe(resolution)!r}")
    return (path, target), resolution.strip()


def apply_mode(vault: Path, mapping_file: Path) -> int:
    """Applique un JSON [{"referer", "target", "resolution"}] ; resolution vide = ignorée.

    Toutes les décisions sont validées avant la première écriture.
    """
    try:
        data = json.loads(mapping_file.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise VaultError(f"fichier de décisions illisible : {exc}") from exc
    if not isinstance(data, list):
        raise VaultError("le fichier de décisions doit contenir une liste")
    decisions = [d for item in data if (d := _decision(vault, item)) is not None]
    return apply_replacements(dict(decisions)) if decisions else 0


def _threshold(value: str) -> float:
    number = float(value)
    if not 0 < number <= 100:
        raise argparse.ArgumentTypeError("le seuil doit être compris entre 0 et 100")
    return number


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
    parser.add_argument(
        "--threshold", type=_threshold, default=AUTO_THRESHOLD, help="seuil flou en %% (0-100]"
    )
    parser.add_argument("--meta-names", help="méta-notes à suffixer, séparées par des virgules")
    args = parser.parse_args(argv)

    vault = resolve_vault(args.vault)
    if args.apply:
        print(f"Fichiers modifiés : {apply_mode(vault, args.apply)}")
        return 0

    root = notes_root(vault, args.notes_dir)
    meta = parse_csv(args.meta_names) or DEFAULT_META_NAMES
    stats = auto_mode(
        vault, root, args.out, meta_names=meta, threshold=args.threshold, dry=args.dry_run
    )
    print(f"Résolus automatiquement : {stats['auto']}")
    print(f"Résolus par approximation (>= {args.threshold:g} %) : {stats['fuzzy']}")
    print(f"Ambigus (à trancher) : {stats['ambigus']} → {safe(args.out)}")
    if args.dry_run:
        print("Mode : SIMULATION (aucune note modifiée)")
    else:
        print(f"Fichiers modifiés : {stats['fichiers']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
