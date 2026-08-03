#!/usr/bin/env bash
# abi-v4-lift.sh — lift a v3 bare-metal Hermes agent's state into a v4 named volume.
#
# v3 layout : /home/<agent>/.hermes  (HERMES_HOME: config.yaml, .env, state.db,
#             sessions/, SOUL.md, per-agent skills/, ...)
# v4 layout : named volume abi-hermes-<agent> mounted at /opt/data (= HERMES_HOME)
#
# Copies the agent's .hermes into the volume and chowns everything to the agent's
# uid:gid, so the v4 container (HERMES_UID=<uid>) can read/write it. The v4 image's
# stage2 hook seeds .env/config.yaml/SOUL.md ONLY when absent — because we lift the
# real ones, first boot preserves them untouched.
#
# ⚠️ v3's /home/<agent>/.local (pip --user packages) is NOT copied by default: v4
#    runs off the IMAGE venv (/opt/hermes/.venv via the /opt/data/venv .pth bridge),
#    NOT .local — copying it only bloats the volume (it cost ~2.4GB + pushed .19 to
#    100% disk on 2026-07-24). Pass --with-local only if a specific v3 user-install
#    is known to be needed at runtime.
#
# USAGE (on the box, as a sudo-capable user):
#   sudo abi-v4-lift.sh <agent> [uid:gid] [--fresh] [--with-local]
#     <agent>       agent name, e.g. mga   (source = /home/mga/.hermes)
#     uid:gid       volume ownership        (default: owner of /home/<agent>/.hermes)
#     --fresh       wipe the volume before copying (clean re-lift); skipped if absent
#     --with-local  ALSO copy /home/<agent>/.local (vestigial for v4 — see note above)
#
# Idempotent + re-runnable: safe to lift once for a dry run, then again at cutover.
# NOTE: stop the agent's v3 gateway first if you want a clean SQLite state.db copy
#       (a live DB can tear mid-copy). This script never starts/stops anything.
set -euo pipefail

FRESH=0
WITH_LOCAL=0
args=()
for a in "$@"; do
  case "$a" in
    --fresh) FRESH=1 ;;
    --with-local) WITH_LOCAL=1 ;;
    *) args+=("$a") ;;
  esac
done
AGENT="${args[0]:?usage: $0 <agent> [uid:gid] [--fresh] [--with-local]}"
OWN="${args[1]:-}"

HOME_DIR="/home/${AGENT}"
SRC="${HOME_DIR}/.hermes"
[ -d "$SRC" ] || { echo "abi-v4-lift: source ${SRC} not found" >&2; exit 1; }
[ -n "$OWN" ] || OWN="$(stat -c '%u:%g' "$SRC")"

VOL="abi-hermes-${AGENT}"
HELPER=alpine:3.20
command -v docker >/dev/null || { echo "abi-v4-lift: docker not found" >&2; exit 1; }
[ "$(id -u)" -eq 0 ] || { echo "abi-v4-lift: must run as root (sudo)" >&2; exit 1; }
docker image inspect "$HELPER" >/dev/null 2>&1 || docker pull "$HELPER" >/dev/null

echo "abi-v4-lift: agent=${AGENT} volume=${VOL} owner=${OWN} fresh=${FRESH} with_local=${WITH_LOCAL}"
docker volume create "$VOL" >/dev/null

if [ "$FRESH" -eq 1 ]; then
  docker run --rm -u 0:0 -v "${VOL}:/opt/data" "$HELPER" \
    sh -c 'rm -rf /opt/data/* /opt/data/.[!.]* /opt/data/..?* 2>/dev/null || true'
fi

LOCAL_MOUNT=""
[ "$WITH_LOCAL" -eq 1 ] && [ -d "${HOME_DIR}/.local" ] && LOCAL_MOUNT="-v ${HOME_DIR}/.local:/srclocal:ro"

# v3 stored service credentials under /home/<agent>/workspace/ — a SIBLING of .hermes
# (outside it), so copying .hermes alone leaves them behind. Bind the v3 workspace ro
# and lift every agent/credentials subtree (top-level + multi-account users/<uuid>/)
# into the volume's workspace/, where v4 credential resolution
# (HERMES_HOME-relative workspace/agent/credentials/) expects them. Surgical — only
# credential dirs, never the whole workspace (some hold 100M+ of generated outputs).
WORKSPACE_MOUNT=""
[ -d "${HOME_DIR}/workspace" ] && WORKSPACE_MOUNT="-v ${HOME_DIR}/workspace:/srcws:ro"

# v3 stored SSH keys under /home/<agent>/.ssh — another sibling of .hermes. The v4
# container runs the gateway as user `hermes` whose HOME=/opt/data, so SSH resolves
# keys from /opt/data/.ssh. Without this lift, agents lose ALL SSH access post-cutover
# (regression on .19 2026-07-28: Atlas lost id_ed25519_proxmox). Lift the whole .ssh
# dir (keys + known_hosts); perms tightened below so non-interactive agent SSH works.
SSH_MOUNT=""
[ -d "${HOME_DIR}/.ssh" ] && SSH_MOUNT="-v ${HOME_DIR}/.ssh:/srcssh:ro"

