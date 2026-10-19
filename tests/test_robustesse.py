"""Non-régression : un test par défaut trouvé en relecture (fiabilité et sécurité)."""

from __future__ import annotations

import json
import os
import stat

import pytest
import yaml

from obsidian_vault_tools import (
    content_hash,
    frontmatter_normalizer,
    health_check,
    link_resolver,
    lint,
    me_audit,
)
from obsidian_vault_tools._common import (
    LinkIndex,
    extract_wikilinks,
    mask_code,
    parse_frontmatter,
    safe,
    split_frontmatter,
)

FM = "---\ndate: x\nstatus: a\ntype: n\ntags: []\n---\n"
BOMB = (
    "---\n"
    "a: &a [x, x, x, x, x, x, x, x, x]\n"
    "b: &b [*a, *a, *a, *a, *a, *a, *a, *a, *a]\n"
    "tags: [*b, *b, *b]\n"
    "---\n"
)


# --- Fichiers inattendus -------------------------------------------------------------


def test_directory_named_md_does_not_crash(vault, capsys, tmp_path):
    (vault.wiki / "dossier.md").mkdir()
    vault.note("A.md", "[[B]]")
    vault.note("B.md", "[[A]]")
    assert lint.main([str(vault.root)]) == 0
    assert health_check.main([str(vault.root)]) == 0
    assert frontmatter_normalizer.main([str(vault.root), "--dry-run"]) == 0
    assert link_resolver.main([str(vault.root), "--auto", "--out", str(tmp_path / "a.json")]) == 0
    assert content_hash.main([str(vault.wiki)]) == 0


def test_symlink_outside_vault_is_never_written(vault, tmp_path):
    outside = tmp_path / "dehors"
    outside.mkdir()
    secret = outside / "x.md"
    secret.write_text("secret [[Cassé]]\n")
    (vault.wiki / "lien.md").symlink_to(secret)
    (vault.wiki / "dossier").symlink_to(outside, target_is_directory=True)
    vault.note("Cible.md")

    frontmatter_normalizer.main([str(vault.root)])
    link_resolver.main([str(vault.root), "--auto", "--out", str(tmp_path / "a.json")])
    assert secret.read_text() == "secret [[Cassé]]\n"


def test_symlink_inside_vault_is_followed(vault):
    target = vault.note("Vraie.md", "x", frontmatter="")
    (vault.wiki / "Alias.md").symlink_to(target)
    assert [p.name for p in lint.md_files(vault.wiki)] == ["Alias.md", "Vraie.md"]


@pytest.mark.skipif(os.geteuid() == 0, reason="root lit tout")
def test_unreadable_note_is_reported_not_fatal(vault, capsys):
    vault.note("A.md", "[[B]]")
    vault.note("B.md", "[[A]]")
    locked = vault.note("Verrou.md", "x")
    locked.chmod(0)
    try:
        assert lint.main([str(vault.root)]) == 0
        assert frontmatter_normalizer.main([str(vault.root)]) == 0
    finally:
        locked.chmod(stat.S_IRUSR | stat.S_IWUSR)
    assert "Verrou.md illisible" in capsys.readouterr().err


# --- Écriture fidèle ------------------------------------------------------------------


def test_bom_note_keeps_single_frontmatter(vault):
    page = vault.wiki / "Bom.md"
    page.write_bytes(b"\xef\xbb\xbf---\ntags: [a]\n---\nCorps\n")
    frontmatter_normalizer.main([str(vault.root)])
    raw = page.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf---\ntags: [a]\n")
    assert raw.count(b"---\n") == 2
    assert lint.parse_frontmatter(raw.decode("utf-8-sig"))["tags"] == ["a"]


def test_crlf_and_comments_are_preserved(vault):
    page = vault.wiki / "Crlf.md"
    page.write_bytes(b"---\r\ntags: [a]  # garder ce commentaire\r\n---\r\nL1\r\nL2\r\n")
    frontmatter_normalizer.main([str(vault.root)])
    raw = page.read_bytes()
    assert raw.startswith(b"---\r\ntags: [a]  # garder ce commentaire\r\n")
    assert b"\n" not in raw.replace(b"\r\n", b"")
    assert raw.endswith(b"---\r\nL1\r\nL2\r\n")


