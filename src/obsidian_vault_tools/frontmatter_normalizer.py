"""Ajoute les champs de frontmatter YAML manquants sur toutes les notes.

Ne remplace jamais une valeur existante : il complète seulement. Les notes dont
le YAML est illisible sont laissées telles quelles et comptées à part.

Usage :
    vault-frontmatter [coffre] [--dry-run] [--notes-dir wiki] [--cluster-roots A,B]
"""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

import yaml

from ._common import cluster_of, iter_markdown, notes_root, parse_csv, resolve_vault

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


def split_fm(text: str) -> tuple[dict | None, str]:
    """(frontmatter, corps). frontmatter vaut None si le YAML est illisible."""
    if text.startswith("---\n") or text.startswith("---\r\n"):
        end = text.find("\n---", 4)
        if end != -1:
            try:
                fm = yaml.safe_load(text[4:end])
            except yaml.YAMLError:
                return None, text
            rest = text[end + 4 :]
            if rest.startswith("\r\n"):
                rest = rest[2:]
            elif rest.startswith("\n"):
                rest = rest[1:]
            return (fm if isinstance(fm, dict) else {}), rest
    return {}, text


def serialize_fm(fm: dict) -> str:
    ordered = {k: fm[k] for k in REQUIRED if k in fm}
    ordered.update({k: v for k, v in fm.items() if k not in ordered})
    body = yaml.safe_dump(
        ordered, allow_unicode=True, sort_keys=False, default_flow_style=False
    ).strip()
    return f"---\n{body}\n---\n\n"


def normalize(
    path: Path, root: Path, cluster_roots: frozenset[str] | None = None, dry: bool = False
) -> str:
    text = path.read_text(encoding="utf-8", errors="replace")
    fm, body = split_fm(text)
    if fm is None:
        return "yaml invalide (ignorée)"

    defaults = {
        "date": infer_date(path),
        "tags": [],
        "type": infer_type(path),
        "status": "active",
        "source": None,
        "cluster": cluster_of(path, root, cluster_roots),
        "verified": None,
    }
    missing = {k: v for k, v in defaults.items() if k not in fm}
    if not missing:
        return "déjà conforme"
    fm.update(missing)
    if not dry:
        path.write_text(serialize_fm(fm) + body, encoding="utf-8")
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
