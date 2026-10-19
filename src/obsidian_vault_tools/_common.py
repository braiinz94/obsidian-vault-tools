"""Fonctions partagées : coffre, lecture et écriture sûres, frontmatter, wikilinks."""

from __future__ import annotations

import os
import re
import sys
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

VAULT_ENV = "OBSIDIAN_VAULT"
NOTES_DIR_ENV = "OBSIDIAN_NOTES_DIR"
DEFAULT_NOTES_DIR = "wiki"
BOM = "﻿"

# Wikilink tolérant aux crochets équilibrés : [[Page [v2]]], [[Page|alias]], [[Page#titre]]
WIKILINK = re.compile(r"\[\[((?:[^\[\]]|\[[^\]]*\])+?)(?=\]\])")
# Zones où Obsidian ne voit pas de liens : blocs de code, code en ligne, commentaires %%…%%
_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
_INLINE_CODE = re.compile(r"(`+)(?:(?!\1).)+?\1")
_COMMENT = re.compile(r"%%.*?%%", re.DOTALL)
# Caractères de contrôle (séquences d'échappement ANSI comprises) à neutraliser à l'affichage
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")


class VaultError(SystemExit):
    """Erreur de configuration : message sur stderr, code de sortie 1."""

    def __init__(self, message: str) -> None:
        super().__init__(f"erreur : {message}")


def warn(message: str) -> None:
    print(f"avertissement : {safe(message)}", file=sys.stderr)


def safe(value: object) -> str:
    """Texte affichable : les caractères de contrôle d'une note ne pilotent pas le terminal."""
    return _CONTROL.sub(lambda m: f"\\x{ord(m.group()):02x}", str(value))


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
    root = (vault / name).resolve() if name else vault
    if not root.is_relative_to(vault):
        raise VaultError(f"le dossier des notes doit être dans le coffre : {name}")
    return root if root.is_dir() else vault


def is_hidden(path: Path) -> bool:
    return ".obsidian" in path.parts or ".git" in path.parts or ".trash" in path.parts


def iter_files(root: Path, pattern: str = "*.md") -> Iterator[Path]:
    """Fichiers du dossier, triés, hors dossiers cachés et hors liens qui sortent du dossier."""
    base = root.resolve()
    for p in sorted(root.rglob(pattern)):
        if is_hidden(p.relative_to(root)) or not p.is_file():
            continue
        # Un lien symbolique qui pointe hors du dossier n'est ni lu ni réécrit
        if p.is_symlink() and not p.resolve().is_relative_to(base):
            continue
        yield p


def iter_markdown(root: Path) -> list[Path]:
    return list(iter_files(root))


def read_text(path: Path) -> str | None:
    """Lecture tolérante (outils en lecture seule) ; None, avec avertissement, si illisible."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        warn(f"{path} illisible ({exc.strerror})")
        return None
    return text.removeprefix(BOM)


@dataclass
class Note:
    """Note lue pour réécriture : contenu exact (fins de ligne, BOM) restitué à l'écriture."""

    path: Path
    text: str
    bom: bool = False


def load_note(path: Path) -> Note | None:
    """Lecture stricte avant écriture : un fichier non UTF-8 est ignoré plutôt qu'abîmé."""
    try:
        with path.open(encoding="utf-8", newline="") as fh:
            text = fh.read()
    except UnicodeDecodeError:
        warn(f"{path} n'est pas en UTF-8 : laissé intact")
        return None
    except OSError as exc:
        warn(f"{path} illisible ({exc.strerror}) : laissé intact")
        return None
    bom = text.startswith(BOM)
    return Note(path, text.removeprefix(BOM), bom)


def save_note(note: Note, text: str) -> None:
    """Écriture atomique : fichier temporaire dans le même dossier, puis remplacement."""
    target = note.path.resolve()
    fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=f".{target.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
            fh.write((BOM if note.bom else "") + text)
        os.chmod(tmp, target.stat().st_mode & 0o7777)
        os.replace(tmp, target)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def split_frontmatter(text: str) -> tuple[str, str]:
    """(bloc frontmatter avec ses délimiteurs, corps). Sans frontmatter : ("", texte).

    Le bloc s'ouvre sur une première ligne « --- » et se ferme sur la ligne « --- » suivante.
    """
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].rstrip() != "---":
        return "", text
    for i in range(1, len(lines)):
        if lines[i].rstrip() == "---":
            return "".join(lines[: i + 1]), "".join(lines[i + 1 :])
    return "", text


def yaml_of(block: str) -> str:
    """Texte YAML d'un bloc frontmatter, sans les lignes « --- »."""
    lines = block.splitlines(keepends=True)
    return "".join(lines[1:-1])


def body_of(text: str) -> str:
    return split_frontmatter(text)[1]


class _NoAliasLoader(yaml.SafeLoader):
    """SafeLoader sans ancres ni alias : ferme la porte aux « bombes » YAML."""

    def compose_node(self, parent: Any, index: Any) -> Any:
        if self.check_event(yaml.AliasEvent):
            raise yaml.YAMLError("alias YAML refusé dans le frontmatter")
        return super().compose_node(parent, index)


