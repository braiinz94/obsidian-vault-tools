from __future__ import annotations

import json
from datetime import date

from obsidian_vault_tools import health_check, me_audit

TODAY = date(2026, 6, 1)


def report(vault, cluster=None):
    return health_check.run(vault.root, vault.wiki, vault.root / "raw", cluster, TODAY)


def dated(day: str, status: str = "active", tags: str = "[]", cluster: str = "alpha") -> str:
    return (
        f"---\ndate: '{day}'\nstatus: {status}\ntype: note\ntags: {tags}\ncluster: {cluster}\n---\n"
    )


def test_aging_and_stagnant(vault):
    vault.note(
        "clip.md", base=vault.root / "raw", frontmatter=dated("2026-05-01", tags="[to-compile]")
    )
    vault.note(
        "frais.md", base=vault.root / "raw", frontmatter=dated("2026-05-30", tags="[to-compile]")
    )
    vault.note("vieille.md", frontmatter=dated("2026-03-01", status="to-verify"))
    vault.note("recente.md", frontmatter=dated("2026-05-20", status="to-verify"))
    r = report(vault)
    assert [x["path"] for x in r["to_compile_aging"]] == ["raw/clip.md"]
    assert r["to_compile_aging"][0]["days"] == 31
    assert [x["path"] for x in r["to_verify_stagnant"]] == ["wiki/vieille.md"]


def test_cluster_distribution_and_drift(vault):
    for i in range(6):
        vault.note(f"a{i}.md", tags="commun", cluster="gros")
    vault.note("a-rare.md", tags="rare", cluster="gros")
    vault.note("b.md", cluster="petit")
    r = report(vault)
    dist = r["cluster_distribution"]
    assert dist["distribution"] == {"gros": 7, "petit": 1}
    assert dist["anemic"] == [("petit", 1)]
    assert r["frontmatter_drift"] == [{"cluster": "gros", "rare_tags": [("rare", 1)]}]


def test_isolated_concepts_and_duplicates(vault):
    for name in ("X", "Y", "Z"):
        vault.note(f"{name}.md")
    vault.note("Un.md", "[[X]] [[Y]] [[Z]]")
    vault.note("Deux.md", "[[X]] [[Y]] [[Z]]")
    vault.note("Trois.md", "[[X]] [[Y]] [[Z]] [[Un]]")
    vault.note("Note-Finale.md")
    vault.note("sous/note_finale.md")
    r = report(vault)
    pairs = {tuple(sorted(i["pages"])) for i in r["isolated_concepts"]}
    assert pairs == {("Deux", "Un"), ("Deux", "Trois")}
    assert r["duplicate_titles"][0][0] == "notefinale"
    assert len(r["duplicate_titles"][0][1]) == 2


def test_cluster_filter(vault):
    vault.note("a.md", cluster="alpha")
    vault.note("b.md", cluster="beta")
    assert report(vault, cluster="beta")["pages"] == 1


def test_health_cli_outputs(vault, capsys):
    vault.note("a.md", cluster="alpha")
    assert health_check.main([str(vault.root)]) == 0
    out = capsys.readouterr().out
    assert "## 3. Répartition des clusters" in out
    assert "alpha : 1 notes (anémique)" in out
    assert health_check.main([str(vault.root), "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["pages"] == 1


def test_me_audit_empty_vault_is_red(vault, capsys):
    assert me_audit.main([str(vault.root)]) == 1
    assert "ROUGE" in capsys.readouterr().out


def test_me_audit_compliant_vault_is_green(vault, capsys):
    root = vault.root
    (root / ".git").mkdir()
    (root / "scripts").mkdir()
    (root / "scripts" / "outil.py").write_text("")
    for i in range(101):
        vault.note(f"n{i}.md")
    (root / "CLAUDE.md").write_text(
        "## Stack markdown engineering\nscripts/outil.py\napproval\nstop condition\n"
    )
    vault.note("_MOC/Markdown-Engineering-Stack.md", "Sonnet pour X. Recette : ingest puis lint.")
    for name in ("MCPs-Registry", "Slash-Commands-Contracts", "Agents-Registry"):
        vault.note(f"Resources/{name}.md")
    (root / "log.md").write_text("2026-01-01T10:00:00Z début\n" + "x" * 100)
    assert me_audit.main([str(root)]) == 0
    assert "14/14" in capsys.readouterr().out


def test_me_audit_custom_paths(vault, tmp_path):
    (vault.root / "AGENTS.md").write_text("## Stack markdown engineering\n")
    cfg = tmp_path / "chemins.json"
    cfg.write_text(json.dumps({"agent_instructions": "AGENTS.md"}))
    checks = {c.name: c.ok for c in me_audit.run_audit(vault.root, json.loads(cfg.read_text()))}
    assert checks["P2 Modèles — section de pile dans les instructions d'agent"]
    assert me_audit.main([str(vault.root), "--config", str(cfg)]) == 1


def test_health_text_report_lists_findings(vault, capsys, monkeypatch):
    monkeypatch.setattr(health_check, "date", type("D", (), {"today": staticmethod(lambda: TODAY)}))
    vault.note(
        "clip.md", base=vault.root / "raw", frontmatter=dated("2026-05-01", tags="[to-compile]")
    )
    vault.note("vieille.md", frontmatter=dated("2026-03-01", status="to-verify"))
    for i in range(51):
        vault.note(f"g{i}.md", tags="commun", cluster="gros")
    vault.note("g-rare.md", tags="rare", cluster="gros")
    for name in ("Un", "Deux"):
        vault.note(f"{name}.md", "[[X]] [[Y]] [[Z]]")
    vault.note("Note-Finale.md")
    vault.note("sous/note_finale.md")
    assert health_check.main([str(vault.root)]) == 0
    out = capsys.readouterr().out
    assert "raw/clip.md (cluster=alpha, 31 j)" in out
    assert "wiki/vieille.md (92 j)" in out
    assert "gros : 52 notes (surchargé)" in out
    assert "gros : rare (1)" in out
    assert "[[Deux]] <-> [[Un]] (3 liens communs)" in out or "[[Un]] <-> [[Deux]]" in out
    assert "notefinale :" in out
