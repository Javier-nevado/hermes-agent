#!/usr/bin/env bash
# =============================================================================
# provision-paas.sh <customer> --lan-ip <ip>  — stand up the per-box abi-paas service.
#
# One command per box. The mini-PaaS sibling (abi-paas) the sandboxed agent uses to
# deploy + run customer web apps. nginx serves the LAN (bound ${LAN_IP}:8090); the
# mgmt API (:7100) is opteia-net only. See docker-compose.paas.yml + the abi-paas plan.
#
#   0. validate args (customer slug + LAN IP)
#   1. build the image (pilot) OR pull from registry (fleet, ABI_PAAS_IMAGE set)
#   2. write .env  (ABI_PAAS_TOKEN shared bearer, LAN_IP, ABI_PAAS_IMAGE)
#   3. docker compose up -d abi-paas
#   4. readiness probe  (curl http://${LAN_IP}:8090/health — proves the LAN publish)
#   5. print the agent env-wiring block (Major Tom applies it + restarts the agent)
#
# Runs ON THE BOX from /opt/abi-paas/ (where this script, docker-compose.paas.yml,
# and the paas/ build context live). Idempotent: re-running regenerates .env (keeping
# an existing ABI_PAAS_TOKEN) + recreates the container.
#
# Env:
#   ABI_PAAS_TOKEN  shared bearer (auto-generated if unset — the agent gets the same)
#   ABI_PAAS_IMAGE  image ref (default abi-paas:pilot; fleet: git.opteia.com/...)
#   LAN_IP          the box's customer-LAN IP (required, via --lan-ip)
# =============================================================================
set -euo pipefail

log() { printf '\033[1;34m[paas:%s]\033[0m %s\n' "${CUSTOMER:-?}" "$*"; }
die() { printf '\033[1;31m[paas:%s ERROR]\033[0m %s\n' "${CUSTOMER:-?}" "$*" >&2; exit 1; }

usage() {
  cat <<USAGE
usage:
  $0 <customer> --lan-ip <ip> [flags]

  <customer>          lowercase slug (used in logs / NetBox record).
  --lan-ip <ip>       the box's CUSTOMER-LAN IP nginx binds to (e.g. 192.168.0.7).
                      Humans/Joshua reach the apps here. ZeroTier is management-only —
                      it is never exposed. REQUIRED.
  --image <ref>       image ref (default abi-paas:pilot; fleet: git.opteia.com/opteia/abi-paas:<tag>).
  --token <tok>       reuse an existing ABI_PAAS_TOKEN (default: generate a fresh one).
  --no-build          skip `docker build` (image already present, e.g. registry pull).
  --yes               skip the confirmation prompt.

examples:
  $0 castor --lan-ip 192.168.0.7                       # pilot, build on-box
  $0 brian  --lan-ip 10.0.0.5 --image git.opteia.com/opteia/abi-paas:v1 --no-build
USAGE
}

CUSTOMER=""
LAN_IP=""
IMAGE=""
TOKEN_IN=""
NO_BUILD=0
ASSUME_YES=0
while [ $# -gt 0 ]; do
  case "$1" in
    --lan-ip)    LAN_IP="${2:-}"; shift 2 ;;
    --image)     IMAGE="${2:-}"; shift 2 ;;
    --token)     TOKEN_IN="${2:-}"; shift 2 ;;
    --no-build)  NO_BUILD=1; shift ;;
    --yes|-y)    ASSUME_YES=1; shift ;;
    -h|--help)   usage; exit 0 ;;
    --)          shift; break ;;
    -*)          echo "unknown flag: $1" >&2; usage >&2; exit 1 ;;
    *) if [ -z "$CUSTOMER" ]; then CUSTOMER="$1"; else echo "unexpected arg: $1" >&2; usage >&2; exit 1; fi; shift ;;
  esac
done
[ -n "$CUSTOMER" ] || { usage; exit 1; }
[ -n "$LAN_IP" ] || die "--lan-ip <ip> is required (the box's customer-LAN IP; ZT is never exposed)"

echo "$CUSTOMER" | grep -qE '^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$' \
  || die "<customer> must be lowercase [a-z0-9-], 1-63 chars"
# crude IPv4 validation (no hostname — this is the box's own LAN interface)
echo "$LAN_IP" | grep -qE '^[0-9]{1,3}(\.[0-9]{1,3}){3}$' \
  || die "--lan-ip must be an IPv4 address (got '$LAN_IP')"

HERE="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
# the compose lives one level up (workspace/hermes-agent-work/docker-compose.paas.yml);
# on-box it is scp'd alongside this script into /opt/abi-paas/.
COMPOSE="$HERE/docker-compose.paas.yml"
[ -f "$COMPOSE" ] || COMPOSE="$HERE/../docker-compose.paas.yml"
[ -f "$COMPOSE" ] || die "can't find docker-compose.paas.yml (looked in $HERE and $HERE/..)"

NET_NAME="${NET_NAME:-hermes-agent_opteia-net}"
IMAGE="${IMAGE:-${ABI_PAAS_IMAGE:-abi-paas:pilot}}"

