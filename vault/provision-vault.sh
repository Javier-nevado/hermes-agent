#!/usr/bin/env bash
# =============================================================================
# provision-vault.sh <customer> — stand up a per-customer Passbolt vault.
#
# One command per customer. See plan §Architecture + spec §11.
#   0. resolve Cloudflare zone/account         (SKIPPED in --no-cloudflare mode)
#   1. Cloudflare: named tunnel + ingress + CNAME  →  <customer>.vaults.opteia.com
#      (SKIPPED in --no-cloudflare mode)
#   2. write vault/.env  →  docker compose up -d passbolt passbolt-db
#   2a. read the first-boot server OpenPGP fingerprint  →  VAULT_SERVER_FP
#       (the image keygen creates the key but does NOT wire its fingerprint —
#        without PASSBOLT_GPG_SERVER_KEY_FINGERPRINT, `cake install` aborts)
#   2b. Phase-0 reachability proof (split-horizon: <cust>.vaults resolves LOCAL)
#   3. cake passbolt install (first boot, --no-admin) + register the ABI agent
#   4. generate the agent GPG keypair + complete headless setup → passbolt.json
#
# Runs ON THE CUSTOMER BOX during onboarding (the vault containers live there),
# but uses Opteia's Cloudflare token (env) for the opteia.com zone DNS/tunnel.
# passbolt.json (GPG private key) is the bootstrap secret — chmod 600, never
# committed/logged.
#
# <customer> is baked into APP_FULL_BASE_URL (https://<customer>.vaults.opteia.com)
# at first boot — a ONE-WAY DOOR. Renaming the hostname later = rebrand/re-key/
# re-share (chargable). The script confirms the FQDN before touching anything.
#
# Agent access path (concern #1): the agent reaches the vault over LOCAL http at
# http://<customer>.vaults.opteia.com (docker network alias on opteia-net), NOT via
# the public Cloudflare edge — so it keeps working with Cloudflare down. Humans use
# the optional https path. A Phase-0 check proves the local resolution.
#
# --no-cloudflare: agent-LOCAL-ONLY deploy (the design default). Skips CF + the
# cloudflared service entirely; the headless setup runs INSIDE opteia-net (the host
# can't resolve the docker alias) via a throwaway container that has gpg+requests.
# Humans get NO browser access in this mode (the optional paid HTTPS layer is added
# later by re-running without --no-cloudflare).
#
# External vault mode (#4): --external-vault <url> skips the per-box deploy and
# points the agent at a Passbolt the customer already runs.
#
# Env required (per-box mode, CF mode only):
#   CF_API_TOKEN   — Cloudflare API token (Account:Cloudflare Tunnel:Edit +
#                    Zone:DNS:Edit on opteia.com). NOT required for --no-cloudflare.
# Optional:
#   AGENT_CREDS_DIR — where to drop passbolt.json for the agent (default:
#                    /opt/abi-tools/v4 — adjust per deploy).
#   VAULT_SETUP_IMAGE — image used for the in-net headless setup in --no-cloudflare
#                    mode (default git.opteia.com/opteia/abi-agent:v4.2.1; it has
#                    /usr/bin/gpg + /opt/hermes/.venv with `requests`).
#   ZONE_NAME (default opteia.com), BREVO_SMTP_USER, BREVO_SMTP_KEY
#
# STATUS: validated piecewise on Brian 2026-08-05 (agent-local-only). The CF path
# (steps 0-1) is production-quality but not yet exercised end-to-end on a fresh box.
# =============================================================================
set -euo pipefail

log() { printf '\033[1;34m[provision:%s]\033[0m %s\n' "${CUSTOMER:-?}" "$*"; }
die() { printf '\033[1;31m[provision:%s ERROR]\033[0m %s\n' "${CUSTOMER:-?}" "$*" >&2; exit 1; }

