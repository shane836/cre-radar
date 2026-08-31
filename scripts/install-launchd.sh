#!/usr/bin/env bash
# Install (or update) the daily cre-radar launchd agent.
#
# launchd rather than cron, for one reason: cron skips a run outright if the
# Mac is asleep at the appointed minute, and this laptop is usually shut at
# 07:00. launchd runs the job when the machine next wakes. (A machine that is
# fully powered off still misses the day — launchd catches up from sleep, not
# from off.)
#
# Idempotent: re-running rewrites the plist and reloads it.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LABEL="com.masonequity.cre-radar"
PLIST="${HOME}/Library/LaunchAgents/${LABEL}.plist"
HOUR="${1:-7}"
MINUTE="${2:-0}"

if ! command -v uv >/dev/null; then
  echo "uv not found on PATH" >&2
  exit 1
fi

chmod +x "${REPO}/scripts/daily.sh"

# One scheduler only. A leftover crontab line from the old installer would run
# the pipeline a second time, against the same SQLite file.
if crontab -l 2>/dev/null | grep -q "cre-radar"; then
  echo "Removing the old crontab entry — launchd replaces it."
  crontab -l 2>/dev/null | grep -v "cre-radar" | crontab -
fi

mkdir -p "${HOME}/Library/LaunchAgents"

# WorkingDirectory is $HOME, not the repo. The repo lives under Dropbox, whose
# directory handle can be transiently unavailable; launchd fails the whole job
# if its chdir fails. daily.sh passes `--directory` to uv instead, so nothing
# depends on the working directory.
cat >"$PLIST" <<PLISTEOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>${LABEL}</string>

    <key>ProgramArguments</key>
    <array>
        <string>/bin/bash</string>
        <string>${REPO}/scripts/daily.sh</string>
    </array>

    <key>WorkingDirectory</key>
    <string>${HOME}</string>

    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>${HOME}/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
        <key>HOME</key>
        <string>${HOME}</string>
    </dict>

    <key>StartCalendarInterval</key>
    <dict>
        <key>Hour</key>
        <integer>${HOUR}</integer>
        <key>Minute</key>
        <integer>${MINUTE}</integer>
    </dict>

    <!-- daily.sh writes its own timestamped log and takes the lock. These
         catch anything that dies before it gets that far — a missing uv, a
         syntax error — which would otherwise vanish silently. -->
    <key>StandardOutPath</key>
    <string>${REPO}/launchd.log</string>
    <key>StandardErrorPath</key>
    <string>${REPO}/launchd.log</string>

    <!-- Never at load. Installing the agent should not kick off a fetch of
         every source, and RunAtLoad also fires on every login. -->
    <key>RunAtLoad</key>
    <false/>
</dict>
</plist>
PLISTEOF

# bootout is expected to fail the first time; the agent is not loaded yet.
launchctl bootout "gui/$(id -u)" "$PLIST" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"

printf 'Installed %s — daily at %02d:%02d\n' "$LABEL" "$HOUR" "$MINUTE"
echo
echo "Verify:    launchctl print gui/$(id -u)/${LABEL} | head -20"
echo "Run now:   launchctl kickstart -p gui/$(id -u)/${LABEL}"
echo "Logs:      tail -f '${REPO}/cron.log'"
echo "Remove:    launchctl bootout gui/$(id -u) '${PLIST}' && rm '${PLIST}'"
echo
echo "If the first scheduled run produces nothing, grant Full Disk Access to"
echo "/bin/bash in System Settings > Privacy & Security: the repo is inside"
echo "Dropbox, which is a protected location for background jobs."
