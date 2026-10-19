from __future__ import annotations

import pytest

from obsidian_vault_tools import lint


def run(vault, capsys, *args: str) -> tuple[str, int]:
    code = lint.main([str(vault.root), *args])
    return capsys.readouterr().out, code


def linked_pair(vault) -> None:
    vault.note("Projets/alpha/_MOC-alpha.md", "[[Note-A]]")
    vault.note("Projets/alpha/pages/Note-A.md", "[[_MOC-alpha]]")


def test_clean_vault_passes(vault, capsys):
    linked_pair(vault)
    out, code = run(vault, capsys)
    assert "Broken links: 0" in out
    assert "Orphans: 0" in out
    assert "Frontmatter invalid: 0" in out
    assert code == 0


def test_broken_link_detected(vault, capsys):
    linked_pair(vault)
    vault.note("Projets/alpha/pages/Note-B.md", "[[Inexistante]] [[Note-A]]")
    out, code = run(vault, capsys)
    assert "Broken links: 1" in out
    assert "[[Inexistante]]" in out
    assert code == 1


def test_path_alias_heading_and_escaped_pipe_resolve(vault, capsys):
    linked_pair(vault)
    vault.note(
        "Projets/alpha/pages/Note-B.md",
        "[[Projets/alpha/pages/Note-A]] [[Note-A|alias]] [[Note-A#Titre]] | [[Note-A\\|x]] |",
    )
    out, _ = run(vault, capsys)
    assert "Broken links: 0" in out


def test_links_in_frontmatter_are_ignored(vault, capsys):
    linked_pair(vault)
    vault.note(
        "Projets/alpha/pages/Note-B.md",
        "[[Note-A]]",
        frontmatter="---\ndate: '2026-01-15'\nstatus: active\ntype: note\ntags: []\n"
        'source: "[[Absente]]"\n---\n\n',
    )
    out, _ = run(vault, capsys)
    assert "Broken links: 0" in out


def test_orphan_detected(vault, capsys):
    linked_pair(vault)
    vault.note("Seule.md", "aucun lien")
    out, code = run(vault, capsys)
    assert "Orphans: 1" in out
    assert "  - Seule" in out
    assert code == 1


def test_service_file_counts_as_incoming(vault, capsys):
    vault.note("Seule.md", "aucun lien")
    (vault.root / "index.md").write_text("[[Seule]]\n", encoding="utf-8")
    out, _ = run(vault, capsys)
    assert "Orphans: 0" in out
    assert "Index: 1 lignes" in out


def test_frontmatter_missing_and_incomplete(vault, capsys):
    linked_pair(vault)
    (vault.wiki / "Sans-fm.md").write_text("# Rien\n[[Note-A]]\n", encoding="utf-8")
    vault.note("Incomplete.md", "[[Note-A]]", frontmatter="---\ndate: '2026-01-15'\n---\n\n")
    vault.note("Cassee.md", "[[Note-A]]", frontmatter="---\ndate: [oups\n---\n\n")
    out, code = run(vault, capsys)
    assert "Frontmatter missing: 1" in out
    assert "Frontmatter invalid: 2" in out
    assert "manque : status,tags,type" in out
    assert code == 1


def test_contested_reference(vault, capsys):
    linked_pair(vault)
    vault.note("Douteuse.md", "[[Note-A]]", status="contested")
    vault.note("Active.md", "[[Douteuse]]")
    vault.note("Ancienne.md", "[[Douteuse]]", frontmatter=None, status="contested")
    out, code = run(vault, capsys)
    assert "Contested refs: 1" in out
    assert "[[Active]] → [[Douteuse]]" in out
    assert "Notes contestées suivies : 2" in out
    assert code == 1


def test_contested_with_french_field(vault, capsys):
    linked_pair(vault)
    vault.note(
        "Douteuse.md",
        "[[Note-A]]",
        frontmatter="---\ndate: '2026-01-15'\nstatus: active\nstatut: contested\ntype: note\n"
        "tags: []\n---\n\n",
    )
    vault.note("Active.md", "[[Douteuse]]")
    out, _ = run(vault, capsys)
    assert "Contested refs: 1" in out


