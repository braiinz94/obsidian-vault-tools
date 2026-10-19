#!/usr/bin/env bash
# scrub.sh — contrôle de cohérence du coffre, à la manière d'un « scrub » RAID :
# une passe qui vérifie que tout concorde, sans rien modifier.
# Sort 1 si un contrôle échoue : utilisable en hook git ou en tâche planifiée.
#
# Usage :
#   scripts/scrub.sh [coffre]          # coffre en argument, sinon $OBSIDIAN_VAULT
#
# Variables :
#   OBSIDIAN_VAULT     chemin du coffre (si pas d'argument)
#   SCRUB_INDEX_CMD    commande facultative qui vérifie vos index générés
#                      (doit sortir != 0 en cas de dérive), lancée depuis le coffre
#   PYTHON             interpréteur (défaut : python3)
set -u

V="${1:-${OBSIDIAN_VAULT:-}}"
if [ -z "$V" ] || [ ! -d "$V" ]; then
  echo "erreur : coffre introuvable (argument ou variable OBSIDIAN_VAULT)" >&2
  exit 2
fi
PY="${PYTHON:-python3}"
rc=0

echo "── SCRUB $(date -u +%FT%TZ) ──"

echo "1. Index générés ↔ sources"
if [ -n "${SCRUB_INDEX_CMD:-}" ]; then
  (cd "$V" && sh -c "$SCRUB_INDEX_CMD") || rc=1
else
  echo "   (ignoré : SCRUB_INDEX_CMD non défini)"
fi

echo "2. Intégrité du coffre (lint)"
"$PY" -m obsidian_vault_tools.lint "$V" | grep -E "^## (Broken|Orphans|Frontmatter|Contested|Structure|Security)"
[ "${PIPESTATUS[0]}" -eq 0 ] || rc=1

echo "── fin scrub (exit $rc) ──"
exit $rc
