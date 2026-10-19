"""Lint d'un coffre Obsidian.

Contrôles bloquants (code de sortie 1 si au moins un problème) :
  1. Wikilinks cassés
  2. Notes orphelines (ni lien sortant ni lien entrant)
  3. Frontmatter absent
  4. Frontmatter invalide ou incomplet (date, tags, type, status)
  5. Références contestées : une note active qui cite une note marquée
     `status: contested` (ou `statut: contested`)
  6. Structure (optionnel, --structured-root) : à la racine d'un domaine
     et de chacun de ses clusters, seuls les hubs préfixés sont admis
  7. Sécurité (optionnel, --forbid-git-remote) : le coffre ne doit avoir
     aucun remote git

Contrôles informatifs (sans effet sur le code de sortie) :
  - notes d'un dossier `pages/` absentes des hubs (_MOC-*, _INDEX*, INDEX.md)
    de leur cluster
  - clusters dont des notes ont bougé aujourd'hui sans que le _JOURNAL-* suive

Usage :
    vault-lint [coffre] [--notes-dir wiki] [--exclude DOSSIER]... [--structured-root DOMAINE]...
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from ._common import (
    extract_wikilinks,
    is_hidden,
    notes_root,
    parse_csv,
    parse_frontmatter,
    resolve_vault,
)

REQUIRED_FIELDS = frozenset({"date", "tags", "type", "status"})
DEFAULT_SERVICE_FILES = ("index.md", "log.md", "README.md", "CLAUDE.md")
DEFAULT_HUB_PREFIXES = ("_MOC-", "_JOURNAL-", "_INDEX", "_NOTES-")
EXCLUDE_ENV = "VAULT_LINT_EXCLUDE"
STRUCTURED_ENV = "VAULT_STRUCTURED_ROOTS"
MAX_LISTED = 20


@dataclass
class LintConfig:
    exclude: frozenset[str] = frozenset()
    service_files: tuple[str, ...] = DEFAULT_SERVICE_FILES
    structured_roots: tuple[str, ...] = ()
    hub_prefixes: tuple[str, ...] = DEFAULT_HUB_PREFIXES
    forbid_git_remote: bool = False


@dataclass
class LintReport:
    vault: Path
    files: int = 0
    broken: list[tuple[str, str]] = field(default_factory=list)
    orphans: list[str] = field(default_factory=list)
    fm_missing: list[str] = field(default_factory=list)
    fm_invalid: list[str] = field(default_factory=list)
    index_lines: int | None = None
    contested_refs: list[tuple[str, str]] = field(default_factory=list)
    contested_pages: int = 0
    structure: list[str] = field(default_factory=list)
    security: list[str] = field(default_factory=list)
    outside_hubs: list[str] = field(default_factory=list)
    stale_journals: list[str] = field(default_factory=list)

    @property
    def blocking(self) -> int:
        return (
            len(self.broken)
            + len(self.orphans)
            + len(self.fm_missing)
            + len(self.fm_invalid)
            + len(self.contested_refs)
            + len(self.structure)
            + len(self.security)
        )


def md_files(root: Path, exclude: frozenset[str] = frozenset()) -> list[Path]:
    """Notes du dossier, hors .obsidian/.git et hors dossiers exclus."""
    return sorted(
        p
        for p in root.rglob("*.md")
        if not is_hidden(p) and not any(part in exclude for part in p.relative_to(root).parts)
    )


def _is_contested(fm: object) -> bool:
    return isinstance(fm, dict) and "contested" in (fm.get("status"), fm.get("statut"))


def _basename(target: str) -> str:
    # Obsidian accepte [[dossier/Note]] : on résout par le nom seul
    return target.rsplit("/", 1)[-1]


def check_structure(root: Path, cfg: LintConfig) -> list[str]:
    violations = []
    for domain in cfg.structured_roots:
        domain_dir = root / domain
        if not domain_dir.is_dir():
            continue
        for p in sorted(domain_dir.iterdir()):
            if p.is_file() and p.suffix == ".md" and not p.name.startswith(cfg.hub_prefixes):
                violations.append(f"Note à la racine de {domain}/ : {p.name}")
        for cluster_dir in sorted(domain_dir.iterdir()):
            if not cluster_dir.is_dir() or cluster_dir.name in cfg.exclude:
                continue
            for p in sorted(cluster_dir.iterdir()):
                if not (p.is_file() and p.suffix == ".md") or p.name == "INDEX.md":
                    continue
                if not p.name.startswith(cfg.hub_prefixes):
                    violations.append(
                        f"Note hors hub à la racine du cluster {domain}/{cluster_dir.name}/ : "
                        f"{p.name} (à ranger dans un sous-dossier)"
                    )
    return violations


def check_git_remote(vault: Path) -> list[str]:
    config = vault / ".git" / "config"
    if config.exists() and "[remote " in config.read_text(encoding="utf-8", errors="replace"):
        return ["Remote git configuré alors que le coffre doit rester local"]
    return []


def check_hub_coverage(root: Path, vault: Path, cfg: LintConfig) -> tuple[list[str], list[str]]:
    outside, stale = [], []
    today = time.strftime("%Y-%m-%d")

    def day(p: Path) -> str:
        return time.strftime("%Y-%m-%d", time.localtime(p.stat().st_mtime))

    for pages_dir in sorted(root.rglob("pages")):
        if not pages_dir.is_dir():
            continue
        cluster_root = pages_dir.parent
        if any(part in cfg.exclude for part in cluster_root.relative_to(root).parts):
            continue
        hubs = [
            *cluster_root.glob("_MOC-*.md"),
            *cluster_root.glob("_INDEX*.md"),
            *cluster_root.glob("INDEX.md"),
        ]
        hub_links: set[str] = set()
        for hub in hubs:
            hub_links |= extract_wikilinks(hub.read_text(encoding="utf-8", errors="replace"))
        moved_today = 0
        for pg in sorted(pages_dir.glob("*.md")):
            if pg.stem not in hub_links:
                outside.append(pg.relative_to(vault).as_posix())
            if day(pg) == today:
                moved_today += 1
        if moved_today and not any(day(j) == today for j in cluster_root.glob("_JOURNAL-*.md")):
            stale.append(
                f"{cluster_root.relative_to(vault).as_posix()} "
                f"({moved_today} note(s) modifiée(s) aujourd'hui, _JOURNAL non actualisé)"
            )
    return outside, stale


def run_lint(vault: Path, root: Path, cfg: LintConfig) -> LintReport:
    report = LintReport(vault=vault)
    files = md_files(root, cfg.exclude)
    report.files = len(files)
    # Les wikilinks se résolvent dans tout le coffre, dossiers exclus compris (comme Obsidian)
    resolution_files = md_files(root)
    service_files = [vault / name for name in cfg.service_files if (vault / name).exists()]
    names = {p.stem for p in resolution_files + service_files}

    outgoing: dict[str, set[str]] = {}
    incoming: dict[str, set[str]] = {n: set() for n in names}
    contested_by_stem: dict[str, bool] = {}
    texts = {f: f.read_text(encoding="utf-8", errors="replace") for f in files}

    for f, text in texts.items():
        fm = parse_frontmatter(text)
        contested_by_stem[f.stem] = _is_contested(fm)
        links = extract_wikilinks(text)
        outgoing[f.stem] = links
        for target in links:
            if target in names:
                incoming[target].add(f.stem)
            elif _basename(target) in names:
                incoming[_basename(target)].add(f.stem)
            else:
                report.broken.append((f.relative_to(vault).as_posix(), target))

        rel = f.relative_to(vault).as_posix()
        if fm is None:
            report.fm_missing.append(rel)
        elif fm == "invalid":
            report.fm_invalid.append(rel)
        else:
            missing = REQUIRED_FIELDS - set(fm)
            if missing:
                report.fm_invalid.append(f"{rel} (manque : {','.join(sorted(missing))})")

    # Les fichiers de service (index.md, README.md…) comptent comme liens entrants,
    # mais ne sont pas lintés : ils contiennent souvent des exemples de syntaxe.
    for f in service_files:
        for target in extract_wikilinks(f.read_text(encoding="utf-8", errors="replace")):
            if target in names:
                incoming[target].add(f.stem)
            elif _basename(target) in names:
                incoming[_basename(target)].add(f.stem)

    report.orphans = sorted(
        {f.stem for f in files if not outgoing.get(f.stem) and not incoming.get(f.stem)}
    )

    index = vault / "index.md"
    if index.exists():
        report.index_lines = len(index.read_text(encoding="utf-8", errors="replace").splitlines())

    contested = {s for s, flag in contested_by_stem.items() if flag}
    report.contested_pages = len(contested)
    for source, targets in outgoing.items():
        if contested_by_stem.get(source):
            continue  # les notes contestées peuvent se citer entre elles
        report.contested_refs.extend(
            (source, t) for t in sorted(targets) if t in contested or _basename(t) in contested
        )

    report.structure = check_structure(root, cfg)
    if cfg.forbid_git_remote:
        report.security = check_git_remote(vault)
    report.outside_hubs, report.stale_journals = check_hub_coverage(root, vault, cfg)
    return report


def _print_list(items: list, fmt=str) -> None:
    for item in items[:MAX_LISTED]:
        print(f"  - {fmt(item)}")
    if len(items) > MAX_LISTED:
        print(f"  ... et {len(items) - MAX_LISTED} autres")


def print_report(r: LintReport) -> None:
    print(f"# Lint — {r.vault}")
    print(f"\n## Files: {r.files}")
    print(f"\n## Broken links: {len(r.broken)}")
    _print_list(r.broken, lambda x: f"{x[0]} → [[{x[1]}]]")
    print(f"\n## Orphans: {len(r.orphans)}")
    _print_list(r.orphans)
    print(f"\n## Frontmatter missing: {len(r.fm_missing)}")
    _print_list(r.fm_missing)
    print(f"\n## Frontmatter invalid: {len(r.fm_invalid)}")
    _print_list(r.fm_invalid)
    print(f"\n## Index: {r.index_lines if r.index_lines is not None else 'ABSENT'} lignes")
    print(f"\n## Contested refs: {len(r.contested_refs)}")
    _print_list(r.contested_refs, lambda x: f"[[{x[0]}]] → [[{x[1]}]] (contestée)")
    print(f"  → Notes contestées suivies : {r.contested_pages}")
    print(f"\n## Structure violations: {len(r.structure)}")
    _print_list(r.structure)
    print(f"\n## Security violations: {len(r.security)}")
    _print_list(r.security)
    print(f"\n## [informatif] Notes hors hub: {len(r.outside_hubs)}")
    _print_list(r.outside_hubs)
    print(f"\n## [informatif] JOURNAL non actualisé: {len(r.stale_journals)}")
    _print_list(r.stale_journals)


def build_config(args: argparse.Namespace) -> LintConfig:
    exclude = set(args.exclude or ())
    exclude |= parse_csv(os.environ.get(EXCLUDE_ENV)) or set()
    structured = list(args.structured_root or ())
    structured += sorted(parse_csv(os.environ.get(STRUCTURED_ENV)) or ())
    return LintConfig(
        exclude=frozenset(exclude),
        service_files=tuple(args.service_file) if args.service_file else DEFAULT_SERVICE_FILES,
        structured_roots=tuple(dict.fromkeys(structured)),
        hub_prefixes=tuple(args.hub_prefix) if args.hub_prefix else DEFAULT_HUB_PREFIXES,
        forbid_git_remote=args.forbid_git_remote,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="vault-lint", description=__doc__.splitlines()[0])
    parser.add_argument("vault", nargs="?", help="chemin du coffre (défaut : $OBSIDIAN_VAULT)")
    parser.add_argument("--notes-dir", help="sous-dossier des notes (défaut : wiki)")
    parser.add_argument(
        "--exclude",
        action="append",
        metavar="DOSSIER",
        help=f"dossier exclu du lint, répétable (aussi : ${EXCLUDE_ENV}, séparés par des virgules)",
    )
    parser.add_argument(
        "--service-file",
        action="append",
        metavar="FICHIER",
        help="fichier à la racine du coffre compté pour les liens entrants, répétable",
    )
    parser.add_argument(
        "--structured-root",
        action="append",
        metavar="DOMAINE",
        help=f"domaine dont la structure est contrôlée, répétable (aussi : ${STRUCTURED_ENV})",
    )
    parser.add_argument(
        "--hub-prefix", action="append", metavar="PREFIXE", help="préfixe de hub admis, répétable"
    )
    parser.add_argument(
        "--forbid-git-remote",
        action="store_true",
        help="échoue si le coffre a un remote git",
    )
    args = parser.parse_args(argv)

    vault = resolve_vault(args.vault)
    report = run_lint(vault, notes_root(vault, args.notes_dir), build_config(args))
    print_report(report)
    sys.stdout.flush()
    return 0 if report.blocking == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