def test_non_utf8_note_is_left_intact(vault, capsys):
    page = vault.wiki / "Latin1.md"
    page.write_bytes(b"caf\xe9 [[Cible-x]]\n")
    vault.note("Cible-y.md")
    before = page.read_bytes()
    frontmatter_normalizer.main([str(vault.root)])
    link_resolver.main([str(vault.root), "--auto", "--out", os.devnull])
    assert page.read_bytes() == before
    assert "n'est pas en UTF-8" in capsys.readouterr().err


def test_flow_style_frontmatter_is_not_mangled(vault, capsys):
    page = vault.note("Flow.md", "x", frontmatter="---\n{tags: [a]}\n---\n")
    before = page.read_bytes()
    frontmatter_normalizer.main([str(vault.root)])
    assert page.read_bytes() == before
    assert "non complétable" in capsys.readouterr().out


def test_list_frontmatter_is_not_overwritten(vault):
    page = vault.note("Liste.md", "x", frontmatter="---\n- a\n- b\n---\n")
    before = page.read_bytes()
    frontmatter_normalizer.main([str(vault.root)])
    assert page.read_bytes() == before


def test_empty_frontmatter_is_completed_in_place(vault):
    page = vault.note("Vide.md", "Corps", frontmatter="---\n---\n")
    frontmatter_normalizer.main([str(vault.root)])
    text = page.read_text()
    assert text.count("---\n") == 2
    assert parse_frontmatter(text)["status"] == "active"
    assert text.endswith("---\nCorps\n")


def test_write_is_atomic_and_keeps_permissions(vault):
    page = vault.note("Perm.md", "x", frontmatter="")
    page.chmod(0o640)
    frontmatter_normalizer.main([str(vault.root)])
    assert stat.S_IMODE(page.stat().st_mode) == 0o640
    assert not [p for p in vault.wiki.iterdir() if p.suffix == ".tmp"]


# --- YAML hostile ---------------------------------------------------------------------


def test_yaml_aliases_are_refused(vault, capsys):
    vault.note("Bombe.md", "[[A]]", frontmatter=BOMB)
    vault.note("A.md", "[[Bombe]]")
    page = vault.wiki / "Bombe.md"
    before = page.read_bytes()
    assert parse_frontmatter(BOMB) == "invalid"
    frontmatter_normalizer.main([str(vault.root)])
    assert page.read_bytes() == before
    lint.main([str(vault.root)])
    assert "wiki/Bombe.md" in capsys.readouterr().out


def test_python_tags_are_not_executed(vault):
    evil = "---\ntags: !!python/object/apply:os.system ['echo pwned']\n---\n"
    assert parse_frontmatter(evil) == "invalid"
    with pytest.raises(yaml.YAMLError):
        yaml.safe_load(evil.strip("-\n"))


# --- Résolution des liens comme Obsidian ----------------------------------------------


def test_links_resolved_like_obsidian(vault, capsys):
    (vault.root / "assets").mkdir()
    (vault.root / "assets" / "schema.png").write_bytes(b"png")
    (vault.root / "raw").mkdir()
    (vault.root / "raw" / "Brouillon.md").write_text("x")
    vault.note("Cible.md", "[[Source]]")
    vault.note(
        "Source.md",
        "[[cible]] [[Cible.md]] [[#Titre local]] ![[schema.png]] [[assets/schema.png]] "
        "[[Brouillon]] [[raw/Brouillon]]",
    )
    lint.main([str(vault.root)])
    assert "Broken links: 0" in capsys.readouterr().out


def test_links_in_code_and_comments_are_ignored(vault, capsys):
    vault.note("A.md", "[[B]]")
    vault.note(
        "B.md",
        "[[A]]\n`[[inline]]`\n```\n[[bloc]]\n```\n~~~md\n[[tilde]]\n~~~\n%% [[commentaire]] %%\n",
    )
    lint.main([str(vault.root)])
    assert "Broken links: 0" in capsys.readouterr().out


