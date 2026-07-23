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

# ABI v4 seat-licensing hard gate.
#
# WHY HERE, NOT IN cont-init.d: this image uses s6-overlay Architecture B — the
# gateway IS this main program (the CMD /init execs), NOT an s6-rc longrun
# (main-hermes/run is a deliberate `exec sleep infinity` no-op). s6-overlay v3's
# legacy-cont-init does NOT abort the container when a cont-init script exits
# non-zero — it logs "exited 1", then reports "legacy-cont-init successfully
# started" and runs the main program anyway (verified on the v4 image). So a
# cont-init gate is silently defeated. But THIS script's exit IS the container's
# exit: failing here BEFORE `exec hermes` means no gateway boots (compose Restart
# retries, but it won't come up until a seat frees / the license validates).
#
# Checkout runs as root (main-wrapper is /init's main program, pre-setuidgid); the
# token it writes (/run/abi-seat-token, 0600) is read by the detached heartbeat the
# gate starts. Bare-executable passthrough (sleep/bash/sh) is intentionally NOT
# gated, so `docker exec`/`docker run … bash` still works for debugging with the
# gate on. Entirely skipped when ABI_SEAT_GATE!=1 (pre-license-api parity).
_seat_gate() {
    [ "${ABI_SEAT_GATE:-0}" = "1" ] || return 0
    PY=/opt/hermes/.venv/bin/python
    GATE=/opt/hermes/docker/abi-seat-gate.py
    if [ ! -f "$GATE" ]; then
        echo "[seat] $GATE missing — cannot enforce seat gate; refusing to start" >&2
        exit 1
    fi
    # Capture the checkout's OWN exit code. Do NOT write `if ! cmd; then rc=$?` —
    # the `!` negates cmd's status for the `if`, so inside `then` $? is the
    # negated value (0 on failure) → `exit "$rc"` exits 0 → the gateway boots
    # without a seat. The form below is set -e-safe and captures the real code.
    if "$PY" "$GATE" checkout; then :; else
        rc=$?
        echo "[seat] checkout FAILED (rc=$rc) — refusing to start the gateway" >&2
        exit "$rc"
    fi
    setsid "$PY" "$GATE" heartbeat >/dev/null 2>&1 &
    echo "[seat] heartbeat loop started (pid $!)"
}

if [ $# -eq 0 ]; then
    _seat_gate
    exec s6-setuidgid hermes hermes
fi

if command -v "$1" >/dev/null 2>&1; then
    # Bare executable — pass through directly (NOT seat-gated: debugging path).
    exec s6-setuidgid hermes "$@"
fi

# Hermes subcommand pass-through.
_seat_gate
exec s6-setuidgid hermes hermes "$@"