usage() {
  cat <<USAGE
usage:
  $0 <customer>                                      # full per-box provision (CF + agent + human web)
  $0 <customer> --no-cloudflare                      # agent-LOCAL-ONLY (design default; no public path)
  $0 <customer> --external-vault <url> --setup-link <link>   # point at an existing vault (#4)

<customer>   lowercase hostname slug, e.g. brian  ->  <customer>.vaults.opteia.com
             (baked into APP_FULL_BASE_URL at first boot; renaming = chargable rebrand).

flags:
  --no-cloudflare         agent-local-only deploy (skip CF tunnel/CNAME + cloudflared).
                          The agent reaches the vault over LOCAL http via the docker
                          network alias. Humans get NO browser access (add CF later).
  --agent-name <slug>     explicit persona name (overrides SOUL detection). Used
                          directly as <slug>@<vault-fqdn> — a real persona (ailean,
                          atlas, …) is already distinct from any human, so no abi- prefix.
  --soul <path>           path to the agent's SOUL.md. If a persona name is found there
                          it becomes <persona>@<vault-fqdn>; otherwise falls back to
                          abi-<customer>@<vault-fqdn>. (Name precedence: --agent-name > --soul > fallback.)
  --external-vault <url>  use the customer's EXISTING Passbolt (skips Cloudflare +
                          compose + install). Requires --setup-link. Only the agent's
                          own key is registered there; Opteia never sees the customer's key.
  --setup-link <url>      /setup/start/<userId>/<token> link (external mode, or to
                          resume a failed headless setup).
  --yes                   skip the FQDN confirmation prompt.
USAGE
}

CUSTOMER=""
EXTERNAL_VAULT=""
EXTERNAL_SETUP_LINK=""
AGENT_NAME=""
SOUL_PATH=""
ASSUME_YES=0
NO_CLOUDFLARE=0
while [ $# -gt 0 ]; do
  case "$1" in
    --no-cloudflare)   NO_CLOUDFLARE=1; shift ;;
    --external-vault) EXTERNAL_VAULT="${2:-}"; shift 2 ;;
    --setup-link)     EXTERNAL_SETUP_LINK="${2:-}"; shift 2 ;;
    --agent-name)     AGENT_NAME="${2:-}"; shift 2 ;;
    --soul)           SOUL_PATH="${2:-}"; shift 2 ;;
    --yes|-y)         ASSUME_YES=1; shift ;;
    -h|--help)        usage; exit 0 ;;
    --)               shift; break ;;
    -*)               echo "unknown flag: $1" >&2; usage >&2; exit 1 ;;
    *) if [ -z "$CUSTOMER" ]; then CUSTOMER="$1"; else echo "unexpected arg: $1" >&2; usage >&2; exit 1; fi; shift ;;
  esac
done
[ -n "$CUSTOMER" ] || { usage; exit 1; }

# <customer> becomes <customer>.vaults.opteia.com — must be a safe DNS slug.
echo "$CUSTOMER" | grep -qE '^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$' \
  || die "<customer> must be lowercase [a-z0-9-], 1-63 chars, not starting/ending with '-': '$CUSTOMER'"

HERE="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
COMPOSE="$HERE/docker-compose.vault.yml"
VAULT_HOST="${VAULT_HOST:-${CUSTOMER}.vaults.opteia.com}"
ZONE_NAME="${ZONE_NAME:-opteia.com}"
AGENT_CREDS_DIR="${AGENT_CREDS_DIR:-/opt/abi-tools/v4}"
VAULT_SETUP_IMAGE="${VAULT_SETUP_IMAGE:-git.opteia.com/opteia/abi-agent:v4.2.1}"
NET_NAME="${NET_NAME:-hermes-agent_opteia-net}"
CAKE=/usr/share/php/passbolt/bin/cake
# Agent identity on the vault — local-part of the Passbolt username.
# Precedence: --agent-name (explicit persona) > persona parsed from --soul SOUL.md
# > none. A REAL persona name (ailean, atlas, …) is already distinct from any human,
# so it's used directly: <persona>@<vault-fqdn>. With no persona we fall back to
# abi-<customer>@<vault-fqdn> — the 'abi-' prefix marks the generic agent so the
# human doesn't confuse it with their own account in the share UI (mobile truncates
# the username, so 'brian@...' reads as the human Brian; 'abi-brian@...' does not).
# The @<vault-fqdn> domain matches the provisioned vault; humans use their own email.
DISPLAY_SLUG="$CUSTOMER"
AGENT_PERSONA="$AGENT_NAME"                                 # explicit override wins
if [ -z "$AGENT_PERSONA" ] && [ -n "$SOUL_PATH" ]; then     # else try SOUL.md
  AGENT_PERSONA="$(python3 "$HERE/passbolt_agent_name.py" "$SOUL_PATH" 2>/dev/null || true)"
  [ -n "$AGENT_PERSONA" ] && log "persona '$AGENT_PERSONA' detected in $SOUL_PATH"