# Copy as root in the helper, then chown the whole volume to the agent uid:gid.
docker run --rm -u 0:0 -e OWN="$OWN" \
  -v "${VOL}:/opt/data" \
  -v "${SRC}:/src:ro" \
  $LOCAL_MOUNT \
  $WORKSPACE_MOUNT \
  $SSH_MOUNT \
  "$HELPER" sh -c '
    set -e
    cp -a /src/. /opt/data/
    if [ -d /srclocal ]; then mkdir -p /opt/data/.local; cp -a /srclocal/. /opt/data/.local/; fi
    if [ -d /srcws ]; then
      find /srcws -type d -path "*/agent/credentials" | while read -r d; do
        rel="${d#/srcws/}"
        mkdir -p "/opt/data/workspace/$(dirname "$rel")"
        cp -a "$d" "/opt/data/workspace/$rel"
      done
    fi
    if [ -d /srcssh ]; then
      mkdir -p /opt/data/.ssh
      cp -a /srcssh/. /opt/data/.ssh/
      chmod 700 /opt/data/.ssh
      find /opt/data/.ssh -type f ! -name "*.pub" -exec chmod 600 {} \;
      find /opt/data/.ssh -type f -name "*.pub" -exec chmod 644 {} \;
    fi
    chown -R "$OWN" /opt/data
  '

# Audit: warn about localhost/127.0.0.1 endpoints in lifted creds. These work
# bare-metal (agent == host) but break under containerization (localhost == the
# container itself, not the host). Operator must rewrite each to on-net service DNS
# (e.g. http://abi-memory-api:8010) or an external URL. Known case: kanban.json.
HITS=$(docker run --rm -v "${VOL}:/opt/data" "$HELPER" \
  sh -c 'grep -rEl "localhost|127\.0\.0\.1" /opt/data/workspace/agent/credentials/ 2>/dev/null || true')
if [ -n "$HITS" ]; then
  echo "abi-v4-lift: ⚠️  localhost endpoints in lifted creds (break in-container):"
  echo "$HITS" | sed 's/^/    /'
  echo "    -> rewrite each to on-net service DNS or an external URL"
fi

# Rewrite CAMOFOX_URL in the lifted ~/.hermes/.env to the v4 per-agent sidecar.
# v3 ran camofox on http://localhost:9377 (agent == host); v4 runs it as a per-agent
# sidecar (http://abi-camofox-<agent>:9377) on the hermes network. hermes loads
# ~/.hermes/.env with load_dotenv(..., override=True) at startup (hermes_cli/env_loader.py,
# also cron/scheduler.py), so a stale CAMOFOX_URL=localhost in the lifted .env OVERRIDES
# the correct value the compose env sets — the gateway then resolves container-self and
# every browser_navigate fails ("Cannot connect to Camofox at http://localhost:9377").
# Rewrite deterministically here so the cutover is correct no matter how .env is loaded.
# Note: /proc/<pid>/environ shows the fork-time (compose) value, NOT runtime os.environ
# mutations from override=True — so the bug is invisible there; trust this rewrite.
SIDEFOX="http://abi-camofox-${AGENT}:9377"
docker run --rm -u 0:0 -e OWN="$OWN" -e SIDEFOX="$SIDEFOX" -v "${VOL}:/opt/data" "$HELPER" sh -c '
  F=/opt/data/.env
  [ -f "$F" ] || exit 0
  if grep -q "^CAMOFOX_URL=" "$F"; then
    sed -i "s|^CAMOFOX_URL=.*|CAMOFOX_URL=$SIDEFOX|" "$F"
    echo "abi-v4-lift: rewrote .env CAMOFOX_URL -> $SIDEFOX"
  fi
  chown "$OWN" "$F" 2>/dev/null || true
'
# Audit: warn about any OTHER localhost/127.0.0.1 endpoints still in the lifted .env
# (e.g. KANBOARD_URL=http://127.0.0.1:8095). Same override=True trap as CAMOFOX_URL —
# these win over compose env and break in-container. Deployment-specific, so only warn.
ENVHITS=$(docker run --rm -v "${VOL}:/opt/data" "$HELPER" \
  sh -c 'grep -nE "localhost|127\.0\.0\.1" /opt/data/.env 2>/dev/null || true')
if [ -n "$ENVHITS" ]; then
  echo "abi-v4-lift: ⚠️  localhost endpoints remain in lifted .env (override compose env; break in-container):"
  echo "$ENVHITS" | sed 's/^/    /'
  echo "    -> rewrite each to on-net service DNS or an external URL (e.g. KANBOARD_URL=http://opteia-kanboard:80)"
fi

echo "abi-v4-lift: done. Volume ${VOL} contents:"
docker run --rm -v "${VOL}:/opt/data" "$HELPER" \
  sh -c 'du -sh /opt/data 2>/dev/null; echo "---"; ls -A /opt/data'
