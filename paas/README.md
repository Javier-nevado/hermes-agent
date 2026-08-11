# abi-paas — ABI-managed mini-PaaS (standard service)

A sandboxed sibling container (`abi-paas`) on `opteia-net` where the **sandboxed agent**
deploys + runs customer web apps — dashboards, internal tools, static reports — through a
token-authed management API. **Passbolt/kanboard-style**: the agent box stays hardened,
the agent stays sandboxed, apps are **LAN-only**, and the whole thing is **relocatable**
(on-box now → separate VPS when a customer outgrows it).

See the full design + phased rollout: `~/.claude/plans/prancy-hopping-narwhal.md`.

## What's here

| File | Role |
|------|------|
| `Dockerfile` | Image: nginx + node + python3 + supervisord + SQLite + the mgmt API |
| `entrypoint.sh` | `supervisord -n` as PID 1 (under tini); requires `ABI_PAAS_TOKEN` |
| `supervisord.conf` | Manages nginx + the mgmt API; includes per-app programs (`conf.d/app-*.conf`) |
| `nginx/nginx.conf` | Serves `:80` in-container; includes per-app routes (`/etc/nginx/apps/*.conf`) |
| `mgmt/api.py` | The agent's control surface (stdlib only, bearer-token auth) |
| `examples/mvp-test-node/` | Trivial Node+SQLite app — the Phase-1 verification vehicle |
| `provision-paas.sh` | One-command per-box provisioner (build → `.env` → up → healthcheck → agent wiring) |

Parent level: `../docker-compose.paas.yml` (the service compose — `${LAN_IP}:8090:80` LAN-only).

## Trust model (load-bearing)

- The container **is** the sandbox boundary. The agent's own volume (`abi-hermes-*`:
  memory/DEK/creds) is **never** mounted here.
- A compromised app reaches only the **LAN/SAP** (as the dashboards legitimately do
  today) — never the agent's secrets.
- **No `docker.sock`**, no host access. Apps run as a non-root `app` user.
- **LAN-only**: nginx `:80` is published `${LAN_IP}:8090:80` → the box's LAN interface.
  ZeroTier (Opteia management) cannot reach it. The mgmt API `:7100` has **no host
  port** — only `opteia-net` peers (the agent) reach it.

## Deploy (pilot, on-box on Castor)

```bash
# 1. ship the source to the box (no git pull — tarball/scp only)
scp -r ../../hermes-agent-work/paas            castor:/opt/abi-paas/paas
scp ../../hermes-agent-work/docker-compose.paas.yml castor:/opt/abi-paas/docker-compose.yml

# 2. on the box: build + provision
ssh castor 'cd /opt/abi-paas && sudo bash paas/provision-paas.sh castor --lan-ip 192.168.0.7 --yes'

# 3. apply the printed agent env-wiring (ABI_PAAS_*, LAN_IP) + recreate the agent
#    (the skill ships to /opt/data/skills/infra/app-deploy/SKILL.md for the pilot)
```

Fleet (Phase 4): image built in CI → `git.opteia.com/opteia/abi-paas:<tag>`; skill baked
via `abi-skills`/`sync-abi-skills.sh`; service added to the cutover runbook + `box init-standard`.

## Management API surface (agent-facing, token-authed)

`GET /health` · `GET /apps` · `GET|POST|DELETE /apps/{name}` ·
`POST /apps/{name}/{start|stop|restart|logs}` · `POST /routes/reload`

Deploy body: `{runtime: node|python|static, start, route, port?, env:{}, source:{type:files|tar, ...}}`.
See `mgmt/api.py` + the `infra/app-deploy` skill.