fi
if [ -n "$AGENT_PERSONA" ]; then
  echo "$AGENT_PERSONA" | grep -qE '^[a-z0-9][a-z0-9-]{0,55}$' \
    || die "agent persona '$AGENT_PERSONA' is not a valid slug (lowercase a-z0-9-, <=56 chars)"
  USER_LOCAL="$AGENT_PERSONA"
  DISPLAY_SLUG="$AGENT_PERSONA"
else
  USER_LOCAL="abi-${CUSTOMER}"
fi
ABI_USER="${USER_LOCAL}@${VAULT_HOST}"

# #4 — external-mode flag validation (early).
if [ -n "$EXTERNAL_VAULT" ]; then
  [ -n "$EXTERNAL_SETUP_LINK" ] || die "--external-vault requires --setup-link <url> (register ${ABI_USER} on the customer's vault, paste the /setup/start link)"
  case "$EXTERNAL_VAULT" in http://*|https://*) : ;; *) die "--external-vault <url> must include the scheme (http:// or https://)" ;; esac
fi

# #3 — confirm the FQDN before touching anything (APP_FULL_BASE_URL is a one-way door).
if [ "$ASSUME_YES" -ne 1 ]; then
  printf '\033[1m[provision:%s]\033[0m About to provision a vault:\n' "$CUSTOMER"
  printf '  hostname (baked into APP_FULL_BASE_URL): \033[1m%s\033[0m\n' "$VAULT_HOST"
  if [ -n "$EXTERNAL_VAULT" ]; then
    printf '  mode: EXTERNAL vault -> %s (no per-box deploy)\n' "$EXTERNAL_VAULT"
  elif [ "$NO_CLOUDFLARE" -eq 1 ]; then
    printf '  mode: agent-LOCAL-ONLY (passbolt + mariadb; NO cloudflared, NO public path)\n'
  else
    printf '  mode: per-box deploy (passbolt + mariadb + cloudflared; public %s)\n' "$VAULT_HOST"
  fi
  printf 'Renaming the hostname later = rebrand/re-key/re-share (chargable). Continue? [y/N] '
  read -r CONFIRM || die "no confirmation (stdin closed) — re-run with --yes"
  case "$CONFIRM" in y|yes|Y|YES) ;; *) echo "aborted."; exit 1 ;; esac
fi

# CF_API_TOKEN only needed for the CF (tunnel + DNS) path.
if [ -z "$EXTERNAL_VAULT" ] && [ "$NO_CLOUDFLARE" -ne 1 ]; then
  : "${CF_API_TOKEN:?CF_API_TOKEN env var is required for CF mode (use --no-cloudflare for agent-local-only)}"
fi
CF="https://api.cloudflare.com/client/v4"
cf() { curl -fsS -H "Authorization: Bearer ${CF_API_TOKEN}" -H "Content-Type: application/json" "$@"; }

# wait for passbolt to serve /healthcheck/status.json (the CE image has NO docker
# HEALTHCHECK, so `compose ps | grep healthy` never matches — poll the app instead,
# from inside the container hitting its own nginx on localhost).
wait_passbolt_ready() {
  log "waiting for passbolt /healthcheck/status.json..."
  local _n
  for _n in $(seq 1 40); do
    if docker compose -f "$COMPOSE" exec -T passbolt \
        php -r '$r=@file_get_contents("http://localhost/healthcheck/status.json"); exit($r===false?1:0);' \
        >/dev/null 2>&1; then
      return 0
    fi
    sleep 5
  done
  die "passbolt did not become ready (/healthcheck/status.json) in ~200s — check 'docker compose -f $COMPOSE logs passbolt'"
}

