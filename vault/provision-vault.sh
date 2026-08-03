#!/usr/bin/env bash
# =============================================================================
# provision-vault.sh <customer> — stand up a per-customer Passbolt vault.
#
# One command per customer. See plan §Architecture + spec §11.
#   1. Cloudflare API (opteia.com zone): named tunnel + token + ingress +
#      CNAME  →  <customer>.vaults.opteia.com
#   2. write vault/.env  →  docker compose up -d
#   3. cake passbolt install (first boot) + register the ABI agent user
#   4. generate the agent GPG keypair + complete headless setup → passbolt.json
#
# Runs OPTEIA-SIDE during onboarding (NOT deployed to the customer box). The
# Cloudflare token is Opteia's (its own zone). passbolt.json (GPG private key)
# is the bootstrap secret — chmod 600, never committed/logged.
#
# Env required:
#   CF_API_TOKEN   — Cloudflare API token (Account:Cloudflare Tunnel:Edit +
#                    Zone:DNS:Edit on opteia.com). Opteia-side only.
#   AGENT_CREDS_DIR — where to drop passbolt.json for the agent (default:
#                    /opt/abi-tools/v4 — adjust per deploy).
# Optional:
#   ZONE_NAME (default opteia.com), BREVO_SMTP_USER, BREVO_SMTP_KEY
#
# STATUS: steps 1-3 are production-quality. Step 4 (headless setup) is written
# from the Passbolt setup protocol and MUST be validated against the central
# passbolt.opteia.com instance (Phase 0) before the first partner migration.
# =============================================================================
set -euo pipefail

CUSTOMER="${1:-}"
[ -n "$CUSTOMER" ] || { echo "usage: $0 <customer>   (e.g. brian)"; exit 1; }

HERE="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
COMPOSE="$HERE/docker-compose.vault.yml"
VAULT_HOST="${VAULT_HOST:-${CUSTOMER}.vaults.opteia.com}"
ZONE_NAME="${ZONE_NAME:-opteia.com}"
AGENT_CREDS_DIR="${AGENT_CREDS_DIR:-/opt/abi-tools/v4}"

: "${CF_API_TOKEN:?CF_API_TOKEN env var is required (Opteia-side Cloudflare token)}"
CF="https://api.cloudflare.com/client/v4"
cf() { curl -fsS -H "Authorization: Bearer ${CF_API_TOKEN}" -H "Content-Type: application/json" "$@"; }

log() { printf '\033[1;34m[provision:%s]\033[0m %s\n' "$CUSTOMER" "$*"; }
die() { printf '\033[1;31m[provision:%s ERROR]\033[0m %s\n' "$CUSTOMER" "$*" >&2; exit 1; }

# ─── 0. resolve Cloudflare zone + account ──────────────────────────────────
log "resolving Cloudflare zone '$ZONE_NAME'..."
ZONE_JSON=$(cf "$CF/zones?name=$ZONE_NAME") || die "CF API: cannot list zone (bad token?)"
ZONE_ID=$(printf '%s' "$ZONE_JSON" | python3 -c 'import sys,json;d=json.load(sys.stdin);r=d["result"];print(r[0]["id"]) if r else exit("zone not found")')
ACCOUNT_ID=$(printf '%s' "$ZONE_JSON" | python3 -c 'import sys,json;print(json.load(sys.stdin)["result"][0]["account"]["id"])')
log "zone=$ZONE_ID account=$ACCOUNT_ID"

# ─── 1. named tunnel + ingress + CNAME ─────────────────────────────────────
log "creating named tunnel '$VAULT_HOST'..."
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

# ─── 2. .env + compose up ──────────────────────────────────────────────────
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

log "docker compose up -d..."
docker compose -f "$COMPOSE" up -d

log "waiting for passbolt to become healthy..."
for i in $(seq 1 60); do
  if docker compose -f "$COMPOSE" ps passbolt | grep -qi healthy; then break; fi
  sleep 5
done
docker compose -f "$COMPOSE" ps passbolt | grep -qi healthy || die "passbolt did not become healthy in 5min"

# ─── 3. install + register the ABI agent user ──────────────────────────────
PB="docker compose -f $COMPOSE exec -T passbolt"
CAKE="su -m -c /usr/share/php/passbolt/bin/cake -s www-data"

log "cake passbolt install (first boot, non-interactive)..."
$PB $CAKE passbolt install --no-interactive --force >/dev/null \
  || die "cake install failed (check DATASOURCES_* + that mariadb is up)"

ABI_USER="abi@${CUSTOMER}.local"
log "registering ABI agent user $ABI_USER..."
REGISTER_OUT=$($PB $CAKE passbolt register_user -u "$ABI_USER" -f ABI -l Agent -r user 2>&1) \
  || die "cake register_user failed"
SETUP_LINK=$(printf '%s' "$REGISTER_OUT" | grep -oE 'https://[^ ]+/setup/start/[A-Za-z0-9]+' | head -1)
ABI_USER_ID=$(printf '%s' "$REGISTER_OUT" | grep -oE 'user/[0-9a-f-]{36}' | head -1 | sed 's|user/||')
[ -n "$SETUP_LINK" ] || die "no setup link in register_user output — inspect:\n$REGISTER_OUT"
log "setup link captured; user_id=${ABI_USER_ID:-<parse-from-setup-step>}"

# ─── 4. agent GPG keypair + headless setup → passbolt.json ─────────────────
log "generating agent keypair + completing headless setup..."
# ⚠️ VALIDATE passbolt_setup.py against the central passbolt.opteia.com (Phase 0)
#    before the first partner migration. Falls back to printing the setup link.
PASSBOLT_JSON="$HERE/passbolt.json"
if python3 "$HERE/passbolt_setup.py" \
      --url "https://$VAULT_HOST" \
      --setup-link "$SETUP_LINK" \
      --username "$ABI_USER" \
      --out "$PASSBOLT_JSON" 2> "$HERE/passbolt_setup.err"; then
  chmod 600 "$PASSBOLT_JSON"
  log "passbolt.json written (chmod 600)."
else
  log "headless setup did not complete — err in $HERE/passbolt_setup.err."
  log "FALLBACK: complete setup once via the link, then re-run passbolt_setup.py with --import-existing."
  log "  setup link: $SETUP_LINK"
  exit 2
fi

# ─── 5. next steps ─────────────────────────────────────────────────────────
log "vault live at https://$VAULT_HOST"
log "place passbolt.json at the agent creds path + set PASSBOLT_CREDS_PATH:"
printf '  install -m 600 -o <hermes-uid> -g <hermes-gid> %s %s/passbolt.json\n' "$PASSBOLT_JSON" "$AGENT_CREDS_DIR"
printf '  # then in the agent compose env: PASSBOLT_CREDS_PATH=%s/passbolt.json\n' "$AGENT_CREDS_DIR"
log "record the vault in NetBox (service + hostname) — standing rule."
log "DONE."
