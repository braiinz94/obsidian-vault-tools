from __future__ import annotations

import json

from obsidian_vault_tools import link_resolver


def test_auto_mode_resolves_path_meta_and_fuzzy(vault, tmp_path, capsys):
    vault.note("Projets/alpha/Plan-alpha.md")
    vault.note("Projets/alpha/Architecture-generale.md")
    vault.note("Projets/alpha/Cible.md")
    src = vault.note(
        "Projets/alpha/pages/Source.md",
        "[[ailleurs/Cible|alias]] [[Plan]] [[Architecture-generalle]] [[Sans rapport du tout]]",
    )
    out = tmp_path / "ambigus.json"

    assert link_resolver.main([str(vault.root), "--auto", "--out", str(out)]) == 0

    body = src.read_text(encoding="utf-8")
    assert "[[Cible|alias]]" in body
    assert "[[Plan-alpha]]" in body
    assert "[[Architecture-generale]]" in body
    assert "[[Sans rapport du tout]]" in body
    ambiguous = json.loads(out.read_text(encoding="utf-8"))
    assert [a["target"] for a in ambiguous] == ["Sans rapport du tout"]
    assert ambiguous[0]["referer"] == "wiki/Projets/alpha/pages/Source.md"
    assert "Ambigus (à trancher) : 1" in capsys.readouterr().out


def test_dry_run_writes_nothing(vault, tmp_path):
    vault.note("Cible.md")
    src = vault.note("Source.md", "[[dossier/Cible]]")
    before = src.read_bytes()
    out = tmp_path / "a.json"
    link_resolver.main([str(vault.root), "--auto", "--dry-run", "--out", str(out)])
    assert src.read_bytes() == before
    assert out.exists()


def test_frontmatter_is_never_rewritten(vault, tmp_path):
    vault.note("Cible.md")
    src = vault.note(
        "Source.md",
        "[[dossier/Cible]]",
        frontmatter='---\ndate: x\nsource: "[[dossier/Cible]]"\n---\n\n',
    )
    link_resolver.main([str(vault.root), "--auto", "--out", str(tmp_path / "a.json")])
    text = src.read_text(encoding="utf-8")
    assert 'source: "[[dossier/Cible]]"' in text
    assert "\n[[Cible]]" in text


def test_replacement_with_backslash_is_literal(vault):
    src = vault.note("Source.md", "[[Ancien]]")
    link_resolver.apply_replacements({(src, "Ancien"): "Nouveau\\1"})
    assert "[[Nouveau\\1]]" in src.read_text(encoding="utf-8")


def test_apply_mode(vault, tmp_path, capsys):
    vault.note("Bonne-cible.md")
    src = vault.note("Source.md", "[[Mauvaise]] [[Autre]]")
    decisions = tmp_path / "decisions.json"
    decisions.write_text(
        json.dumps(
            [
                {"referer": "wiki/Source.md", "target": "Mauvaise", "resolution": "Bonne-cible"},
                {"referer": "wiki/Source.md", "target": "Autre", "resolution": None},
            ]
        ),
        encoding="utf-8",
    )
    assert link_resolver.main([str(vault.root), "--apply", str(decisions)]) == 0
    body = src.read_text(encoding="utf-8")
    assert "[[Bonne-cible]]" in body
    assert "[[Autre]]" in body
    assert "Fichiers modifiés : 1" in capsys.readouterr().out


def test_custom_meta_names(vault, tmp_path):
    vault.note("Projets/alpha/Bilan-alpha.md")
    src = vault.note("Projets/alpha/pages/Source.md", "[[Bilan]]")
    link_resolver.main(
        [str(vault.root), "--auto", "--meta-names", "Bilan", "--out", str(tmp_path / "a.json")]
    )
    assert "[[Bilan-alpha]]" in src.read_text(encoding="utf-8")
