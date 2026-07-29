#!/command/with-contenv sh
# shellcheck shell=sh
# /opt/hermes/docker/main-wrapper.sh — wraps the container's CMD with
# the same argument-routing logic the pre-s6 entrypoint.sh used. Runs
# as /init's "main program" (Docker CMD) so it inherits stdin/stdout/
# stderr from the container.
#
# Shebang note: /init scrubs env before invoking CMD, so a plain
# `#!/bin/sh` wrapper sees an empty environ and `ENV HERMES_HOME=/opt/data`
# from the Dockerfile never reaches `hermes`. with-contenv repopulates
# the env from /run/s6/container_environment before exec'ing, which is
# what s6-supervised services use too (see main-hermes/run).
#
# Routing:
#   no args                       → exec `hermes` (the default)
#   first arg is an executable    → exec it directly (sleep, bash, sh, …)
#   first arg is anything else    → exec `hermes <args>` (subcommand passthrough)
#
# We drop to the hermes user via `s6-setuidgid` so the supervised
# workload runs unprivileged (UID 10000 by default).
set -e

# HOME comes through with-contenv as /root (the /init context). Override
# to the hermes user's home before dropping privileges so libraries that
# resolve paths via $HOME (e.g. discord lockfile under XDG_STATE_HOME)
# don't try to write to /root.
export HOME=/opt/data

cd /opt/data
# ABI v4: prefer the per-agent volume venv ($HERMES_HOME/venv) when present —
# it bridges the product venv via .pth so the gateway sees BOTH the inherited
# product deps and the agent's own installed packages (which survive image
# swaps). Fall back to the product venv otherwise (legacy / pre-v4 boots).
ABI_VENV="${HERMES_HOME:-/opt/data}/venv"
# shellcheck disable=SC1091
if [ -f "$ABI_VENV/bin/activate" ]; then
    . "$ABI_VENV/bin/activate"
else
    . /opt/hermes/.venv/bin/activate
fi

# ABI v4 license fingerprint staging (machine-fingerprint binding).
#
# NON-BLOCKING: the gateway ALWAYS boots from here. The license check runs at
# SESSION START inside the gateway (gateway/license_check.py → run.py
# _check_license), where a failure surfaces a USER-FACING message ("contact Opteia
# support") instead of silently never starting. A boot-time gate was silent death:
# a bot bound to another machine just looked dead in Telegram — the user who needs
# to see the error never saw anything.
#
# ARCHITECTURE NOTE: the gateway is launched by the runtime-generated `gateway-default`
# s6-rc longrun, NOT by this main-wrapper. So the env var this function exports only
# reaches main-wrapper's OWN process tree — it does NOT reach the gateway (verified:
# the gateway process had ABI_FINGERPRINT=<MISSING>). The PRIMARY stager is therefore
# cont-init.d/03-abi-license-fp (runs as root before any longrun starts, writes
# /run/abi-fingerprint which the gateway reads at session start). main-wrapper ALSO
# writes /run/abi-fingerprint here as a redundant backup + keeps the env export
# (harmless; useful if main-wrapper's own gateway ever runs via `gateway run --replace`).
# The fingerprint is sha256(product_uuid); product_uuid is mode 0400 (root-only), so
# only a root context (cont-init or main-wrapper, pre-setuidgid) can compute it. It is
# staged to /run (tmpfs, NOT the persistent volume) → a disk clone reboots → recomputes
# from new hardware → a different fingerprint (clone-safe).
#
# The gateway-side check then binds on first use (activate) and read-only-verifies
# the binding (verify) — no heartbeat/release/seat, which is what structurally removes
# the per-heartbeat KV writes that caused CF 1101 (see memory/abi-v4-fingerprint-licensing
# + memory/api-opteia-license-origin-1101-outage). Bare-executable passthrough
# (sleep/bash/sh) is intentionally NOT staged, so `docker exec`/`docker run … bash`
# still works for debugging with the gate on. Entirely skipped unless ABI_LICENSE_GATE=1
# (ABI_SEAT_GATE=1 accepted as a legacy alias for smooth cutover; pre-license-api
# boxes keep it off).
_stage_fingerprint() {
    [ "${ABI_LICENSE_GATE:-${ABI_SEAT_GATE:-0}}" = "1" ] || return 0
    PY=/opt/hermes/.venv/bin/python
    FP_HELPER=/opt/hermes/docker/abi-fingerprint.py
    if [ ! -f "$FP_HELPER" ]; then
        echo "[license] $FP_HELPER missing — cannot stage fingerprint; gateway will fail-open" >&2
        return 0
    fi
    # Compute the live fingerprint as root. NEVER exit on failure — the gateway must
    # always boot; a missing fingerprint makes the gateway fail-open + log, never a
    # silent dead bot. The PRIMARY stager is cont-init.d/03-abi-license-fp; this is a
    # redundant backup. `if cmd; then` consumes the failure under `set -e`.
    if fp=$("$PY" "$FP_HELPER"); then
        fp=$(printf '%s' "$fp" | tr -d ' \n\r\t')
        export ABI_FINGERPRINT="$fp"                              # main-wrapper's own tree (harmless)
        printf '%s' "$fp" > /run/abi-fingerprint 2>/dev/null || true   # gateway-readable (backup)
        chmod 0644 /run/abi-fingerprint 2>/dev/null || true
        fp_short=$(printf '%s' "$fp" | cut -c1-12)
        echo "[license] staged fingerprint ${fp_short}… → /run/abi-fingerprint (session-start gate will verify)" >&2
    else
        rc=$?
        echo "[license] fingerprint helper exit $rc — gateway will fail-open (cont-init may still stage)" >&2
    fi
    return 0
}

if [ $# -eq 0 ]; then
    _stage_fingerprint
    exec s6-setuidgid hermes hermes
fi

if command -v "$1" >/dev/null 2>&1; then
    # Bare executable — pass through directly (NOT license-gated: debugging path).
    exec s6-setuidgid hermes "$@"
fi

# Hermes subcommand pass-through.
_stage_fingerprint
exec s6-setuidgid hermes hermes "$@"