# Shared helper — emit passbolt.json from a setup link (#1 setup-url vs agent-url split).
#   $1 setup_url  (https://<host> for CF mode; http://<host> for --no-cloudflare)
#   $2 agent_url  (http://<host> LOCAL — the ongoing GPGAuth target)
#   $3 setup_link (/setup/start/<userId>/<token>)
emit_passbolt_json() {
  local setup_url="$1" agent_url="$2" setup_link="$3" out="$HERE/passbolt.json"
  log "generating agent keypair + completing headless setup (setup via $setup_url)..."
  local rc
  if [ "$NO_CLOUDFLARE" -eq 1 ]; then
    # agent-local: the HOST can't resolve the docker alias <cust>.vaults.opteia.com,
    # so run the setup INSIDE opteia-net (where the alias resolves to the container).
    # The abi-agent image has /usr/bin/gpg + /opt/hermes/.venv (requests).
    log "  (no-cloudflare: running setup in-net via $VAULT_SETUP_IMAGE)"
    docker run --rm --network "$NET_NAME" -v "$HERE:/work" \
      --entrypoint /opt/hermes/.venv/bin/python3 "$VAULT_SETUP_IMAGE" \
      /work/passbolt_setup.py \
      --url "$setup_url" --agent-url "$agent_url" \
      --setup-link "$setup_link" --username "$ABI_USER" --out /work/passbolt.json \
      2> "$HERE/passbolt_setup.err"
    rc=$?
  else
    python3 "$HERE/passbolt_setup.py" \
      --url "$setup_url" --agent-url "$agent_url" \
      --setup-link "$setup_link" --username "$ABI_USER" --out "$out" \
      2> "$HERE/passbolt_setup.err"
    rc=$?
  fi
  if [ "$rc" -eq 0 ]; then
    chmod 600 "$out"
    log "passbolt.json written (chmod 600). ongoing agent url = $agent_url"
  else
    log "headless setup did not complete — err in $HERE/passbolt_setup.err."
    log "FALLBACK: complete setup once via the link, then re-run passbolt_setup.py with --import-existing."
    log "  setup link: $setup_link"
    exit 2
  fi
}

# #4 — external mode short-circuit: skip CF/compose/install, just register the agent.
if [ -n "$EXTERNAL_VAULT" ]; then
  log "EXTERNAL vault mode -> $EXTERNAL_VAULT (no per-box deploy)."
  emit_passbolt_json "$EXTERNAL_VAULT" "$EXTERNAL_VAULT" "$EXTERNAL_SETUP_LINK"
  log "place passbolt.json at the agent creds path + set PASSBOLT_CREDS_PATH:"
  printf '  install -m 600 -o <hermes-uid> -g <hermes-gid> %s %s/passbolt.json\n' "$HERE/passbolt.json" "$AGENT_CREDS_DIR"
  printf '  # agent compose env: PASSBOLT_CREDS_PATH=%s/passbolt.json\n' "$AGENT_CREDS_DIR"
  log "record the vault in NetBox (external: $EXTERNAL_VAULT) — standing rule."
  log "DONE (external mode)."
  exit 0
fi

# ─── 0-1. Cloudflare zone + tunnel + ingress + CNAME (SKIPPED in --no-cloudflare) ─
TUNNEL_TOKEN=""
if [ "$NO_CLOUDFLARE" -ne 1 ]; then
  log "resolving Cloudflare zone '$ZONE_NAME'..."
  ZONE_JSON=$(cf "$CF/zones?name=$ZONE_NAME") || die "CF API: cannot list zone (bad token?)"
  ZONE_ID=$(printf '%s' "$ZONE_JSON" | python3 -c 'import sys,json;d=json.load(sys.stdin);r=d["result"];print(r[0]["id"]) if r else exit("zone not found")')
  ACCOUNT_ID=$(printf '%s' "$ZONE_JSON" | python3 -c 'import sys,json;print(json.load(sys.stdin)["result"][0]["account"]["id"])')
  log "zone=$ZONE_ID account=$ACCOUNT_ID"

  log "creating named tunnel 'vault-${CUSTOMER}'..."
  TUNNEL_JSON=$(cf -X POST "$CF/accounts/$ACCOUNT_ID/cfd_tunnel" \
    --data "{\"name\":\"vault-${CUSTOMER}\",\"tunnel_secret\":\"$(openssl rand -hex 32)\",\"config_src\":\"cloudflare\"}") \
    || die "CF: tunnel create failed"
  TUNNEL_ID=$(printf '%s' "$TUNNEL_JSON" | python3 -c 'import sys,json;print(json.load(sys.stdin)["result"]["id"])')
  TUNNEL_TOKEN=$(printf '%s' "$TUNNEL_JSON" | python3 -c 'import sys,json;print(json.load(sys.stdin)["result"]["token"])')

  log "configuring ingress $VAULT_HOST -> http://passbolt:80..."
  cf -X PUT "$CF/accounts/$ACCOUNT_ID/cfd_tunnel/$TUNNEL_ID/configurations" \
    --data "{\"config\":{\"ingress\":[{\"hostname\":\"$VAULT_HOST\",\"service\":\"http://passbolt:80\"},{\"service\":\"http_status:404\"}]}}" >/dev/null \
    || die "CF: ingress config failed"

  log "creating CNAME $VAULT_HOST -> ${TUNNEL_ID}.cfargotunnel.com..."
  cf -X POST "$CF/zones/$ZONE_ID/dns_records" \
    --data "{\"type\":\"CNAME\",\"name\":\"$VAULT_HOST\",\"content\":\"${TUNNEL_ID}.cfargotunnel.com\",\"proxied\":true}" >/dev/null \
    || die "CF: CNAME create failed (may already exist — check dashboard)"
