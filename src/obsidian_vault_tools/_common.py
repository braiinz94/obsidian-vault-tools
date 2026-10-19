"""Fonctions partagées : localisation du coffre, frontmatter, wikilinks."""

from __future__ import annotations

import os
import re
from pathlib import Path

import yaml

VAULT_ENV = "OBSIDIAN_VAULT"
NOTES_DIR_ENV = "OBSIDIAN_NOTES_DIR"
DEFAULT_NOTES_DIR = "wiki"

# Wikilink tolérant aux crochets équilibrés : [[Page [v2]]], [[Page|alias]], [[Page#titre]]
WIKILINK = re.compile(r"\[\[((?:[^\[\]]|\[[^\]]*\])+?)(?=\]\])")


class VaultError(SystemExit):
    """Erreur de configuration : message sur stderr, code de sortie 1."""

    def __init__(self, message: str) -> None:
        super().__init__(f"erreur : {message}")


def resolve_vault(arg: str | None) -> Path:
    """Chemin du coffre : argument, sinon variable OBSIDIAN_VAULT, sinon erreur."""
    raw = arg or os.environ.get(VAULT_ENV)
    if not raw:
        raise VaultError(f"chemin du coffre manquant (argument ou variable {VAULT_ENV})")
    vault = Path(raw).expanduser().resolve()
    if not vault.is_dir():
        raise VaultError(f"coffre introuvable : {vault}")
    return vault


def notes_root(vault: Path, notes_dir: str | None = None) -> Path:
    """Dossier des notes (par défaut <coffre>/wiki, sinon la racine du coffre)."""
    name = notes_dir if notes_dir is not None else os.environ.get(NOTES_DIR_ENV, DEFAULT_NOTES_DIR)
    root = vault / name if name else vault
    return root if root.is_dir() else vault


def is_hidden(path: Path) -> bool:
    return ".obsidian" in path.parts or ".git" in path.parts


def iter_markdown(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*.md") if not is_hidden(p))


def split_frontmatter(text: str) -> tuple[str, str]:
    """Sépare (bloc frontmatter avec ses délimiteurs, corps). Sans frontmatter : ("", texte)."""
    if text.startswith("---\n") or text.startswith("---\r\n"):
        end = text.find("\n---", 4)
        if end != -1:
            fm_end = end + 4
            if text.startswith("\r\n", fm_end):
                fm_end += 2
            elif text.startswith("\n", fm_end):
                fm_end += 1
            return text[:fm_end], text[fm_end:]
    return "", text


def body_of(text: str) -> str:
    return split_frontmatter(text)[1]


def parse_frontmatter(text: str) -> dict | str | None:
    """dict si valide, "invalid" si YAML illisible, None si absent."""
    if not text.startswith("---"):
        return None
    end = text.find("\n---", 4)
    if end == -1:
        return None
    try:
        data = yaml.safe_load(text[4:end])
    except yaml.YAMLError:
        return "invalid"
    return data if isinstance(data, dict) else "invalid"


def extract_wikilinks(text: str) -> set[str]:
    """Cibles des wikilinks du CORPS (le frontmatter est de la métadonnée, pas de la navigation)."""
    targets = set()
    for raw in WIKILINK.findall(body_of(text)):
        # Dans un tableau Markdown, l'alias s'écrit \| : on le déséchappe avant de couper
        raw = raw.replace("\\|", "|")
        targets.add(raw.split("|")[0].split("#")[0].strip())
    return targets


def cluster_of(path: Path, root: Path, cluster_roots: frozenset[str] | None = None) -> str | None:
    """Cluster d'une note : le sous-dossier de second niveau (<domaine>/<cluster>/...).

    Si cluster_roots est fourni, seuls ces domaines portent des clusters.
    """
    parts = path.relative_to(root).parts
    if len(parts) < 3:
        return None
    if cluster_roots is not None and parts[0] not in cluster_roots:
        return None
    return parts[1]


def parse_csv(value: str | None) -> frozenset[str] | None:
    if value is None:
        return None
    return frozenset(v.strip() for v in value.split(",") if v.strip())
