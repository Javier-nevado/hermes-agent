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
# NON-BLOCKING: the gateway ALWAYS boots from here. The license check itself runs
# at SESSION START inside the gateway (gateway/license_check.py → run.py
# _check_license), where a failure surfaces a USER-FACING message ("contact Opteia
# support") instead of silently never starting. A boot-time gate was silent death:
# a bot bound to another machine just looked dead in Telegram — the user who needs
# to see the error never saw anything.
#
# THIS function's only job is to compute the LIVE fingerprint as ROOT (main-wrapper
# is /init's main program, pre-setuidgid) and hand it to the non-root gateway via
# the ABI_FINGERPRINT env var, which `exec s6-setuidgid hermes hermes` inherits
# (s6-setuidgid preserves environ). product_uuid is mode 0400 (root-only) so only
# this root phase can read it; the env var is NOT a file on disk → a disk clone
# reboots → recomputes from new hardware → a different fingerprint (clone-safe).
#
# WHY HERE, NOT cont-init.d: s6-overlay Architecture B — the gateway IS this main
# program (CMD). s6 v3 legacy-cont-init does NOT abort the container on a non-zero
# exit (it logs "exited 1" then runs the main program anyway), so a cont-init stage
# would be silently defeated; only THIS script's env reaches the gateway.
#
# The gateway-side check then binds on first use (activate) and read-only-verifies
# the binding (verify) — no heartbeat/release/seat, which is what structurally
# removes the per-heartbeat KV writes that caused CF 1101 (see
# memory/abi-v4-fingerprint-licensing + memory/api-opteia-license-origin-1101-outage).
# Bare-executable passthrough (sleep/bash/sh) is intentionally NOT staged, so
# `docker exec`/`docker run … bash` still works for debugging with the gate on.
# Entirely skipped unless ABI_LICENSE_GATE=1 (ABI_SEAT_GATE=1 accepted as a legacy
# alias for smooth cutover; pre-license-api boxes keep it off).
_stage_fingerprint() {
    [ "${ABI_LICENSE_GATE:-${ABI_SEAT_GATE:-0}}" = "1" ] || return 0
    PY=/opt/hermes/.venv/bin/python
    FP_HELPER=/opt/hermes/docker/abi-fingerprint.py
    if [ ! -f "$FP_HELPER" ]; then
        echo "[license] $FP_HELPER missing — cannot stage fingerprint; gateway will fail-open" >&2
        export ABI_FINGERPRINT=""
        return 0
    fi
    # Compute the live fingerprint as root. NEVER exit on failure — the gateway
    # must always boot. An empty fingerprint makes the gateway fail-open + log
    # (run.py _check_license), never a silent dead bot. `if cmd; then` consumes
    # the failure under `set -e`; rc captured directly in the else branch.
    if fp=$("$PY" "$FP_HELPER"); then
        export ABI_FINGERPRINT="$fp"
        fp_short=$(printf '%s' "$fp" | cut -c1-12)
        echo "[license] staged fingerprint ${fp_short}… → session-start gate will verify" >&2
    else
        rc=$?
        export ABI_FINGERPRINT=""
        echo "[license] fingerprint helper exit $rc — staged empty; gateway will fail-open" >&2
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