fi

# ─── 2. .env + compose up ─────────────────────────────────────────────────────
VAULT_DB_ROOT="$(openssl rand -base64 32 | tr -d '/+=' | head -c 32)"
VAULT_DB_PASS="$(openssl rand -base64 32 | tr -d '/+=' | head -c 32)"
log "writing $HERE/.env (secrets — chmod 600)..."
umask 077
cat > "$HERE/.env" <<EOF
VAULT_HOST=$VAULT_HOST
VAULT_DB_ROOT=$VAULT_DB_ROOT
VAULT_DB_PASS=$VAULT_DB_PASS
CLOUDFLARED_TOKEN=$TUNNEL_TOKEN
BREVO_SMTP_USER=${BREVO_SMTP_USER:-}
BREVO_SMTP_KEY=${BREVO_SMTP_KEY:-}
EOF
chmod 600 "$HERE/.env"

log "docker compose up -d passbolt passbolt-db (cloudflared starts later in CF mode)..."
docker compose -f "$COMPOSE" up -d passbolt passbolt-db
wait_passbolt_ready

# ─── 2a. server OpenPGP fingerprint → VAULT_SERVER_FP (restart-safe) ───────────
# The image keygen creates the server keypair in GNUPGHOME on first boot but does
# NOT export its fingerprint → cake install aborts "The server OpenPGP key is not
# set". default.php reads PASSBOLT_GPG_SERVER_KEY_FINGERPRINT via env() at runtime,
# so we read the generated fp, persist it to .env, and recreate passbolt so the env
# is baked in for every future restart.
log "reading the first-boot server OpenPGP fingerprint..."
SERVER_FP="$(docker compose -f "$COMPOSE" exec -T passbolt \
  sh -c 'GNUPGHOME=/var/lib/passbolt/.gnupg gpg --list-secret-keys --with-colons 2>/dev/null | grep "^fpr:" | head -1 | cut -d: -f10')"
[ -n "$SERVER_FP" ] || die "no server key fingerprint found — first-boot keygen may have failed (check passbolt logs)"
log "server fingerprint: ${SERVER_FP:0:12}… ; persisting VAULT_SERVER_FP + recreating passbolt"
echo "VAULT_SERVER_FP=$SERVER_FP" >> "$HERE/.env"
docker compose -f "$COMPOSE" up -d passbolt >/dev/null   # recreate with the fp env baked in
wait_passbolt_ready

# (CF mode) now that .env carries the tunnel token, start cloudflared for human access.
if [ "$NO_CLOUDFLARE" -ne 1 ]; then
  log "starting cloudflared (human HTTPS path)..."
  docker compose -f "$COMPOSE" up -d cloudflared
fi

# ─── 2b. Phase-0 reachability proof (concern #1 split-horizon) ─────────────────
# Prove the AGENT path resolves LOCALLY on opteia-net (a docker IP), not out to
# the Cloudflare edge. <cust>.vaults.opteia.com is a real public CNAME (CF mode),
# so without the network alias docker DNS would forward it externally -> silent
# Cloudflare routing + the agent breaks when Cloudflare is down. Requires opteia-net
# (owned by the agent stack) to already exist.
log "Phase-0: proving agent local-http path on $NET_NAME..."
docker run --rm --network "$NET_NAME" -i -e VAULT_HOST="$VAULT_HOST" \
  python:3-slim python3 - <<'PY' || die "Phase-0 local-http proof FAILED — $VAULT_HOST did not resolve to a docker-local IP on $NET_NAME. The agent would route via Cloudflare. Check the opteia-net alias in docker-compose.vault.yml + that the agent stack (which owns $NET_NAME) is already up."
