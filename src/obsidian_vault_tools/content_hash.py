"""SHA-256 du corps Markdown hors frontmatter.

Sert à vérifier qu'une migration ou une normalisation n'a pas changé le contenu
d'un dossier de notes : on compare les empreintes avant et après.

Usage :
    vault-hash <dossier>
"""

from __future__ import annotations

import argparse
import hashlib
import re
from pathlib import Path

from ._common import BOM, iter_markdown, resolve_vault, safe, split_frontmatter, warn


def body_only(text: str) -> str:
    """Corps sans frontmatter, espaces normalisés pour une comparaison sémantique."""
    rest = split_frontmatter(text)[1].strip()
    # Plusieurs lignes vides consécutives comptent pour une seule
    return re.sub(r"\n{3,}", "\n\n", rest)


def hash_text(text: str) -> str:
    # surrogateescape : un octet non UTF-8 garde son identité au lieu de devenir « � »
    return hashlib.sha256(body_only(text).encode("utf-8", "surrogateescape")).hexdigest()


def hash_file(path: Path) -> str:
    text = path.read_bytes().decode("utf-8", "surrogateescape").removeprefix(BOM)
    # Fins de ligne normalisées : une conversion CRLF ↔ LF ne change pas le contenu
    return hash_text(text.replace("\r\n", "\n").replace("\r", "\n"))


def hash_dir(root: Path) -> list[tuple[str, str]]:
    """Liste (chemin relatif, sha256) triée par chemin."""
    root = Path(root)
    hashes = []
    for p in iter_markdown(root):
        try:
            hashes.append((p.relative_to(root).as_posix(), hash_file(p)))
        except OSError as exc:
            warn(f"{p} illisible ({exc.strerror})")
    return hashes


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="vault-hash", description=__doc__.splitlines()[0])
    parser.add_argument("root", nargs="?", help="dossier à empreinter (défaut : $OBSIDIAN_VAULT)")
    args = parser.parse_args(argv)
    for rel, digest in hash_dir(resolve_vault(args.root)):
        print(f"{digest}  {safe(rel)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
