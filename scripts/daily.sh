#!/usr/bin/env bash
# The daily run, as launchd invokes it.
#
# `cre-radar run` is the pipeline; everything here is the three things a
# scheduled job needs and the pipeline should not know about: one run at a
# time, a log that cannot grow forever, and a timestamp on every line so a
# failure can be placed in time.
#
# Deliberately not `set -e`. A non-zero exit from the pipeline has to be
# logged and reported, not swallowed by the shell exiting first.
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG="${REPO}/cron.log"
LOCK="${REPO}/.run.lock"
MAX_LOG_BYTES=$((2 * 1024 * 1024))

# launchd gives a job almost no PATH and never reads a shell profile, so both
# `uv` and the `vercel` CLI (which `publish --deploy` shells out to) have to be
# findable. The plist sets this too; repeating it here means the script also
# works when run by hand from a bare shell.
export PATH="${HOME}/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"

say() { printf '%s  %s\n' "$(date '+%Y-%m-%d %H:%M:%S %Z')" "$*" >>"$LOG"; }

# --- Log rotation --------------------------------------------------------
# One generation back is enough: this log is for "what happened this morning",
# and the database is the actual record. Rotate before the run so a crash
# mid-run still leaves the evidence in place.
if [[ -f "$LOG" ]]; then
  size=$(wc -c <"$LOG" | tr -d ' ')
  if (( size > MAX_LOG_BYTES )); then
    mv -f "$LOG" "${LOG}.1"
    say "rotated log at ${size} bytes"
  fi
fi

# --- Lock ----------------------------------------------------------------
# `mkdir` because it is atomic on every filesystem and macOS ships no
# `flock(1)`. A slow run must never have the next morning's run start on top
# of it: two processes writing one SQLite file is how the database gets
# corrupted.
if ! mkdir "$LOCK" 2>/dev/null; then
  stale_pid=$(cat "${LOCK}/pid" 2>/dev/null || echo "")
  if [[ -n "$stale_pid" ]] && kill -0 "$stale_pid" 2>/dev/null; then
    say "SKIPPED: a run is already in progress (pid ${stale_pid})"
    exit 0
  fi
  # The holder is gone — killed, or the machine lost power mid-run.
  say "clearing stale lock from pid ${stale_pid:-unknown}"
  rm -rf "$LOCK"
  if ! mkdir "$LOCK" 2>/dev/null; then
    say "FAILED: could not take the lock at ${LOCK}"
    exit 1
  fi
fi
echo $$ >"${LOCK}/pid"
trap 'rm -rf "$LOCK"' EXIT

# --- Run -----------------------------------------------------------------
# `--directory` rather than `cd`: it sets uv's project root *and* the working
# directory, so `.env` and the default relative `CRE_DB` both resolve, without
# this script depending on where it was invoked from.
say "start"
start=$SECONDS
uv run --directory "$REPO" cre-radar run >>"$LOG" 2>&1
status=$?
elapsed=$((SECONDS - start))

if (( status == 0 )); then
  say "done in ${elapsed}s"
else
  say "FAILED with exit ${status} after ${elapsed}s"
fi
exit "$status"
