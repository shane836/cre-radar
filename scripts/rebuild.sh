#!/usr/bin/env bash
# Rebuild the database from scratch.
#
# Extraction rules (heuristic.py, sources.toml) only affect NEW extractions —
# junk already stored stays stored. After tightening a rule, rebuild.
# Scoring rules do NOT need this: use `cre-radar rescore`.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO"

DB="${CRE_DB:-cre_radar.db}"
if [[ -f "$DB" ]]; then
  cp "$DB" "${DB}.bak"
  echo "backed up -> ${DB}.bak"
fi

rm -f "$DB"
uv run cre-radar collect
uv run cre-radar score
echo
echo "Rebuilt. Preview with: uv run cre-radar digest --dry-run"