def load_yaml(raw: str) -> Any:
    return yaml.load(raw, Loader=_NoAliasLoader)  # noqa: S506 (chargeur dérivé de SafeLoader)


def parse_frontmatter(text: str) -> dict[str, Any] | str | None:
    """dict si valide, "invalid" si YAML illisible ou non clé/valeur, None si absent."""
    block, _ = split_frontmatter(text)
    if not block:
        return None
    try:
        data = load_yaml(yaml_of(block))
    except yaml.YAMLError:
        return "invalid"
    if data is None:
        return {}
    return data if isinstance(data, dict) else "invalid"


def _mask(text: str, pattern: re.Pattern[str], chars: list[str]) -> None:
    for m in pattern.finditer(text):
        for i in range(m.start(), m.end()):
            if chars[i] != "\n":
                chars[i] = " "


def mask_code(text: str) -> str:
    """Même longueur que le texte, avec code et commentaires remplacés par des espaces."""
    chars = list(text)
    pos, fence = 0, None
    for line in text.splitlines(keepends=True):
        m = _FENCE.match(line)
        if fence is None and m:
            fence = m.group(1)[0] * len(m.group(1))
            start = pos
        if fence is not None:
            for i in range(pos, pos + len(line)):
                if chars[i] != "\n":
                    chars[i] = " "
            if pos != start and line.strip().startswith(fence) and not line.strip(fence).strip():
                fence = None
        pos += len(line)
    masked = "".join(chars)
    chars = list(masked)
    _mask(masked, _INLINE_CODE, chars)
    _mask("".join(chars), _COMMENT, chars)
    return "".join(chars)


def link_target(raw: str) -> str:
    """Cible d'un wikilink brut : sans alias, sans titre ; « \\| » (tableau) compte comme « | »."""
    return raw.replace("\\|", "|").split("|")[0].split("#")[0].strip()


def iter_links(body: str) -> Iterator[re.Match[str]]:
    """Wikilinks du corps hors code et commentaires ; les positions valent pour `body`."""
    masked = mask_code(body)
    for m in WIKILINK.finditer(masked):
        yield WIKILINK.match(body, m.start()) or m


def extract_wikilinks(text: str) -> set[str]:
    """Cibles des wikilinks du CORPS (le frontmatter est de la métadonnée, pas de la navigation).

    Les liens vers un titre de la note elle-même ([[#Titre]]) sont ignorés.
    """
    targets = {link_target(m.group(1)) for m in iter_links(body_of(text))}
    targets.discard("")
    return targets


def _fold(name: str) -> str:
    name = name.casefold()
    return name.removesuffix(".md")


@dataclass
class LinkIndex:
    """Ce qu'un wikilink peut désigner dans le coffre, comme Obsidian : sans tenir compte
    de la casse, par nom de note, par chemin depuis la racine ou par nom de pièce jointe."""

    stems: dict[str, list[str]] = field(default_factory=dict)
    paths: set[str] = field(default_factory=set)
    attachments: set[str] = field(default_factory=set)

    @classmethod
    def build(cls, vault: Path) -> LinkIndex:
        index = cls()
        for p in iter_files(vault, "*"):
            rel = p.relative_to(vault).as_posix()
            if p.suffix.lower() == ".md":
                index.stems.setdefault(_fold(p.stem), []).append(p.stem)
                index.paths.add(_fold(rel))
            else:
                index.attachments.add(p.name.casefold())
                index.attachments.add(rel.casefold())
        return index

    def notes_for(self, target: str) -> set[str]:
        """Noms des notes désignées par la cible (vide si aucune)."""
        return set(self.stems.get(_fold(target.rsplit("/", 1)[-1]), ()))

    def note_count(self, target: str) -> int:
        """Nombre de fichiers qui portent ce nom de note (plusieurs = nom ambigu)."""
        return len(self.stems.get(_fold(target.rsplit("/", 1)[-1]), ()))

    def exact(self, target: str) -> bool:
        """La cible telle qu'écrite existe : nom seul, chemin complet ou pièce jointe."""
        t = target.strip()
        if "/" in t:
            return _fold(t) in self.paths or t.casefold() in self.attachments
        return _fold(t) in self.stems or t.casefold() in self.attachments

    def exists(self, target: str) -> bool:
        """Comme `exact`, et accepte un chemin faux dont le nom de note existe."""
        return self.exact(target) or bool(self.notes_for(target))


def cluster_of(path: Path, root: Path, cluster_roots: frozenset[str] | None = None) -> str | None:
    """Cluster d'une note : le sous-dossier de second niveau (<domaine>/<cluster>/...).

    Si cluster_roots est fourni, seuls ces domaines portent des clusters.
    """
    parts = path.relative_to(root).parts
    if len(parts) < 3:  # domaine, cluster, fichier
        return None
    if cluster_roots is not None and parts[0] not in cluster_roots:
        return None
    return parts[1]


def parse_csv(value: str | None) -> frozenset[str] | None:
    if value is None:
        return None
    return frozenset(v.strip() for v in value.split(",") if v.strip())