def test_mask_code_keeps_offsets():
    text = "a `[[x]]` b\n```\n[[y]]\n```\n[[z]]"
    masked = mask_code(text)
    assert len(masked) == len(text)
    assert masked.count("\n") == text.count("\n")
    assert extract_wikilinks(text) == {"z"}


def test_unclosed_fence_masks_to_end():
    assert extract_wikilinks("[[a]]\n```\n[[b]]\n") == {"a"}


def test_link_index_is_case_insensitive(vault):
    vault.note("Projets/Note Finale.md")
    index = LinkIndex.build(vault.root)
    assert index.exact("note finale")
    assert index.exact("wiki/projets/note finale.md")
    assert not index.exact("ailleurs/note finale")
    assert index.exists("ailleurs/note finale")
    assert index.notes_for("NOTE FINALE") == {"Note Finale"}


# --- Réparation des liens -------------------------------------------------------------


def test_resolver_keeps_table_escaping_and_alias(vault, tmp_path):
    vault.note("Architecture-generale.md")
    src = vault.note("Source.md", "| a | [[Architecture-generalle\\|vue]] |")
    link_resolver.main([str(vault.root), "--auto", "--out", str(tmp_path / "a.json")])
    assert "| a | [[Architecture-generale\\|vue]] |" in src.read_text()


def test_resolver_does_not_touch_code_or_valid_paths(vault, tmp_path):
    vault.note("Projets/Cible.md")
    body = "[[wiki/Projets/Cible]]\n```\n[[ailleurs/Cible]]\n```\n`[[ailleurs/Cible]]`"
    src = vault.note("Source.md", body)
    link_resolver.main([str(vault.root), "--auto", "--out", str(tmp_path / "a.json")])
    assert src.read_text().endswith(body + "\n")


def test_resolver_keeps_ambiguous_names_for_review(vault, tmp_path):
    vault.note("a/Doublon.md")
    vault.note("b/Doublon.md")
    vault.note("Note-2025.md")
    vault.note("Note-2026.md")
    src = vault.note("Source.md", "[[c/Doublon]] [[Note-2027]]")
    out = tmp_path / "a.json"
    link_resolver.main([str(vault.root), "--auto", "--threshold", "80", "--out", str(out)])
    assert "[[c/Doublon]] [[Note-2027]]" in src.read_text()
    assert {a["target"] for a in json.loads(out.read_text())} == {"c/Doublon", "Note-2027"}


def test_resolver_only_rewrites_exact_target(vault, tmp_path):
    vault.note("Cible.md")
    src = vault.note("Source.md", "[[x/Cible]] [[x/Cible-bis]] [[x/Cible#t|alias x/Cible]]")
    link_resolver.apply_replacements({(src, "x/Cible"): "Cible"})
    assert "[[Cible]] [[x/Cible-bis]] [[Cible#t|alias x/Cible]]" in src.read_text()


@pytest.mark.parametrize(
    "decision",
    [
        {"referer": "../dehors.md", "target": "a", "resolution": "b"},
        {"referer": "wiki/Absente.md", "target": "a", "resolution": "b"},
        {"referer": "wiki/Source.md", "target": "a", "resolution": "b]] [[pirate"},
        {"referer": "wiki/Source.md", "target": "a", "resolution": "b\nc"},
        {"referer": ["wiki/Source.md"], "target": "a", "resolution": "b"},
        "texte",
    ],
)
def test_apply_rejects_unsafe_decisions(vault, tmp_path, decision):
    outside = vault.root.parent / "dehors.md"
    outside.write_text("[[a]]")
    src = vault.note("Source.md", "[[a]]")
    decisions = tmp_path / "d.json"
    decisions.write_text(
        json.dumps([{"referer": "wiki/Source.md", "target": "a", "resolution": "ok"}, decision])
    )
    with pytest.raises(SystemExit):
        link_resolver.main([str(vault.root), "--apply", str(decisions)])
    # Validation complète avant la première écriture : rien n'a bougé
    assert src.read_text().endswith("[[a]]\n")
    assert outside.read_text() == "[[a]]"


