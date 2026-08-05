# v4/ — host-side fleet tooling for the ABI v4 container stack

These scripts run **on the box** (as a sudo-capable user), NOT inside the image.
They manage the **`v4` compose project** — the per-agent containers
(`docker-compose.abi-v4.yml`, image set by `ABI_IMAGE` in `.env`).

> Scope note: `abi-update.sh` (elsewhere in the repo) manages the **abi-memory
> api/db stack** (`docker-compose.abi-api.yml`). It does **not** touch the agent
> containers — which is why old `abi-agent:vX` image tags accumulated on `.19`
> until `abi-v4-update.sh` was added. Keep agent rollouts on THIS tooling.

## Canonical on-box path

```
/opt/abi-tools/v4/
├── docker-compose.abi-v4.yml      # (copied from the repo root)
├── .env                           # ABI_IMAGE=<registry>:<tag>  (+ per-agent env)
├── abi-v4-lift.sh
└── abi-v4-update.sh
```

Deploy a fresh copy to a box:

```sh
scp -r v4/ <box>:/tmp/v4-deploy
ssh <box> 'sudo cp -r /tmp/v4-deploy/* /opt/abi-tools/v4/ && sudo chmod +x /opt/abi-tools/v4/*.sh'
```

## The two scripts

### `abi-v4-lift.sh` — v3 → v4 state migration (run once per agent, at cutover)

Lifts a bare-metal v3 agent's `/home/<agent>/.hermes` into the v4 named volume
`abi-hermes-<agent>` (mounted at `/opt/data` = `HERMES_HOME`), chowned to the
agent uid:gid. Also lifts the sibling `workspace/agent/credentials/` subtrees and
`~/.ssh` (without which agents lose SSH access post-cutover). Rewrites the lifted
`.env` `CAMOFOX_URL` to the per-agent sidecar (the `load_dotenv(override=True)`
trap — see the cred-migration notes). Idempotent + re-runnable.

```sh
sudo abi-v4-lift.sh <agent> [uid:gid] [--fresh] [--with-local]
```

`.local` (v3 pip --user) is **not** copied by default — v4 runs off the image
venv, and `.local` only bloats the volume (it pushed `.19` to 100% disk once).
Pass `--with-local` only if a specific v3 user-install is known to be needed.

### `abi-v4-update.sh` — roll the fleet to a new image + prune the old tag

Safe, verified fleet rollout: pulls the new image, bumps `ABI_IMAGE`, recreates
the fleet, waits for **all** agents healthy, then prunes the previous tag **only
if nothing still runs it**. **Rolls back** (revert `.env` + restart) if health
fails, so a bad image never leaves the fleet down. Refuses `latest` as target or
current (ambiguous). Idempotent no-op if already on the tag.

```sh
sudo abi-v4-update.sh <new-tag>     # e.g. v4.2.1
```

## Out of scope (Opteia-internal, NOT shipped here)

The zero-LLM Kanboard cron scripts (`kanban_poll.py` / `kanban_trigger.py` /
`weekly-netbird-update-kanboard-task.py`) and the `abi-v4-deploy-cron-scripts.sh`
that ships them are **Opteia-internal operations tooling** — they target Opteia's
own Kanboard and NetBird/OPNsense infra, which no customer box has. They stay
box-local on Opteia boxes (e.g. `.19 /opt/abi-tools/v4/`), not in this repo.

## The standard-box Kanboard service (SHIPPED here — a default service)

Every ABI box ships **Kanboard + Passbolt as default services** (one image, one set
of services). The Kanboard *service* compose is shipped here —
`../docker-compose.kanboard.yml` (repo root, next to `docker-compose.abi-v4.yml`).
It is the **stripped, agent-local** variant: just `kanboard-db-init` + the
`opteia-kanboard` service, shared Postgres (the box's `abi-memory-db`, separate
`kanboard` DB), `REVERSE_PROXY_AUTH=false`, loopback web port `127.0.0.1:8095:80`.
The Opteia-internal CF-Access proxy variant lives in `docker-compose.abi-api.yml`'s
`kanban` profile (not for customer boxes).

Copy-per-box, to its own dir (NOT `/opt/abi-tools/v4/`):

```sh
ssh <box> 'sudo mkdir -p /opt/kanboard'
scp docker-compose.kanboard.yml <box>:/opt/kanboard/docker-compose.yml
# .env: ABI_DB_USER + ABI_DB_PASSWORD (from the box's infra docker.env)
# stock dirs: sudo mkdir -p /opt/kanboard/{data,plugins} && sudo chown -R 33:33 /opt/kanboard/{data,plugins}
```

The agent wires to it via `KANBOARD_URL=http://opteia-kanboard:80` + `KANBOARD_USER` /
`KANBOARD_TOKEN` + `HERMES_KANBAN_DISPATCH_IN_GATEWAY=false` in the agent compose
(`docker-compose.abi-v4.yml`), and the zero-LLM `kanban_poll.py` cron wakes it on card
changes (cron script shipped separately — see "Out of scope" above; lifted to
`/opt/data/scripts/`). Full deploy + agent-user/token steps: cutover runbook Phase 3b.

The per-box Passbolt vault (`provision-vault.sh`, in `vault/`) is the other default
service — also shipped here.
