#!/usr/bin/env bash
# abi-bootstrap.sh — fresh-deploy bring-up from an extracted ABI release tarball.
#
# Run AFTER extracting the release tarball into the hermes-agent dir (default:
# current dir; override with HERMES_DIR). Brings up the core stack (abi-db +
# abi-api + camofox), any enabled compose profiles (e.g. kanban), and reconciles
# the default skills library. Idempotent — safe to re-run.
#
# Usage:
#   ./scripts/abi-bootstrap.sh                # uses $PWD
#   HERMES_DIR=/opt/hermes-agent ./scripts/abi-bootstrap.sh
#
# This is the "deploy consistently from the tarball" path. For in-place updates
# of an existing deployment, use abi-update.sh --apply instead.

set -euo pipefail

HERMES_DIR="${HERMES_DIR:-$(pwd)}"
COMPOSE="docker-compose.abi-api.yml"
ENVFILE="docker.env"
cd "$HERMES_DIR"

if [ ! -f "$COMPOSE" ]; then
  echo "ERROR: $COMPOSE not found in $HERMES_DIR — run from the hermes-agent dir." >&2
  exit 1
fi

# 1. Ensure docker.env exists (scaffold from example, never clobber secrets)
if [ ! -f "$ENVFILE" ]; then
  if [ -f docker.env.example ]; then
    cp docker.env.example "$ENVFILE"
    echo "Created $ENVFILE from docker.env.example."
    echo ">>> Edit $ENVFILE (set ABI_DB_PASSWORD, OPTEIA_LICENSE_KEY, LICENSE_JWT_SECRET) and re-run." >&2
    exit 0
  fi
  echo "ERROR: no $ENVFILE and no docker.env.example to scaffold from." >&2
  exit 1
fi

# 1b. Create the shared HOST venv ($HERMES_DIR/.venv).
#
# The abi-memory-api CONTAINER builds its own venv (Dockerfile.abi-api). This is the
# HOST venv that user-level systemd gateways run against (/opt/hermes-agent/.venv/bin/
# hermes), that abi-provision's shebang points at, and that stdio MCP servers exec.
# Without it, a fresh install brings up a healthy memory stack but NO agent can ever
# run — abi-provision can't even start (bad shebang) and the gateway ExecStart 404s.
# Mirrors the .19 / Castor canonical shape. Idempotent: skipped if .venv/bin/hermes
# exists (so re-running bootstrap, and the update path, are no-ops).
create_host_venv() {
  local venv="$HERMES_DIR/.venv"
  if [ -x "$venv/bin/hermes" ]; then
    echo "Host venv present ($venv) — skipping creation."
    return 0
  fi
  if [ ! -f "$HERMES_DIR/pyproject.toml" ] || [ ! -f "$HERMES_DIR/uv.lock" ]; then
    echo "WARN: pyproject.toml/uv.lock missing in $HERMES_DIR — cannot create host venv." >&2
    echo "      abi-provision and the gateway will NOT run until a venv exists." >&2
    return 0
  fi

  echo "Creating host venv ($venv) — resolving + downloading deps (a few minutes, one-time)..."
  # Ensure uv is available (the tarball does not ship it; abi-deploy.sh requires only
  # docker + python3 + curl). Mirrors scripts/install.sh's uv bootstrap.
  local uv_bin
  uv_bin="$(command -v uv 2>/dev/null || true)"
  if [ -z "$uv_bin" ]; then
    if [ -x /usr/local/bin/uv ]; then uv_bin=/usr/local/bin/uv
    elif [ -x "$HOME/.local/bin/uv" ]; then uv_bin="$HOME/.local/bin/uv"
    else
      echo "  uv not found — installing from astral.sh ..."
      if ! curl -fsSL https://astral.sh/uv/install.sh | sh >/dev/null 2>&1; then
        echo "WARN: uv install failed — host venv NOT created." >&2
        return 0
      fi
      uv_bin="$HOME/.local/bin/uv"
    fi
  fi

  # uv-managed Python must live under a world-traversable path so non-root agent users
  # can exec the venv interpreter (default uv paths land under the creating user's
  # $HOME/.local/share/uv, which agent users can't traverse). See install.sh #21457.
  export UV_PYTHON_INSTALL_DIR="${UV_PYTHON_INSTALL_DIR:-/usr/local/share/uv/python}"
  export UV_PYTHON_BIN_DIR="${UV_PYTHON_BIN_DIR:-/usr/local/share/uv/bin}"
  export UV_PROJECT_ENVIRONMENT="$venv"
  export UV_NO_CONFIG=1   # don't inherit a random user's uv.toml/pyproject under sudo

  # Frozen = exact uv.lock (hash-verified transitives, no re-resolve). Fall back to
  # --locked, then a plain resolve, so a stale-but-present lockfile never hard-blocks
  # bring-up. Extras mirror .19: [all] (feature tools) + [abi] (psycopg2/onnxruntime/
  # tokenizers) + [messaging] (telegram) + [edge-tts] (voice). No torch/CUDA/playwright
  # (those left [all] post-2026-05-12 lazy-install migration).
  local _log
  _log="$(mktemp)"
  if "$uv_bin" sync --frozen --extra all --extra abi --extra messaging --extra edge-tts >"$_log" 2>&1 \
     || "$uv_bin" sync --locked --extra all --extra abi --extra messaging --extra edge-tts >"$_log" 2>&1 \
     || "$uv_bin" sync --extra all --extra abi --extra messaging --extra edge-tts >"$_log" 2>&1; then
    tail -3 "$_log"
  else
    echo "WARN: uv sync failed — host venv incomplete. Last 20 lines:" >&2
    tail -20 "$_log" >&2
    rm -f "$_log"
    return 0
  fi
  rm -f "$_log"

  # Root-own the venv (tamper-proof: agents read/exec but can't mutate the runtime).
  # The gateway needs only read+exec; on-demand lazy-dep install is intentionally off
  # (every extra the agents use is pre-installed above).
  chown -R root:root "$venv" 2>/dev/null || true
  chmod -R a+rX "$venv" 2>/dev/null || true
  echo "  host venv ready: $venv/bin/hermes"
}
create_host_venv

