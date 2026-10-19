"""Audit de conformité d'un coffre à la pile « Markdown Engineering ».

Vérifie les 6 piliers et les 4 techniques d'orchestration décrits sur
https://www.markdown.engineering/ : présence de git, d'outillage, d'un registre
des MCP, des agents, des contrats de commandes, d'un journal horodaté, etc.
Lecture seule.

Les emplacements attendus (CLAUDE.md, log.md, wiki/Resources/…) sont une
convention par défaut : ils se changent dans un fichier JSON passé par --config.

Code de sortie : 0 si tout passe, 1 s'il reste au moins un manque.

Usage :
    vault-me-audit [coffre] [--config chemins.json]
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path

from ._common import resolve_vault

DEFAULT_PATHS = {
    "agent_instructions": "CLAUDE.md",
    "stack_moc": "wiki/_MOC/Markdown-Engineering-Stack.md",
    "mcp_registry": "wiki/Resources/MCPs-Registry.md",
    "command_contracts": "wiki/Resources/Slash-Commands-Contracts.md",
    "agents_registry": "wiki/Resources/Agents-Registry.md",
    "log": "log.md",
    "scripts_dir": "scripts",
}
MIN_NOTES = 100


@dataclass
class Check:
    name: str
    ok: bool
    hint: str = ""


def run_audit(vault: Path, paths: dict[str, str] | None = None) -> list[Check]:
    p = {**DEFAULT_PATHS, **(paths or {})}

    def read(key: str) -> str:
        try:
            return (vault / p[key]).read_text(encoding="utf-8")
        except (FileNotFoundError, IsADirectoryError):
            return ""

    def exists(key: str) -> bool:
        return (vault / p[key]).exists()

    agent_md = read("agent_instructions")
    moc = read("stack_moc")
    both = (agent_md + "\n" + moc).lower()
    log = read("log")
    scripts = vault / p["scripts_dir"]
    notes = sum(1 for f in vault.rglob("*.md") if ".git" not in f.parts)

    return [
        # Piliers de la pile (6)
        Check("P1 Primitives — dépôt git initialisé", (vault / ".git").exists()),
        Check(
            "P1 Primitives — outillage Python dans le dossier de scripts",
            scripts.is_dir() and any(scripts.glob("*.py")),
        ),
        Check(f"P1 Primitives — corpus Markdown (> {MIN_NOTES} notes)", notes > MIN_NOTES),
        Check(
            "P2 Modèles — section de pile dans les instructions d'agent",
            "## Stack markdown engineering" in agent_md,
            f"Ajouter une section '## Stack markdown engineering' à {p['agent_instructions']}",
        ),
        Check(
            "P2 Modèles — modèles documentés dans le MOC de pile",
            bool(re.search(r"\b(Opus|Sonnet|Haiku|GPT|Claude|Gemini|Mistral|Llama)\b", moc)),
            f"Documenter le routage des modèles dans {p['stack_moc']}",
        ),
        Check(
            "P3 Outils — scripts référencés dans les instructions ou le MOC",
            bool(re.search(re.escape(p["scripts_dir"]) + r"/\w+\.(py|sh)", agent_md + moc)),
            f"Citer les scripts de {p['scripts_dir']}/ dans {p['agent_instructions']} ou le MOC",
        ),
        Check("P4 MCP — registre présent", exists("mcp_registry"), f"Créer {p['mcp_registry']}"),
        Check(
            "P5 Skills — contrats des commandes présents",
            exists("command_contracts"),
            f"Créer {p['command_contracts']} (entrées et sorties de chaque commande)",
        ),
        Check(
            "P6 Agents — registre présent",
            exists("agents_registry"),
            f"Créer {p['agents_registry']}",
        ),
        # Techniques d'orchestration (4)
        Check("T-A Journal — présent et non vide", len(log) > 100, f"Tenir {p['log']}"),
        Check(
            "T-A Journal — horodatage ISO-8601 UTC",
            bool(re.search(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", log)),
            "Horodater les entrées au format AAAA-MM-JJTHH:MM:SSZ",
        ),
        Check(
            "T-B Points de validation — documentés",
            "approval" in both or "validation" in both,
            "Documenter qui valide quoi, et quand",
        ),
        Check(
            "T-C Conditions d'arrêt — documentées",
            "stop condition" in both or "condition d'arrêt" in both,
            "Documenter les conditions d'arrêt (seuils de lint, budgets, points de contrôle)",
        ),
        Check(
            "T-D Comportements composés — recettes documentées",
            any(k in moc.lower() for k in ("recipe", "recette", "workflow", "composition")),
            f"Documenter des recettes (ex. ingestion → lint → sauvegarde) dans {p['stack_moc']}",
        ),
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="vault-me-audit", description=__doc__.splitlines()[0])
    parser.add_argument("vault", nargs="?", help="chemin du coffre (défaut : $OBSIDIAN_VAULT)")
    parser.add_argument(
        "--config", type=Path, help="JSON qui remplace tout ou partie des chemins attendus"
    )
    args = parser.parse_args(argv)

    vault = resolve_vault(args.vault)
    paths = json.loads(args.config.read_text(encoding="utf-8")) if args.config else None
    checks = run_audit(vault, paths)
    passed = sum(c.ok for c in checks)

    print(f"# Audit Markdown Engineering — {passed}/{len(checks)} contrôles passés\n")
    for c in checks:
        print(f"[{'PASS' if c.ok else 'FAIL'}] {c.name}")
        if not c.ok and c.hint:
            print(f"       -> {c.hint}")
    print()
    if passed == len(checks):
        print("VERT — coffre conforme à la pile markdown.engineering.")
        return 0
    print(f"ROUGE — {len(checks) - passed} manque(s) à combler.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
