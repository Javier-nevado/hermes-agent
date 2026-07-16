#!/bin/bash
# abi-install-dreamer-timer.sh — install/refresh the Dreamer nightly timer.
#
# The Dreamer runs nightly (~03:17) to densify/dedup memory. Ships the wrapper to a
# FIXED location (/usr/local/sbin/abi-dreamer-nightly.sh — the service ExecStart) +
# the systemd timer/service. Idempotent. Best-effort: exits 0 always (never aborts a
# deploy/update on a timer failure — mirrors abi-install-fleet-timer.sh's contract).
#
# ENABLEMENT CONTRACT (differs from the fleet timer):
#   The fleet timer is read-only telemetry -> always `enable --now`. The Dreamer is
#   NOT read-only: densify rewrites memory in place + spends the box's own LLM
#   credits, and dedup/extract-llm are opt-in per-box. So this installer PRESERVES
#   enablement: it only starts the timer if it was ALREADY enabled (e.g. .19, or a
#   customer box afer sign-off). On a fresh/customer box it installs the mechanism
#   but leaves the timer DISABLED until the operator opts in
#   (`systemctl enable --now abi-dreamer-nightly.timer`). Refreshing a unit file does
#   not clear an existing enablement (the wants-symlink survives daemon-reload), so
#   an already-enabled box keeps running across updates.
#
# SERVICE-FILE PRESERVATION (also differs from fleet):
#   The timer + wrapper are config-agnostic -> refreshed on every install. The
#   SERVICE unit, however, holds per-box config (ABI_DENSIFY_AGENTS / ABI_DENSIFY_MODEL
#   / ABI_DREAMER_DEDUP / ABI_EXTRACT_LLM_ENABLE in [Service] Environment=). So the
#   service is installed ONLY IF ABSENT — never overwritten — or a self-update would
#   silently wipe an enabled box's tuned config. To adopt a new service template,
#   `rm /etc/systemd/system/abi-dreamer-nightly.service` then re-run.
#
# Ships in the ABI release tarball. Called by:
#   - abi-bootstrap.sh   (fresh install)
#   - abi-update.sh      (every in-place update — refreshes wrapper + timer)
#   - operators manually (sudo abi-install-dreamer-timer.sh [hermes_dir])
#
# HERMES_DIR resolves from: $1 > $HERMES_DIR env > /opt/hermes-agent > CWD.

set -u

HERMES_DIR="${1:-${HERMES_DIR:-}}"
if [ -z "$HERMES_DIR" ]; then
  if [ -f /opt/hermes-agent/VERSION ]; then HERMES_DIR="/opt/hermes-agent"
  else HERMES_DIR="$(pwd)"; fi
fi

log() { echo "[dreamer-timer] $*"; }

SRC_WRAPPER="$HERMES_DIR/scripts/abi-dreamer-nightly.sh"
if [ ! -f "$SRC_WRAPPER" ]; then
  log "wrapper not found at $SRC_WRAPPER — nothing to install (pre-dreamer box?)."
  exit 0
fi

# 1. Wrapper -> fixed sbin location (the service ExecStart). Refreshed every install.
mkdir -p /usr/local/sbin 2>/dev/null || true
if cp "$SRC_WRAPPER" /usr/local/sbin/abi-dreamer-nightly.sh 2>/dev/null; then
  chmod 755 /usr/local/sbin/abi-dreamer-nightly.sh 2>/dev/null || true
  log "wrapper -> /usr/local/sbin/abi-dreamer-nightly.sh"
else
  log "WARN: could not copy wrapper to /usr/local/sbin — timer ExecStart may 404."
fi

# 2. Timer unit -> always refresh (no per-box config).
TIMER_SRC="$HERMES_DIR/scripts/systemd/abi-dreamer-nightly.timer"
if [ -f "$TIMER_SRC" ]; then
  cp "$TIMER_SRC" /etc/systemd/system/abi-dreamer-nightly.timer 2>/dev/null \
    || log "WARN: could not install abi-dreamer-nightly.timer"
else
  log "WARN: abi-dreamer-nightly.timer not in tarball"
fi

# 3. Service unit -> install ONLY IF ABSENT (preserve per-box config — see header).
SERVICE_DST="/etc/systemd/system/abi-dreamer-nightly.service"
SERVICE_SRC="$HERMES_DIR/scripts/systemd/abi-dreamer-nightly.service"
if [ -f "$SERVICE_SRC" ]; then
  if [ -f "$SERVICE_DST" ]; then
    log "service already installed ($SERVICE_DST) — keeping existing per-box config."
  else
    cp "$SERVICE_SRC" "$SERVICE_DST" 2>/dev/null \
      && log "service -> $SERVICE_DST (template; edit Environment= before enabling)" \
      || log "WARN: could not install abi-dreamer-nightly.service"
  fi
else
  log "WARN: abi-dreamer-nightly.service not in tarball"
fi
systemctl daemon-reload 2>/dev/null || true

# 4. PRESERVE enablement: start the timer only if it was already enabled. Never
#    auto-enable on a fresh/customer box (densify mutates memory + spends credits).
if systemctl is-enabled --quiet abi-dreamer-nightly.timer 2>/dev/null; then
  systemctl start abi-dreamer-nightly.timer 2>/dev/null || true
  log "timer already enabled — refreshed + started (next fire ~03:17, ±5min)."
  log "  Verify: systemctl list-timers abi-dreamer-nightly.timer"
else
  log "timer installed but NOT enabled (densify is opt-in). To activate after sign-off:"
  log "  sudo systemctl enable --now abi-dreamer-nightly.timer"
fi
exit 0
