# obsidian-vault-tools

Petits outils en ligne de commande pour garder un coffre [Obsidian](https://obsidian.md) propre quand il grossit : liens cassés, notes orphelines, frontmatter incomplet, notes oubliées, doublons. Écrits pour mon propre coffre (plus de mille notes, alimenté en partie par des agents IA), puis généralisés.

Tout tourne en local, sans réseau. Seuls `vault-links` et `vault-frontmatter` modifient des fichiers, et les deux ont un mode `--dry-run`.

## Installation

```bash
pip install git+https://github.com/braiinz94/obsidian-vault-tools
```

Python 3.12 ou plus. Dépendances : `pyyaml`, `rapidfuzz`.

## Le coffre visé

Chaque commande prend le chemin du coffre en argument, ou à défaut la variable `OBSIDIAN_VAULT` :

```bash
export OBSIDIAN_VAULT=~/mon-coffre
vault-lint
```

Par défaut les notes sont cherchées dans `<coffre>/wiki/` ; si ce dossier n'existe pas, c'est la racine du coffre qui est analysée. `--notes-dir` (ou `OBSIDIAN_NOTES_DIR`) change ce dossier.

Les outils supposent une organisation simple, facultative :

```
coffre/
├── index.md, log.md, README.md…    fichiers de service (comptent pour les liens entrants)
├── raw/                            notes brutes à compiler (tag to-compile)
└── wiki/
    └── <domaine>/
        ├── _MOC-<domaine>.md       hub du domaine
        └── <cluster>/
            ├── _MOC-<cluster>.md   hub du cluster
            ├── _JOURNAL-<cluster>.md
            └── pages/              les notes du cluster
```

Le **cluster** d'une note est le dossier de second niveau (`wiki/<domaine>/<cluster>/…`), ou le champ `cluster:` de son frontmatter pour le bilan de santé.

## Les commandes

| Commande | Rôle | Écrit ? |
|---|---|---|
| `vault-lint` | Liens cassés, orphelines, frontmatter, références contestées, structure | non |
| `vault-health` | Bilan qualitatif : notes qui vieillissent, clusters déséquilibrés, doublons | non |
| `vault-links` | Répare les wikilinks cassés, liste les cas ambigus en JSON | oui (`--dry-run`) |
| `vault-frontmatter` | Complète les champs de frontmatter manquants | oui (`--dry-run`) |
| `vault-hash` | Empreinte SHA-256 du corps de chaque note, hors frontmatter | non |
| `vault-me-audit` | Conformité à la pile [markdown.engineering](https://www.markdown.engineering/) | non |
| `scripts/scrub.sh` | Passe de contrôle pour un hook git ou une tâche planifiée | non |

### `vault-lint`

```bash
vault-lint ~/mon-coffre
vault-lint --exclude genere --structured-root Projets --forbid-git-remote
```

Contrôles bloquants (code de sortie 1) :

1. **Liens cassés** — `[[Note]]`, `[[dossier/Note]]`, `[[Note|alias]]`, `[[Note#titre]]`, et `[[Note\|alias]]` dans un tableau. Les liens du frontmatter sont ignorés : ce sont des métadonnées.
2. **Orphelines** — ni lien sortant, ni lien entrant.
3. **Frontmatter absent**.
4. **Frontmatter invalide ou incomplet** — `date`, `tags`, `type`, `status` sont requis.
5. **Références contestées** — une note active qui cite une note marquée `status: contested` (ou `statut: contested`). Utile pour garder une source écartée dans le coffre, par traçabilité, sans qu'elle soit encore citée ailleurs.
6. **Structure** (`--structured-root DOMAINE`, optionnel) — à la racine du domaine et de chacun de ses clusters, seuls les hubs préfixés (`_MOC-`, `_JOURNAL-`, `_INDEX`, `_NOTES-`, ou `INDEX.md`) sont admis ; le reste va dans un sous-dossier.
7. **Sécurité** (`--forbid-git-remote`, optionnel) — échoue si le coffre a un remote git, pour un coffre qui doit rester sur la machine.

Contrôles informatifs (sans effet sur le code de sortie) : notes d'un dossier `pages/` absentes des hubs de leur cluster, et clusters dont des notes ont bougé aujourd'hui sans que le `_JOURNAL-*` suive.

`--exclude DOSSIER` (répétable, ou `VAULT_LINT_EXCLUDE=a,b`) retire un dossier du lint, typiquement un dossier régénéré automatiquement. Ses notes restent connues pour la résolution des liens, comme dans Obsidian.

### `vault-health`

```bash
vault-health ~/mon-coffre
vault-health --cluster alpha --json
```

Notes `to-compile` du dossier `raw/` de plus de 7 jours, notes `to-verify` de plus de 30 jours, clusters de moins de 5 ou plus de 50 notes, tags rares dans un cluster, paires de notes qui citent au moins 3 notes en commun sans se citer, titres quasi identiques.

### `vault-links`

```bash
vault-links ~/mon-coffre --auto --dry-run       # voir ce qui serait fait
vault-links ~/mon-coffre --auto                 # appliquer, cas ambigus → ambiguous-links.json
vault-links ~/mon-coffre --apply decisions.json # appliquer vos arbitrages
```

Dans l'ordre : un lien préfixé par un chemin dont le nom seul existe est réécrit (`[[a/b/Note]]` → `[[Note]]`) ; une méta-note générique (`[[Plan]]`, `[[_MOC]]`, `[[Audit]]`…) devient `[[Plan-<cluster>]]` si cette note existe (`--meta-names` pour changer la liste) ; une correspondance approximative au-dessus de 92 % (`--threshold`) est appliquée. Le reste part dans le fichier JSON : remplissez `resolution` pour chaque cas, puis `--apply`. Seul le corps des notes est réécrit.

### `vault-frontmatter`

```bash
vault-frontmatter ~/mon-coffre --dry-run
vault-frontmatter ~/mon-coffre --cluster-roots Projets,Recherche
```

Ajoute `date` (date de modification du fichier), `tags`, `type` (déduit du nom), `status: active`, `source`, `cluster`, `verified` quand ils manquent. Ne remplace jamais une valeur existante. Les notes au YAML illisible sont laissées telles quelles.

### `vault-hash`

```bash
vault-hash ~/avant > avant.txt
vault-hash ~/apres > apres.txt
diff avant.txt apres.txt
```

Une ligne `sha256  chemin/relatif.md` par note. Le frontmatter et les lignes vides en trop sont ignorés : on vérifie qu'une migration ou une normalisation n'a pas touché au contenu.

### `vault-me-audit`

Vérifie les 6 piliers et les 4 techniques d'orchestration de la pile décrite sur [markdown.engineering](https://www.markdown.engineering/) : git, outillage, registre des MCP, des agents, contrats des commandes, journal horodaté, points de validation, conditions d'arrêt, recettes. Les emplacements attendus (`CLAUDE.md`, `log.md`, `wiki/Resources/…`) sont une convention ; un JSON passé par `--config` les remplace :

```json
{"agent_instructions": "AGENTS.md", "log": "journal.md"}
```

### `scripts/scrub.sh`

```bash
scripts/scrub.sh ~/mon-coffre
SCRUB_INDEX_CMD="python3 outils/regen_index.py --check" scripts/scrub.sh ~/mon-coffre
```

Lance le lint et, si `SCRUB_INDEX_CMD` est défini, votre propre contrôle d'index générés. Sort 1 si l'un échoue. Le paquet doit être installé (ou `PYTHON` pointer vers un interpréteur qui le trouve).

## Développement

```bash
python -m pip install -e ".[dev]"
ruff check . && ruff format --check .
pytest -q
```

Les tests construisent un faux coffre dans un dossier temporaire ; aucun ne lit de vrai coffre.

## Licence

Apache-2.0, voir [LICENSE](LICENSE).

---

## In English

Small command-line tools to keep a growing Obsidian vault healthy: `vault-lint` (broken wikilinks, orphans, frontmatter, references to notes marked `contested`, optional folder-structure and no-git-remote checks), `vault-health` (aging notes, unbalanced clusters, near-duplicate titles, related notes that never link to each other), `vault-links` (auto-fixes broken wikilinks, exports ambiguous cases to JSON for manual review), `vault-frontmatter` (fills in missing frontmatter fields without overwriting), `vault-hash` (SHA-256 of note bodies, to check a migration did not alter content) and `vault-me-audit` (compliance with the markdown.engineering stack). Pass the vault path as an argument or set `OBSIDIAN_VAULT`. Everything runs locally; the two commands that write files have a `--dry-run` mode. The output is in French. Apache-2.0.
