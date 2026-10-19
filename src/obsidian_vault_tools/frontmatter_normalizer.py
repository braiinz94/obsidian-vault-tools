"""Ajoute les champs de frontmatter YAML manquants sur toutes les notes.

Ne remplace jamais une valeur existante : les champs manquants sont ajoutés en fin
de bloc, le YAML existant (ordre, guillemets, commentaires) reste tel quel. Les notes
au YAML illisible, non UTF-8 ou impossibles à compléter proprement sont laissées
intactes et comptées à part. Écriture atomique, fins de ligne et BOM conservés.

Usage :
    vault-frontmatter [coffre] [--dry-run] [--notes-dir wiki] [--cluster-roots A,B]
"""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from ._common import (
    cluster_of,
    iter_markdown,
    load_note,
    load_yaml,
    notes_root,
    parse_csv,
    resolve_vault,
    save_note,
    split_frontmatter,
    yaml_of,
)

REQUIRED = ["date", "tags", "type", "status", "source", "cluster", "verified"]
RESOURCE_HINTS = ("audit", "plan", "debrief", "fact-check", "schema", "instruction")


def infer_type(path: Path) -> str:
    name = path.stem.lower()
    if name.startswith("_moc"):
        return "moc"
    if any(k in name for k in RESOURCE_HINTS):
        return "ressource"
    if "Daily" in path.parts:
        return "daily"
    return "note"


def infer_date(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d")


def _dump(fields: dict[str, Any], newline: str) -> str:
    text = yaml.safe_dump(fields, allow_unicode=True, sort_keys=False, default_flow_style=False)
    return text.replace("\n", newline)


def complete(text: str, missing: dict[str, Any], data: dict[str, Any], block: str) -> str | None:
    """Texte complété ; le YAML existant est conservé à l'octet près (commentaires compris).

    None si l'ajout en fin de bloc ne relit pas exactement le résultat attendu
    (frontmatter en style « {…} » par exemple) : la note est alors laissée intacte.
    """
    newline = "\r\n" if "\r\n" in (block or text) else "\n"
    addition = _dump(missing, newline)
    if not block:
        return f"---{newline}{addition}---{newline}{newline}{text}"
    lines = block.splitlines(keepends=True)
    raw = yaml_of(block)
    if raw and not raw.endswith("\n"):
        raw += newline
    new_yaml = raw + addition
    try:
        if load_yaml(new_yaml) != {**data, **missing}:
            return None
    except yaml.YAMLError:
        return None
    body = text[len(block) :]
    return lines[0] + new_yaml + lines[-1] + body


def normalize(
    path: Path, root: Path, cluster_roots: frozenset[str] | None = None, dry: bool = False
) -> str:
    note = load_note(path)
    if note is None:
        return "illisible (ignorée)"
    block, _ = split_frontmatter(note.text)
    data: Any = {}
    if block:
        try:
            data = load_yaml(yaml_of(block))
        except yaml.YAMLError:
            return "yaml invalide (ignorée)"
        if data is None:
            data = {}
        if not isinstance(data, dict):
            return "yaml non clé/valeur (ignorée)"

    defaults: dict[str, Any] = {
        "date": infer_date(path),
        "tags": [],
        "type": infer_type(path),
        "status": "active",
        "source": None,
        "cluster": cluster_of(path, root, cluster_roots),
        "verified": None,
    }
    missing = {k: v for k, v in defaults.items() if k not in data}
    if not missing:
        return "déjà conforme"
    new_text = complete(note.text, missing, data, block)
    if new_text is None:
        return "frontmatter non complétable (ignorée)"
    if not dry:
        save_note(note, new_text)
    return "normalisée"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="vault-frontmatter", description=__doc__.splitlines()[0])
    parser.add_argument("vault", nargs="?", help="chemin du coffre (défaut : $OBSIDIAN_VAULT)")
    parser.add_argument("--dry-run", action="store_true", help="n'écrit rien, compte seulement")
    parser.add_argument("--notes-dir", help="sous-dossier des notes (défaut : wiki)")
    parser.add_argument(
        "--cluster-roots",
        help="domaines qui portent des clusters, séparés par des virgules (défaut : tous)",
    )
    args = parser.parse_args(argv)

    vault = resolve_vault(args.vault)
    root = notes_root(vault, args.notes_dir)
    roots = parse_csv(args.cluster_roots)
    files = iter_markdown(root)
    counts: dict[str, int] = {}
    for f in files:
        status = normalize(f, root, roots, dry=args.dry_run)
        counts[status] = counts.get(status, 0) + 1
    print(f"Total : {len(files)}")
    for k, v in counts.items():
        print(f"  {k} : {v}")
    print(f"Mode : {'SIMULATION' if args.dry_run else 'APPLIQUÉ'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
