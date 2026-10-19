from __future__ import annotations

import yaml

from obsidian_vault_tools import content_hash, frontmatter_normalizer
from obsidian_vault_tools._common import split_frontmatter


def fm_of(path):
    head = split_frontmatter(path.read_text(encoding="utf-8"))[0]
    return yaml.safe_load(head.strip().strip("-"))


def test_normalizer_completes_without_overwriting(vault, capsys):
    page = vault.note(
        "Projets/alpha/pages/Note.md",
        "Corps intact",
        frontmatter="---\ntags: [garde]\nauteur: moi\n---\n",
    )
    assert frontmatter_normalizer.main([str(vault.root)]) == 0
    fm = fm_of(page)
    assert fm["tags"] == ["garde"]
    assert fm["auteur"] == "moi"
    assert fm["status"] == "active"
    assert fm["cluster"] == "alpha"
    assert fm["type"] == "note"
    assert list(fm)[:7] == frontmatter_normalizer.REQUIRED
    assert page.read_text(encoding="utf-8").endswith("Corps intact\n")
    assert "normalisée : 1" in capsys.readouterr().out


def test_normalizer_dry_run_and_invalid_yaml(vault, capsys):
    page = vault.note("Note.md", "x", frontmatter="")
    broken = vault.note("Cassee.md", "x", frontmatter="---\ndate: [oups\n---\n")
    before = page.read_bytes(), broken.read_bytes()
    frontmatter_normalizer.main([str(vault.root), "--dry-run"])
    assert (page.read_bytes(), broken.read_bytes()) == before
    out = capsys.readouterr().out
    assert "yaml invalide (ignorée) : 1" in out
    assert "SIMULATION" in out


def test_cluster_roots_and_types(vault):
    moc = vault.note("Projets/alpha/_MOC-alpha.md", frontmatter="")
    other = vault.note("Archives/beta/Plan-beta.md", frontmatter="")
    top = vault.note("Racine.md", frontmatter="")
    frontmatter_normalizer.main([str(vault.root), "--cluster-roots", "Projets"])
    assert fm_of(moc)["cluster"] == "alpha"
    assert fm_of(moc)["type"] == "moc"
    assert fm_of(other)["cluster"] is None
    assert fm_of(other)["type"] == "ressource"
    assert fm_of(top)["cluster"] is None


def test_already_ok_is_untouched(vault):
    page = vault.note(
        "Note.md",
        "x",
        frontmatter="---\ndate: '2026-01-01'\ntags: []\ntype: note\nstatus: active\n"
        "source: null\ncluster: null\nverified: null\n---\n",
    )
    before = page.read_bytes()
    frontmatter_normalizer.main([str(vault.root)])
    assert page.read_bytes() == before


def test_hash_ignores_frontmatter_and_blank_lines(tmp_path, capsys):
    a, b = tmp_path / "a", tmp_path / "b"
    (a / "sous").mkdir(parents=True)
    (b / "sous").mkdir(parents=True)
    (a / "sous" / "N.md").write_text("---\ndate: 1\n---\nTexte\n\n\n\nSuite\n")
    (b / "sous" / "N.md").write_text("---\ndate: 2\ntags: [x]\n---\n\nTexte\n\nSuite")
    assert content_hash.hash_dir(a) == content_hash.hash_dir(b)
    (b / "sous" / "N.md").write_text("Texte modifié")
    assert content_hash.hash_dir(a) != content_hash.hash_dir(b)

    assert content_hash.main([str(a)]) == 0
    line = capsys.readouterr().out.strip()
    digest, rel = line.split("  ")
    assert len(digest) == 64
    assert rel == "sous/N.md"


def test_hash_skips_obsidian_folder(tmp_path):
    (tmp_path / ".obsidian").mkdir()
    (tmp_path / ".obsidian" / "x.md").write_text("cache")
    (tmp_path / "N.md").write_text("note")
    assert [rel for rel, _ in content_hash.hash_dir(tmp_path)] == ["N.md"]
