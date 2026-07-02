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
if [ -d opteia-skills/standard ]; then
  for u in $(ls /home/ 2>/dev/null); do
    [ -d "/home/$u/.hermes" ] || continue
    sudo -u "$u" mkdir -p "/home/$u/.hermes/skills/opteia/standard"
    sudo cp -a opteia-skills/standard/. "/home/$u/.hermes/skills/opteia/standard/"
    sudo chown -R "$u:$u" "/home/$u/.hermes/skills/opteia/standard"
  done
  echo "  standard skills -> per-agent ~/.hermes/skills/opteia/standard"
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
