#!/bin/sh
# docker/abi-venv-init.sh — per-agent install venv (ABI v4 container deployment).
#
# THE PROBLEM:
# The product ships a venv baked into the image at /opt/hermes/.venv. It is
# chowned writable at runtime so lazy_deps / agent `pip install`s succeed — but
# because it lives in the IMAGE layer, every `docker pull` + container recreate
# WIPES whatever the agent installed there. Agent-installed pip packages, MCP
# servers, and lazy deps routinely vanished on upgrade.
#
# THE MECHANISM (dual-venv, .pth-bridged):
# A second venv lives in the PERSISTENT state volume at $HERMES_HOME/venv
# (= /opt/data/venv). A .pth file in its site-packages points at the product
# venv's site-packages + the product source dir, both BY PATH. So the agent
# venv:
#   - INHERITS the product deps + the editable `hermes` source from /opt/hermes.
#     On a new image /opt/hermes/.venv is refreshed and the .pth (a path string)
#     picks up the new product deps + new source automatically — no reinstall,
#     no reconcile.
#   - HOLDS the agent's OWN `pip install`s in the volume, surviving the swap.
# The gateway runs on the volume venv's python (see main-wrapper.sh + the
# `hermes` wrapper below), so it sees BOTH the inherited product deps and its own
# packages.
#
# Why .pth, not `--system-site-packages`: `python -m venv --system-site-packages`
# follows the interpreter symlink to the REAL base and inherits the BASE's
# site-packages, NOT a sibling venv's — so it cannot inherit /opt/hermes/.venv.
# A .pth file is the correct way to add an arbitrary dir to sys.path. Verified
# 2026-07-23 (inherit + survives-swap + auto-source-update all PASS).
#
# PYTHON-MINOR PIN: the volume venv is built against the image Python (3.13). A
# v4 image that bumps 3.13 -> 3.14 invalidates compiled extensions; on mismatch
# this snapshots the agent's OWN packages (volume freeze minus product freeze)
# to venv.frozen.txt, moves the old venv aside, rebuilds a fresh one, and
# reinstalls from the frozen list (best-effort). Patch bumps (3.13.x -> 3.13.y)
# need no rebuild.
#
# Idempotent; safe to run every boot. Runs as the agent user (called from
# stage2-hook.sh via s6-setuidgid) so venv files are owned correctly.

set -eu

HERMES_HOME="${HERMES_HOME:-/opt/data}"
PRODUCT_DIR="/opt/hermes"
PRODUCT_VENV="$PRODUCT_DIR/.venv"
AGENT_VENV="$HERMES_HOME/venv"
FROZEN="$HERMES_HOME/venv.frozen.txt"

PY_BIN="$PRODUCT_VENV/bin/python"
[ -x "$PY_BIN" ] || { echo "[abi-venv] $PY_BIN missing — not a product layout; skipping"; exit 0; }

pyver=$("$PY_BIN" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")' 2>/dev/null || echo "")
[ -n "$pyver" ] || { echo "[abi-venv] cannot determine product python version — skipping"; exit 0; }

prod_site="$PRODUCT_VENV/lib/python$pyver/site-packages"

# --- Rebuild on Python-minor mismatch ---
if [ -f "$AGENT_VENV/pyvenv.cfg" ]; then
    agent_ver=$(grep -m1 '^version *=' "$AGENT_VENV/pyvenv.cfg" 2>/dev/null | sed 's/^[^=]*= *//' | cut -d. -f1,2 | tr -d ' \r')
    if [ -n "$agent_ver" ] && [ "$agent_ver" != "$pyver" ]; then
        echo "[abi-venv] Python changed ($agent_ver -> $pyver): rebuilding $AGENT_VENV"
        if [ -x "$AGENT_VENV/bin/pip" ]; then
            tmp_ag=$(mktemp 2>/dev/null || echo /tmp/_ag.$$); tmp_pr=$(mktemp 2>/dev/null || echo /tmp/_pr.$$)
            "$AGENT_VENV/bin/pip" freeze 2>/dev/null | sort > "$tmp_ag" || true
            "$PY_BIN" -m pip freeze 2>/dev/null | sort > "$tmp_pr" || true
            comm -23 "$tmp_ag" "$tmp_pr" > "$FROZEN.new" 2>/dev/null || true
            rm -f "$tmp_ag" "$tmp_pr"
            if [ -s "$FROZEN.new" ]; then mv "$FROZEN.new" "$FROZEN"; else rm -f "$FROZEN.new"; fi
        fi
        mv "$AGENT_VENV" "$AGENT_VENV.bak-$agent_ver" 2>/dev/null || rm -rf "$AGENT_VENV"
        n=$(wc -l < "$FROZEN" 2>/dev/null || echo 0)
        echo "[abi-venv] old venv preserved at $AGENT_VENV.bak-$agent_ver; $n agent packages recorded in $FROZEN"
    fi
fi

# --- Create the volume venv (first boot, or post-rebuild) ---
if [ ! -f "$AGENT_VENV/pyvenv.cfg" ]; then
    echo "[abi-venv] Creating per-agent venv at $AGENT_VENV (python $pyver)"
    "$PY_BIN" -m venv "$AGENT_VENV"
    # Belt-and-suspenders: ensure pip exists in the volume venv.
    "$AGENT_VENV/bin/python" -m ensurepip --upgrade 2>/dev/null || true
fi

agent_site="$AGENT_VENV/lib/python$pyver/site-packages"
mkdir -p "$agent_site"

# --- .pth bridge to product deps + source (the inherit-by-path mechanism) ---
# Re-written every boot so a path/product change is picked up immediately.
printf '%s\n%s\n' "$prod_site" "$PRODUCT_DIR" > "$agent_site/_abi_product.pth"

# --- `hermes` wrapper: run the product CLI on the volume venv's python ---
# hermes_cli is inherited via the .pth (product site-packages -> editable link
# into /opt/hermes), so it tracks the current image's source. The wrapper keeps
# the agent's own installed packages on sys.path for the running gateway.
cat > "$AGENT_VENV/bin/hermes" <<'WRAPPER'
#!/bin/sh
# ABI v4 per-agent wrapper: ensures the gateway runs on the volume venv's python
# so agent-installed packages are on sys.path.
exec "$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)/python" -m hermes_cli.main "$@"
WRAPPER
chmod +x "$AGENT_VENV/bin/hermes"

# --- First-boot reinstall from the frozen list (post Python-minor rebuild) ---
if [ -s "$FROZEN" ] && [ ! -f "$AGENT_VENV/.frozen-applied" ]; then
    echo "[abi-venv] Reinstalling agent packages from $FROZEN (best-effort)"
    "$AGENT_VENV/bin/pip" install -q -r "$FROZEN" 2>&1 | tail -1 \
        || echo "[abi-venv] some frozen packages failed — see $FROZEN"
    touch "$AGENT_VENV/.frozen-applied"
fi

echo "[abi-venv] OK: $AGENT_VENV (python $pyver, .pth -> $prod_site + $PRODUCT_DIR)"
