# Opteia Kanboard (ABI task board) — opt-in service

Kanboard is an **opt-in** service in the ABI stack, enabled per-VM with a Docker
Compose profile. It runs behind nginx + a Cloudflare Access JWT verifier.

It shares the existing `abi-db` Postgres (a separate `kanboard` database, created
idempotently by the `kanboard-db-init` one-shot container on first start).

## Files (all customer-portable, no per-customer secrets)
- `nginx.conf` — reverse proxy + CF Access `auth_request` gate.

## Configuration (env-driven — no config.php)
Kanboard is configured entirely via environment variables set on the `kanboard`
service in `docker-compose.abi-api.yml`. The image declares them in
`/etc/php84/php-fpm.d/env.conf`, which passes them to the fpm workers. The DB
connection reuses the shared `abi-db` credentials (`ABI_DB_USER` / `ABI_DB_PASSWORD`),
and `DB_RUN_MIGRATIONS=true` makes kanboard auto-install + upgrade its own schema —
so there is **no manual SQL import** and **no `config.php` to maintain**. The
reverse-proxy (CF Access) auth is likewise env-driven (`REVERSE_PROXY_AUTH`,
`REVERSE_PROXY_USER_HEADER`).
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