def test_apply_rejects_non_list_and_bad_json(vault, tmp_path):
    for content in ("{}", "pas du json"):
        decisions = tmp_path / "d.json"
        decisions.write_text(content)
        with pytest.raises(SystemExit):
            link_resolver.main([str(vault.root), "--apply", str(decisions)])


def test_threshold_must_be_a_percentage(vault):
    with pytest.raises(SystemExit):
        link_resolver.main([str(vault.root), "--auto", "--threshold", "150"])


# --- Lint ------------------------------------------------------------------------------


def test_git_remote_detected_in_worktree_and_spacing(vault, tmp_path, capsys):
    vault.note("A.md", "[[B]]")
    vault.note("B.md", "[[A]]")
    real = tmp_path / "depot.git"
    (real / "worktrees" / "w").mkdir(parents=True)
    (real / "config").write_text('  [remote "origin"]\n\turl = x\n')
    (real / "worktrees" / "w" / "commondir").write_text("../..\n")
    (vault.root / ".git").write_text(f"gitdir: {real / 'worktrees' / 'w'}\n")
    assert lint.main([str(vault.root), "--forbid-git-remote"]) == 1
    assert "Security violations: 1" in capsys.readouterr().out


def test_notes_dir_cannot_escape_vault(vault):
    with pytest.raises(SystemExit, match="dans le coffre"):
        lint.main([str(vault.root), "--notes-dir", "../.."])


def test_terminal_escape_sequences_are_neutralised(vault, capsys):
    vault.note("A.md", "[[piège\x1bc\x07]] [[B]]")
    vault.note("B.md", "[[A]]")
    lint.main([str(vault.root)])
    out = capsys.readouterr().out
    assert "\x1b" not in out
    assert "piège\\x1bc\\x07" in out


def test_safe_keeps_newlines_and_tabs():
    assert safe("a\tb\nc\x07") == "a\tb\nc\\x07"


def test_frontmatter_closing_must_be_a_full_line():
    block, body = split_frontmatter("---\na: 1\n----\nb: 2\n---\ncorps")
    assert block == "---\na: 1\n----\nb: 2\n---\n"
    assert body == "corps"


# --- Divers -----------------------------------------------------------------------------


def test_hash_ignores_line_endings_and_bom_but_not_bytes(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    (tmp_path / "a" / "N.md").write_bytes(b"Texte\nSuite\n")
    (tmp_path / "b" / "N.md").write_bytes(b"\xef\xbb\xbfTexte\r\nSuite\r\n")
    assert content_hash.hash_dir(tmp_path / "a") == content_hash.hash_dir(tmp_path / "b")
    (tmp_path / "a" / "N.md").write_bytes(b"caf\xe9")
    (tmp_path / "b" / "N.md").write_bytes(b"caf\xe8")
    assert content_hash.hash_dir(tmp_path / "a") != content_hash.hash_dir(tmp_path / "b")


@pytest.mark.parametrize(
    "config",
    ['{"inconnue": "x"}', '{"log": "../../etc/passwd"}', '{"log": 3}', "[]", "pas du json"],
)
def test_me_audit_rejects_bad_config(vault, tmp_path, config):
    cfg = tmp_path / "c.json"
    cfg.write_text(config)
    with pytest.raises(SystemExit):
        me_audit.main([str(vault.root), "--config", str(cfg)])


def test_failed_write_leaves_original_and_no_temp(vault, monkeypatch):
    page = vault.note("A.md", "x", frontmatter="")
    before = page.read_bytes()

    def boom(*_args):
        raise OSError("disque plein")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError, match="disque plein"):
        frontmatter_normalizer.main([str(vault.root)])
    assert page.read_bytes() == before
    assert [p.name for p in vault.wiki.iterdir()] == ["A.md"]
