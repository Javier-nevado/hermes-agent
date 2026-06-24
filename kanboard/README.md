# Opteia Kanboard (ABI task board) — opt-in service

Kanboard is an **opt-in** service in the ABI stack, enabled per-VM with a Docker
Compose profile. It runs behind nginx + a Cloudflare Access JWT verifier.

It shares the existing `abi-db` Postgres (a separate `kanboard` database, created
idempotently by the `kanboard-db-init` one-shot container on first start).

## Files (all customer-portable, no per-customer secrets)
- `config.php` — DB password read from `ABI_DB_PASSWORD` env (never hardcoded).
- `nginx.conf` — reverse proxy + CF Access `auth_request` gate.
- `cf_access_verifier.py` — PyJWT verifier (bind-mounted into the verifier image).
- `Dockerfile.cfverify` — builds the verifier image with PyJWT pre-installed.
- `plugins/OpteiaSkin/` — Opteia branding skin.
- `docker.env.example` — per-customer variables (CF team/AUD, port, etc.).

## Enable on a VM
Add to the VM's `docker.env` (next to `docker-compose.abi-api.yml`):
```env
COMPOSE_PROFILES=kanban
KANBOARD_CF_TEAM=opteia
KANBOARD_CF_APP_AUD=<your CF Access AUD>
KANBOARD_ENFORCE=false        # flip to true once verified
```
Then `docker compose --env-file docker.env -f docker-compose.abi-api.yml up -d`
brings up `kanboard` + `kanboard-db-init` + `kanboard-cfverify` + `kanboard-proxy`.

`abi-update.sh` reads `COMPOSE_PROFILES` and passes `--profile kanban` through on
updates, so the stack survives self-updates.
