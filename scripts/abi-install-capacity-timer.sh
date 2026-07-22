#!/bin/bash
# abi-install-capacity-timer.sh — install/refresh the daily capacity-equivalent timer.
#
# Mirrors abi-install-fleet-timer.sh. Copies the capacity reporter + its prompt
# template to a FIXED location (/opt/abi-tools/) so the timer ExecStart works on BOTH
# bare-metal (/opt/hermes-agent) and per-user (~/.hermes/hermes-agent) layouts; installs
# the systemd timer+service; reloads + enables the timer. Idempotent. Best-effort:
# exits 0 always (never aborts a deploy on a timer failure).
#
# Ships in the ABI release tarball. Called by:
#   - abi-bootstrap.sh   (fresh install)
#   - abi-update.sh      (every in-place update — refreshes the reporter copy)
#   - operators manually (sudo abi-install-capacity-timer.sh [hermes_dir])
#
# HERMES_DIR resolves from: $1 > $HERMES_DIR env > /opt/hermes-agent > CWD.
# Only ENABLES the timer (first push at the next 02:27, ±5min) — does NOT run the
# service immediately. Operators can force a push for testing with:
#   sudo systemctl start abi-capacity-reporter.service

set -u

HERMES_DIR="${1:-${HERMES_DIR:-}}"
if [ -z "$HERMES_DIR" ]; then
  if [ -f /opt/hermes-agent/VERSION ]; then HERMES_DIR="/opt/hermes-agent"
  else HERMES_DIR="$(pwd)"; fi
fi

log() { echo "[capacity-timer] $*"; }

SRC_SCRIPT="$HERMES_DIR/scripts/abi-capacity-reporter.py"
SRC_PROMPT="$HERMES_DIR/scripts/abi_capacity_prompt.txt"
if [ ! -f "$SRC_SCRIPT" ]; then
  log "reporter not found at $SRC_SCRIPT — nothing to install (pre-capacity box?)."
  exit 0
fi

# 1. Reporter + prompt template -> fixed operator-tooling location (refreshes on every update).
mkdir -p /opt/abi-tools 2>/dev/null || true
if cp "$SRC_SCRIPT" /opt/abi-tools/abi-capacity-reporter.py 2>/dev/null; then
  chmod 755 /opt/abi-tools/abi-capacity-reporter.py 2>/dev/null || true
  log "reporter -> /opt/abi-tools/abi-capacity-reporter.py"
else
  log "WARN: could not copy reporter to /opt/abi-tools — timer may run stale code."
fi
if [ -f "$SRC_PROMPT" ]; then
  cp "$SRC_PROMPT" /opt/abi-tools/abi_capacity_prompt.txt 2>/dev/null || log "WARN: could not copy prompt template"
  log "prompt template -> /opt/abi-tools/abi_capacity_prompt.txt"
else
  log "WARN: prompt template not found at $SRC_PROMPT"
fi

# 2. Install the systemd units (idempotent overwrite).
for u in abi-capacity-reporter.timer abi-capacity-reporter.service; do
  src="$HERMES_DIR/scripts/systemd/$u"
  if [ -f "$src" ]; then
    cp "$src" /etc/systemd/system/"$u" 2>/dev/null || log "WARN: could not install $u"
  else
    log "WARN: $u not in tarball"
  fi
done
systemctl daemon-reload 2>/dev/null || true

# 3. Enable + start the TIMER (NOT the service — see header). Idempotent, never fails.
systemctl enable abi-capacity-reporter.timer 2>/dev/null || true
systemctl start abi-capacity-reporter.timer 2>/dev/null || true
log "timer enabled (nightly at 02:27, ±5min spread). Verify: systemctl list-timers abi-capacity-reporter.timer"
exit 0