import os, socket, sys
h = os.environ["VAULT_HOST"]
try:
    ip = socket.gethostbyname(h)
except OSError as e:
    print(f"RESOLVE FAIL: {h} not resolvable: {e}"); sys.exit(1)
if ip.startswith(("172.", "10.", "192.168.")):
    print(f"ok: {h} -> {ip} (docker-local — agent path is resilient to Cloudflare outages)")
else:
    print(f"SPLIT-HORIZON FAIL: {h} -> {ip} is not a docker IP (routes via Cloudflare)"); sys.exit(1)
PY

# ─── 3. install + register the ABI agent user ──────────────────────────────────
# Run cake AS www-data via `exec -u` (the old `su -m -c … -s www-data` form was a
# BUG: -s sets the shell, so it died "failed to execute www-data").
PB=(docker compose -f "$COMPOSE" exec -T -u www-data passbolt)

log "cake passbolt install (first boot, --no-admin)..."
# --no-admin -q is the correct non-interactive form for passbolt CE v4/v5
# (older `--no-interactive` is rejected: "Unknown option"). Idempotent: if the
# entrypoint already installed on the recreate above, this is a no-op.
"${PB[@]}" "$CAKE" passbolt install --no-admin -q \
  || log "WARN: install returned non-zero (may already be installed) — verifying schema..."
# gate: schema must be present
TABLES="$(docker compose -f "$COMPOSE" exec -T passbolt \
  php -r 'try{$p=new PDO("mysql:host=passbolt-db;dbname=passbolt","passbolt",getenv("DATASOURCES_DEFAULT_PASSWORD"));$n=$p->query("SHOW TABLES")->rowCount();echo $n;}catch(\Throwable $e){echo "0";}')"
[ "${TABLES:-0}" -gt 10 ] || die "cake install did not create the schema (tables=$TABLES) — check .install + passbolt logs"

log "registering agent user $ABI_USER (display: 'ABI Agent $DISPLAY_SLUG')..."
REGISTER_OUT=$("${PB[@]}" "$CAKE" passbolt register_user -u "$ABI_USER" -f "ABI Agent" -l "$DISPLAY_SLUG" -r user 2>&1) \
  || die "cake register_user failed"
SETUP_LINK=$(printf '%s' "$REGISTER_OUT" | grep -oE 'https://[^ ]+/setup/start/[A-Za-z0-9/-]+' | head -1)
ABI_USER_ID=$(printf '%s' "$REGISTER_OUT" | grep -oE 'user/[0-9a-f-]{36}' | head -1 | sed 's|user/||')
[ -n "$SETUP_LINK" ] || die "no setup link in register_user output — inspect:\n$REGISTER_OUT"
log "setup link captured; user_id=${ABI_USER_ID:-<parse-from-setup-step>}"

# ─── 4. agent GPG keypair + headless setup → passbolt.json ─────────────────────
# Setup runs over the PUBLIC https url in CF mode (the setup link lives there);
# in --no-cloudflare mode it runs over LOCAL http (the alias only resolves in-net).
# The agent reads creds over LOCAL http thereafter (concern #1 — resilient to CF).
if [ "$NO_CLOUDFLARE" -eq 1 ]; then
  emit_passbolt_json "http://$VAULT_HOST" "http://$VAULT_HOST" "$SETUP_LINK"
else
  emit_passbolt_json "https://$VAULT_HOST" "http://$VAULT_HOST" "$SETUP_LINK"
fi

# ─── 5. next steps ─────────────────────────────────────────────────────────────
if [ "$NO_CLOUDFLARE" -eq 1 ]; then
  log "vault live (agent-local-only: no public path). Add CF later by re-running without --no-cloudflare."
else
  log "vault live at https://$VAULT_HOST"
fi
log "place passbolt.json at the agent creds path + set PASSBOLT_CREDS_PATH:"
printf '  docker cp %s/passbolt.json <agent-container>:/opt/data/credentials/passbolt.json\n' "$HERE"
printf '  # then chown <hermes-uid>:<hermes-gid> + chmod 600, set PASSBOLT_CREDS_PATH,\n'
printf '  # restart the agent (cont-init 04 imports the key -> /opt/data/.gnupg).\n'
log "record the vault in NetBox (service + hostname) — standing rule."
log "DONE."