# 2. Compose profiles (opt-in services) from docker.env
PROFILE_FLAGS=""
if grep -qE '^COMPOSE_PROFILES=' "$ENVFILE" 2>/dev/null; then
  PROFILES="$(grep -E '^COMPOSE_PROFILES=' "$ENVFILE" | head -1 | cut -d= -f2- | tr -d '"' | tr ',' ' ')"
  for p in $PROFILES; do [ -n "$p" ] && PROFILE_FLAGS="$PROFILE_FLAGS --profile $p"; done
fi
[ -n "$PROFILE_FLAGS" ] && echo "Compose profiles enabled:$PROFILE_FLAGS"

# 3. Build + bring up the stack
echo "Building images..."
docker compose --env-file "$ENVFILE" -f "$COMPOSE" build 2>&1 | tail -3
echo "Starting services..."
docker compose --env-file "$ENVFILE" -f "$COMPOSE" $PROFILE_FLAGS up -d 2>&1 | tail -5

# 4. Reconcile default skills from the tarball
echo "Reconciling skills..."
if [ -d opteia-skills/shared ]; then
  sudo mkdir -p /opt/abi-tools/skills
  if command -v rsync >/dev/null 2>&1; then
    sudo rsync -a --delete opteia-skills/shared/ /opt/abi-tools/skills/
  else
    sudo cp -a opteia-skills/shared/. /opt/abi-tools/skills/
  fi
  sudo chown -R root:abi-agents /opt/abi-tools/skills 2>/dev/null || sudo chown -R root:root /opt/abi-tools/skills
  sudo chmod -R g+rX /opt/abi-tools/skills
  echo "  shared MCP skills -> /opt/abi-tools/skills"
fi

