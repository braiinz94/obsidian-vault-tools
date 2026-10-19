from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SCRUB = Path(__file__).resolve().parent.parent / "scripts" / "scrub.sh"
SRC = Path(__file__).resolve().parent.parent / "src"

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="bash requis")


def scrub(vault_root: Path, **env: str) -> subprocess.CompletedProcess:
    full_env = {
        **os.environ,
        "PYTHON": sys.executable,
        "PYTHONPATH": str(SRC),
        **env,
    }
    full_env.pop("OBSIDIAN_VAULT", None)
    return subprocess.run(
        ["bash", str(SCRUB), str(vault_root)], capture_output=True, text=True, env=full_env
    )


def test_scrub_clean_vault(vault):
    vault.note("A.md", "[[B]]")
    vault.note("B.md", "[[A]]")
    r = scrub(vault.root)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "## Broken links: 0" in r.stdout
    assert "SCRUB_INDEX_CMD non défini" in r.stdout


def test_scrub_propagates_lint_failure(vault):
    vault.note("A.md", "[[Absente]]")
    r = scrub(vault.root)
    assert r.returncode == 1
    assert "## Broken links: 1" in r.stdout


def test_scrub_runs_index_command(vault):
    vault.note("A.md", "[[B]]")
    vault.note("B.md", "[[A]]")
    assert scrub(vault.root, SCRUB_INDEX_CMD="test -f wiki/A.md").returncode == 0
    assert scrub(vault.root, SCRUB_INDEX_CMD="exit 3").returncode == 1


def test_scrub_without_vault():
    r = scrub(Path("/chemin/inexistant"))
    assert r.returncode == 2
    assert "coffre introuvable" in r.stderr