# confirm
if [ "$ASSUME_YES" -ne 1 ]; then
  printf '\033[1m[paas:%s]\033[0m About to provision abi-paas:\n' "$CUSTOMER"
  printf '  LAN URL (humans/Joshua): \033[1mhttp://%s:8090\033[0m\n' "$LAN_IP"
  printf '  mgmt API (agent, opteia-net): http://abi-paas:7100\n'
  printf '  image: %s\n' "$IMAGE"
  printf '  ZT is NOT exposed (LAN-only). Continue? [y/N] '
  read -r CONFIRM || die "no confirmation (stdin closed) — re-run with --yes"
  case "$CONFIRM" in y|yes|Y|YES) ;; *) echo "aborted."; exit 1 ;; esac
fi

# the agent stack must own opteia-net already
docker network inspect "$NET_NAME" >/dev/null 2>&1 \
  || die "network $NET_NAME not found — bring up the agent stack first (it owns opteia-net)"

# ─── 1. image ────────────────────────────────────────────────────────────────
if [ "$NO_BUILD" -ne 1 ]; then
  if docker image inspect "$IMAGE" >/dev/null 2>&1; then
    log "image $IMAGE already present (use --no-build to skip checks)"
  else
    BUILD_CTX="$HERE/paas"
    [ -d "$BUILD_CTX" ] || BUILD_CTX="$HERE"
    [ -f "$BUILD_CTX/Dockerfile" ] || die "no Dockerfile found at $BUILD_CTX/Dockerfile (scp the paas/ build context)"
    log "building image $IMAGE from $BUILD_CTX (pilot)..."
    docker build -t "$IMAGE" "$BUILD_CTX" \
      || die "docker build failed (check the Dockerfile + that the build context was scp'd)"
  fi
fi

# ─── 2. .env (secrets — chmod 600) ───────────────────────────────────────────
ENV_FILE="$HERE/.env"
# keep an existing token (so the agent's ABI_PAAS_TOKEN stays valid across re-runs)
if [ -z "$TOKEN_IN" ] && [ -f "$ENV_FILE" ]; then
  TOKEN_IN="$(grep -E '^ABI_PAAS_TOKEN=' "$ENV_FILE" 2>/dev/null | cut -d= -f2- || true)"
fi
ABI_PAAS_TOKEN="${TOKEN_IN:-$(openssl rand -hex 32)}"
log "writing $ENV_FILE (secrets — chmod 600)..."
umask 077
cat > "$ENV_FILE" <<EOF
# abi-paas ($CUSTOMER) — generated $(date -u +%FT%TZ)
ABI_PAAS_TOKEN=$ABI_PAAS_TOKEN
ABI_PAAS_IMAGE=$IMAGE
ABI_PAAS_VERSION=${ABI_PAAS_VERSION:-pilot}
LAN_IP=$LAN_IP
EOF
chmod 600 "$ENV_FILE"

# ─── 3. up ───────────────────────────────────────────────────────────────────
log "docker compose up -d abi-paas..."
docker compose --env-file "$ENV_FILE" -f "$COMPOSE" up -d abi-paas

# ─── 4. readiness (prove nginx + the LAN publish) ────────────────────────────
log "waiting for http://${LAN_IP}:8090/health ..."
ok=0
for _ in $(seq 1 30); do
  if curl -fsS "http://${LAN_IP}:8090/health" >/dev/null 2>&1; then ok=1; break; fi
  sleep 2
done
[ "$ok" -eq 1 ] || die "abi-paas did not become ready at http://${LAN_IP}:8090/health — check: docker compose -f $COMPOSE logs abi-paas"

# also prove the mgmt API is up on opteia-net (token-gated, so expect 200 from /health)
docker exec abi-paas curl -fsS http://localhost:7100/health >/dev/null 2>&1 \
  || die "mgmt API not responding on :7100 inside the container — check supervisord logs"

log "abi-paas is LIVE on the LAN at http://${LAN_IP}:8090 (ZT not exposed)."

# ─── 5. agent env-wiring (Major Tom applies + restarts the agent) ────────────
cat <<NEXT

────────────────────────────────────────────────────────────────────────────────
 NEXT: wire the agent to use abi-paas
────────────────────────────────────────────────────────────────────────────────
1) Add these SHARED vars to the box's agent secrets env (e.g. /opt/abi-tools/v4/.env):

     ABI_PAAS_TOKEN=$ABI_PAAS_TOKEN
     LAN_IP=$LAN_IP

2) Add to the agent's environment block in docker-compose.abi-v4.yml
   (alongside KANBOARD_URL / CAMOFOX_URL):

     ABI_PAAS_URL: http://abi-paas:7100
     ABI_PAAS_TOKEN: \${ABI_PAAS_TOKEN}
     ABI_PAAS_HUMAN_URL: http://\${LAN_IP}:8090

3) Recreate the agent so it picks up the env:

     docker compose --env-file /opt/abi-tools/v4/.env \\
       -f /opt/abi-tools/v4/docker-compose.abi-v4.yml up -d <agent-service>

4) Record abi-paas in NetBox (service + http://${LAN_IP}:8090) — standing rule.
────────────────────────────────────────────────────────────────────────────────
NEXT

log "DONE. (abi-paas up; agent wiring printed above)"