# 4a. Shared kanban board dir (group-writable by abi-agents, setgid so new files
# inherit the group). A fresh box lacked this → the first provisioned agent's
# kanban cron tick threw PermissionError every 60s. Idempotent; also repairs
# existing boxes on update. Perms match the fleet reference (.19).
echo "Ensuring shared kanban board dir..."
sudo groupadd -f abi-agents 2>/dev/null || true
sudo install -d -o root -g abi-agents -m 2775 /opt/abi-tools/kanban
echo "  kanban board dir -> /opt/abi-tools/kanban (root:abi-agents 2775)"
if [ -d opteia-skills/standard ]; then
  for u in $(ls /home/ 2>/dev/null); do
    [ -d "/home/$u/.hermes" ] || continue
    # Root creates the dir (agent user can't mkdir into a root-owned ~/.hermes);
    # the chown below fixes ownership so the creds-sync step can write as the user.
    sudo mkdir -p "/home/$u/.hermes/skills/opteia/standard"
    sudo cp -a opteia-skills/standard/. "/home/$u/.hermes/skills/opteia/standard/"
    sudo chown -R "$u:$u" "/home/$u/.hermes/skills/opteia/standard"
  done
  echo "  standard skills -> per-agent ~/.hermes/skills/opteia/standard"
fi

# 4b. Enforce ABI memory policy on agent homes (disable local file-based memory
# tool; root-own the SOUL policy section). Idempotent; safe on fresh + existing.
echo "Enforcing ABI memory policy..."
if [ -x scripts/abi-enforce-memory-policy.sh ]; then
  sudo bash scripts/abi-enforce-memory-policy.sh || echo "  (memory-policy enforcement reported warnings — continuing)"
else
  echo "  scripts/abi-enforce-memory-policy.sh not found in tarball — skip"
fi

# 4c. Install the fleet-health telemetry timer (hourly push to api.opteia.com).
# Agent-independent (system python3, stdlib only) so it reports agent/venv breakage
# rather than going silent. Reporter lives at a fixed /opt/abi-tools path so it
# works on bare-metal + per-user code layouts. Best-effort; never blocks bring-up.
echo "Installing fleet-health telemetry timer..."
if [ -x scripts/abi-install-fleet-timer.sh ]; then
  sudo bash scripts/abi-install-fleet-timer.sh "$HERMES_DIR" || echo "  (fleet-timer install reported warnings — continuing)"
else
  echo "  scripts/abi-install-fleet-timer.sh not found in tarball — skip"
fi

# 4d. Install the Dreamer nightly timer (memory densify/dedup at ~03:17). Ships the
# unit files + wrapper; PRESERVES enablement — only enables where already enabled,
# so a fresh/customer box gets the mechanism but stays OFF until per-box sign-off
# (densify rewrites memory + spends the box's LLM credits). Best-effort; never blocks.
echo "Installing Dreamer nightly timer..."
if [ -x scripts/abi-install-dreamer-timer.sh ]; then
  sudo bash scripts/abi-install-dreamer-timer.sh "$HERMES_DIR" || echo "  (dreamer-timer install reported warnings — continuing)"
else
  echo "  scripts/abi-install-dreamer-timer.sh not found in tarball — skip"
fi

# 5. Health check
echo "Waiting for abi-api health..."
for i in $(seq 1 30); do
  if curl -sf http://localhost:8010/health >/dev/null 2>&1; then
    echo "Health check passed. Version: $(cat VERSION 2>/dev/null)"
    # Harden the code dir (secure self-update boundary): root-own it so a
    # compromised agent can't delete-and-replace the root-owned
    # docker-compose.abi-api.yml / Dockerfile and inject code into the root
    # `docker build`. Done LAST so it doesn't block .venv / __pycache__ creation
    # during bring-up; those stay agent-owned (not in the tarball). abi-update.sh
    # re-asserts this after every apply.
    sudo chown root:root "$HERMES_DIR" 2>/dev/null || true
    docker compose --env-file "$ENVFILE" -f "$COMPOSE" ps 2>/dev/null || true
    exit 0
  fi
  sleep 2
done
echo "WARNING: abi-api did not become healthy within 60s — check 'docker compose logs abi-api'." >&2
exit 1
