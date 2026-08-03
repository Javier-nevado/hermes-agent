#!/usr/bin/env bash
# abi-v4-update.sh — roll the v4 agent fleet to a new image + prune the old tag.
#
# The 8 agents are the `v4` compose project (docker-compose.abi-v4.yml), image set by
# ABI_IMAGE in .env. abi-update.sh does NOT manage these (it manages the abi-memory
# api/db stack), so old image tags accumulated. This script does a VERIFIED rollout and
# removes the previous tag ONLY once every agent is healthy on the new image — and
# rolls back (revert + restart) if health fails, so a bad image never leaves the fleet down.
#
# Usage: abi-v4-update.sh <new-tag>     e.g.  abi-v4-update.sh v4.2.1
set -euo pipefail

V4_DIR="/opt/abi-tools/v4"
COMPOSE="$V4_DIR/docker-compose.abi-v4.yml"
ENVF="$V4_DIR/.env"
REGISTRY="git.opteia.com/opteia/abi-agent"
HEALTH_TIMEOUT=180   # seconds to wait for all agents healthy

[ $# -eq 1 ] || { echo "Usage: $0 <new-tag>  (e.g. v4.2.1)"; exit 1; }
NEW_TAG="$1"
[ -f "$COMPOSE" ] && [ -f "$ENVF" ] || { echo "ERROR: $COMPOSE / $ENVF not found"; exit 1; }

NEW_IMAGE="${REGISTRY}:${NEW_TAG}"
CUR_IMAGE=$(grep -E "^ABI_IMAGE=" "$ENVF" | head -1 | cut -d= -f2-)
CUR_TAG="${CUR_IMAGE##*:}"

echo "[v4-update] current: $CUR_IMAGE"
echo "[v4-update] target:  $NEW_IMAGE"
[ "$CUR_IMAGE" = "$NEW_IMAGE" ] && { echo "[v4-update] already on $NEW_TAG — nothing to do"; exit 0; }
[ "$NEW_TAG" = "latest" ] && { echo "[v4-update] refuse: target latest is ambiguous (use a real version tag)"; exit 1; }
[ "$CUR_TAG" = "latest" ] && { echo "[v4-update] refuse: current is latest — pin .env to a real version before updating"; exit 1; }

cd "$V4_DIR"

# 1. ensure the new image is present
echo "[v4-update] pulling $NEW_IMAGE ..."
docker pull "$NEW_IMAGE" >/dev/null

# 2. capture target count + bump ABI_IMAGE
AGENT_COUNT=$(docker ps -a --filter name=abi-agent- --format "{{.Names}}" | wc -l)
cp -a "$ENVF" "$ENVF.bak.$(date +%s)"
sed -i "s|^ABI_IMAGE=.*|ABI_IMAGE=$NEW_IMAGE|" "$ENVF"
echo "[v4-update] .env bumped; $AGENT_COUNT agents in fleet"

# 3. recreate fleet
echo "[v4-update] recreating fleet ..."
docker compose -f "$COMPOSE" up -d >/dev/null

# 4. wait for all agents healthy
healthy_count() { docker ps --filter name=abi-agent- --filter health=healthy --format "{{.Names}}" | wc -l; }
echo "[v4-update] waiting for $AGENT_COUNT/$AGENT_COUNT healthy (up to ${HEALTH_TIMEOUT}s) ..."
elapsed=0
while [ "$(healthy_count)" -lt "$AGENT_COUNT" ] && [ "$elapsed" -lt "$HEALTH_TIMEOUT" ]; do
  sleep 5; elapsed=$((elapsed + 5))
done
H=$(healthy_count)

if [ "$H" -ge "$AGENT_COUNT" ]; then
  echo "[v4-update] $H/$AGENT_COUNT healthy on $NEW_TAG"
  # 5. prune the PREVIOUS tag — only if nothing still runs it
  if docker ps -a --format "{{.Image}}" | grep -qx "$CUR_IMAGE"; then
    echo "[v4-update] WARNING: $CUR_IMAGE still in use by a container — NOT pruning it"
  else
    docker rmi "$CUR_IMAGE" 2>/dev/null && echo "[v4-update] pruned previous tag $CUR_TAG" || echo "[v4-update] (could not rmi $CUR_IMAGE — may already be absent)"
  fi
  echo "[v4-update] DONE — disk: $(df -h / | awk 'NR==2{print $3"/"$2" used, "$4" free"}')"
  exit 0
else
  # ROLLBACK: revert .env + restart on the known-good image
  echo "[v4-update] only $H/$AGENT_COUNT healthy after ${HEALTH_TIMEOUT}s — ROLLING BACK to $CUR_IMAGE"
  sed -i "s|^ABI_IMAGE=.*|ABI_IMAGE=$CUR_IMAGE|" "$ENVF"
  docker compose -f "$COMPOSE" up -d >/dev/null
  echo "[v4-update] reverted. Investigate before retrying. (new image $NEW_TAG left pulled for inspection.)"
  exit 1
fi
