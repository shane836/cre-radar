#!/usr/bin/env bash
# Install (or update) the daily cre-radar cron entry.
#
# Idempotent: re-running replaces the existing line rather than adding a second.
# Remove it with `crontab -e` and deleting the marked line.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UV="$(command -v uv)"
MARKER="# cre-radar daily digest"
HOUR="${1:-7}"

if [[ -z "$UV" ]]; then
  echo "uv not found on PATH" >&2
  exit 1
fi

LINE="0 ${HOUR} * * * cd '${REPO}' && '${UV}' run cre-radar run >> '${REPO}/cron.log' 2>&1 ${MARKER}"

# Keep every line that isn't ours, then append the current one.
{ crontab -l 2>/dev/null | grep -v -F "$MARKER" || true; echo "$LINE"; } | crontab -

echo "Installed:"
echo "  $LINE"
echo
echo "Verify with:  crontab -l | grep cre-radar"
echo "Logs:         tail -f '${REPO}/cron.log'"
