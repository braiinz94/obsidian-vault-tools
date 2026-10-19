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

from ._common import iter_markdown, resolve_vault, split_frontmatter


def body_only(text: str) -> str:
    """Corps sans frontmatter, espaces normalisés pour une comparaison sémantique."""
    rest = split_frontmatter(text)[1].strip()
    # Plusieurs lignes vides consécutives comptent pour une seule
    return re.sub(r"\n{3,}", "\n\n", rest)


def hash_text(text: str) -> str:
    return hashlib.sha256(body_only(text).encode("utf-8")).hexdigest()


def hash_dir(root: Path) -> list[tuple[str, str]]:
    """Liste (chemin relatif, sha256) triée par chemin."""
    root = Path(root)
    return [
        (p.relative_to(root).as_posix(), hash_text(p.read_text(encoding="utf-8", errors="replace")))
        for p in iter_markdown(root)
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="vault-hash", description=__doc__.splitlines()[0])
    parser.add_argument("root", nargs="?", help="dossier à empreinter (défaut : $OBSIDIAN_VAULT)")
    args = parser.parse_args(argv)
    for rel, digest in hash_dir(resolve_vault(args.root)):
        print(f"{digest}  {rel}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
