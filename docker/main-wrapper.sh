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

# ABI v4 license hard gate (machine-fingerprint binding).
#
# WHY HERE, NOT IN cont-init.d: this image uses s6-overlay Architecture B — the
# gateway IS this main program (the CMD /init execs), NOT an s6-rc longrun
# (main-hermes/run is a deliberate `exec sleep infinity` no-op). s6-overlay v3's
# legacy-cont-init does NOT abort the container when a cont-init script exits
# non-zero — it logs "exited 1", then reports "legacy-cont-init successfully
# started" and runs the main program anyway (verified on the v4 image). So a
# cont-init gate is silently defeated. But THIS script's exit IS the container's
# exit: failing here BEFORE `exec hermes` means no gateway boots (compose Restart
# retries, but it won't come up until the license validates).
#
# The gate runs as root (main-wrapper is /init's main program, pre-setuidgid) so it
# can read the mode-0400 DMI product_uuid and compute the fingerprint
# (sha256(product_uuid)) via abi-fingerprint.py → abi-license-gate.py: it binds on
# first boot (activate) then read-only-confirms the binding every session (verify).
# No heartbeat, no release, no seat token — the binding replaces all of that and is
# what structurally removes the per-heartbeat KV writes that caused CF 1101 (see
# memory/abi-v4-fingerprint-licensing + memory/api-opteia-license-origin-1101-outage).
# Bare-executable passthrough (sleep/bash/sh) is intentionally NOT gated, so
# `docker exec`/`docker run … bash` still works for debugging with the gate on.
# Entirely skipped unless ABI_LICENSE_GATE=1 (ABI_SEAT_GATE=1 accepted as a legacy
# alias for smooth cutover; pre-license-api boxes keep it off).
_license_gate() {
    [ "${ABI_LICENSE_GATE:-${ABI_SEAT_GATE:-0}}" = "1" ] || return 0
    PY=/opt/hermes/.venv/bin/python
    GATE=/opt/hermes/docker/abi-license-gate.py
    if [ ! -f "$GATE" ]; then
        echo "[license] $GATE missing — cannot enforce license gate; refusing to start" >&2
        exit 1
    fi
    # Capture the gate's OWN exit code. Do NOT write `if ! cmd; then rc=$?` —
    # the `!` negates cmd's status for the `if`, so inside `then` $? is the
    # negated value (0 on failure) → `exit "$rc"` exits 0 → the gateway boots
    # unlicensed. The form below is set -e-safe and captures the real code.
    if "$PY" "$GATE" verify; then :; else
        rc=$?
        echo "[license] gate FAILED (rc=$rc) — refusing to start the gateway" >&2
        exit "$rc"
    fi
}

if [ $# -eq 0 ]; then
    _license_gate
    exec s6-setuidgid hermes hermes
fi

if command -v "$1" >/dev/null 2>&1; then
    # Bare executable — pass through directly (NOT license-gated: debugging path).
    exec s6-setuidgid hermes "$@"
fi

# Hermes subcommand pass-through.
_license_gate
exec s6-setuidgid hermes hermes "$@"
