#!/bin/bash
# abi-install-fleet-timer.sh — install/refresh the fleet-health telemetry timer.
#
# Copies the reporter to a FIXED location (/opt/abi-tools/abi-fleet-reporter.py) so
# the timer ExecStart works on BOTH bare-metal (/opt/hermes-agent) and per-user
# (~/.hermes/hermes-agent) code layouts; installs the systemd timer+service; reloads
# and enables the timer. Idempotent. Best-effort: exits 0 always (never aborts a
# deploy on a timer failure — mirrors abi-enforce-memory-policy.sh's contract).
#
# Ships in the ABI release tarball. Called by:
#   - abi-bootstrap.sh   (fresh install)
#   - abi-update.sh      (every in-place update — refreshes the reporter copy)
#   - operators manually (sudo abi-install-fleet-timer.sh [hermes_dir])
#
# HERMES_DIR resolves from: $1 > $HERMES_DIR env > /opt/hermes-agent > CWD.
# Only ENABLES the timer (first push at the next :13, ±15min) — does NOT run the
# service immediately, so the update path (gateway briefly down mid-restart) never
# emits a transient false-decay push. Operators can force a push for testing with:
#   sudo systemctl start abi-fleet-reporter.service

set -u

HERMES_DIR="${1:-${HERMES_DIR:-}}"
if [ -z "$HERMES_DIR" ]; then
  if [ -f /opt/hermes-agent/VERSION ]; then HERMES_DIR="/opt/hermes-agent"
  else HERMES_DIR="$(pwd)"; fi
fi

log() { echo "[fleet-timer] $*"; }

SRC_SCRIPT="$HERMES_DIR/scripts/abi-fleet-reporter.py"
if [ ! -f "$SRC_SCRIPT" ]; then
  log "reporter not found at $SRC_SCRIPT — nothing to install (pre-3.3.3 box?)."
  exit 0
fi

# 1. Reporter -> fixed operator-tooling location (refreshes on every update).
mkdir -p /opt/abi-tools 2>/dev/null || true
if cp "$SRC_SCRIPT" /opt/abi-tools/abi-fleet-reporter.py 2>/dev/null; then
  chmod 755 /opt/abi-tools/abi-fleet-reporter.py 2>/dev/null || true
  log "reporter -> /opt/abi-tools/abi-fleet-reporter.py"
else
  log "WARN: could not copy reporter to /opt/abi-tools — timer may run stale code."
fi

# 2. Install the systemd units (idempotent overwrite).
for u in abi-fleet-reporter.timer abi-fleet-reporter.service; do
  src="$HERMES_DIR/scripts/systemd/$u"
  if [ -f "$src" ]; then
    cp "$src" /etc/systemd/system/"$u" 2>/dev/null || log "WARN: could not install $u"
  else
    log "WARN: $u not in tarball"
  fi
done
systemctl daemon-reload 2>/dev/null || true

# 3. Enable + start the TIMER (NOT the service — see header). Idempotent, never fails.
systemctl enable abi-fleet-reporter.timer 2>/dev/null || true
systemctl start abi-fleet-reporter.timer 2>/dev/null || true
log "timer enabled (hourly at :13, ±15min spread). Verify: systemctl list-timers abi-fleet-reporter.timer"
exit 0
