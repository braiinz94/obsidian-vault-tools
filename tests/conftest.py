"""Faux coffre Obsidian construit dans tmp_path pour chaque test."""

from __future__ import annotations

from pathlib import Path

import pytest

FM = """---
date: '2026-01-15'
status: {status}
type: note
tags: [{tags}]
cluster: {cluster}
---

"""


class Vault:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.wiki = root / "wiki"
        self.wiki.mkdir(parents=True)

    def note(
        self,
        rel: str,
        body: str = "",
        *,
        status: str = "active",
        tags: str = "test",
        cluster: str = "alpha",
        frontmatter: str | None = None,
        base: Path | None = None,
    ) -> Path:
        path = (base or self.wiki) / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        head = (
            frontmatter
            if frontmatter is not None
            else FM.format(status=status, tags=tags, cluster=cluster)
        )
        path.write_text(head + body + "\n", encoding="utf-8")
        return path


@pytest.fixture
def vault(tmp_path: Path) -> Vault:
    return Vault(tmp_path / "vault")