def test_exclude_option_and_env(vault, capsys, monkeypatch):
    linked_pair(vault)
    (vault.wiki / "genere").mkdir()
    (vault.wiki / "genere" / "brut.md").write_text("# sans frontmatter\n", encoding="utf-8")
    out, _ = run(vault, capsys)
    assert "Frontmatter missing: 1" in out

    out, _ = run(vault, capsys, "--exclude", "genere")
    assert "Frontmatter missing: 0" in out

    monkeypatch.setenv(lint.EXCLUDE_ENV, "autre, genere")
    out, _ = run(vault, capsys)
    assert "Frontmatter missing: 0" in out


def test_excluded_folder_still_resolves_links(vault, capsys):
    linked_pair(vault)
    vault.note("genere/Cible.md", "")
    vault.note("Source.md", "[[Cible]] [[Note-A]]")
    out, _ = run(vault, capsys, "--exclude", "genere")
    assert "Broken links: 0" in out


@pytest.mark.parametrize(
    ("rel", "violation"),
    [
        ("Projets/Racine.md", "Note à la racine de Projets/"),
        ("Projets/alpha/Mal-rangee.md", "Note hors hub à la racine du cluster Projets/alpha/"),
    ],
)
def test_structure_violations(vault, capsys, rel, violation):
    linked_pair(vault)
    vault.note(rel, "[[Note-A]]")
    out, code = run(vault, capsys)
    assert "Structure violations: 0" in out  # contrôle désactivé par défaut

    out, code = run(vault, capsys, "--structured-root", "Projets")
    assert "Structure violations: 1" in out
    assert violation in out
    assert code == 1


def test_structure_accepts_hubs_and_index(vault, capsys, monkeypatch):
    linked_pair(vault)
    vault.note("Projets/_MOC-Projets.md", "[[_MOC-alpha]]")
    vault.note("Projets/alpha/_JOURNAL-alpha.md", "[[Note-A]]")
    vault.note("Projets/alpha/INDEX.md", "[[Note-A]]")
    monkeypatch.setenv(lint.STRUCTURED_ENV, "Projets")
    out, _ = run(vault, capsys)
    assert "Structure violations: 0" in out


def test_git_remote_check_is_opt_in(vault, capsys):
    linked_pair(vault)
    git = vault.root / ".git"
    git.mkdir()
    (git / "config").write_text('[remote "origin"]\n\turl = https://example.com/r.git\n')
    out, code = run(vault, capsys)
    assert "Security violations: 0" in out
    assert code == 0

    out, code = run(vault, capsys, "--forbid-git-remote")
    assert "Security violations: 1" in out
    assert code == 1


def test_informative_checks_do_not_fail(vault, capsys):
    linked_pair(vault)
    vault.note("Projets/alpha/pages/Hors-moc.md", "[[Note-A]]")
    out, code = run(vault, capsys)
    assert "Notes hors hub: 1" in out
    assert "JOURNAL non actualisé: 1" in out  # notes écrites aujourd'hui, pas de _JOURNAL
    assert code == 0


def test_no_wiki_dir_falls_back_to_root(tmp_path, capsys):
    root = tmp_path / "plat"
    root.mkdir()
    (root / "A.md").write_text("---\ndate: x\nstatus: a\ntype: n\ntags: []\n---\n[[B]]\n")
    (root / "B.md").write_text("---\ndate: x\nstatus: a\ntype: n\ntags: []\n---\n[[A]]\n")
    assert lint.main([str(root)]) == 0
    assert "Files: 2" in capsys.readouterr().out


def test_missing_vault_is_an_error(tmp_path, monkeypatch):
    monkeypatch.delenv("OBSIDIAN_VAULT", raising=False)
    with pytest.raises(SystemExit, match="chemin du coffre manquant"):
        lint.main([])
    with pytest.raises(SystemExit, match="coffre introuvable"):
        lint.main([str(tmp_path / "absent")])


def test_vault_from_env(vault, capsys, monkeypatch):
    linked_pair(vault)
    monkeypatch.setenv("OBSIDIAN_VAULT", str(vault.root))
    assert lint.main([]) == 0
