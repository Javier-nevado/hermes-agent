# Default skills — what every ABI v4 box ships

Every v4 box ships THREE skill layers. Knowing which is which matters when a skill is
missing or stale — each layer has a different source and update path.

## 1. Upstream Hermes skills — `/opt/hermes/skills/` (image-baked, read-only)

The agent's base competence — the upstream Hermes skill set, baked into the image from
this repo's `skills/` dir (copied by the bulk `COPY . .`). Read-only + root-owned. Loaded
by default; the agent is useful out-of-the-box without anything else.

## 2. Opteia shared skills — `/opt/abi-tools/skills/` (image-baked, read-only)

The Opteia-specific skills, staged into the build by `docker/sync-abi-skills.sh` from the
**separate `abi-skills` repo** (`Javier-nevado/abi-skills`, private) and baked at the exact
path `/opt/abi-tools/skills` (the skill CONTENT references its own scripts by that absolute
path, e.g. `/opt/abi-tools/skills/pm/kanboard/kanboard_client.py`). The `abi-skills` repo is
the single canonical source — this repo NEVER vendors a copy (see `.gitignore: abi-tools-skills/`).
Loaded via `skills.external_dirs` in `cli-config.yaml.example`.

**Full inventory (sync = whole tree, so ALL of these bake into every image):**

| Category | Skills |
|----------|--------|
| `analytics` | webstats |
| `comms` | brevo, camofox-browser, **m365**, recall, twenty |
| `content` | content-ideas, image-tools, linkedin-analytics, opteia-web-deploy, opteia-web-manage, payload-publish, remotion, youtube |
| `core` | file-catalog, memory-backfill, ocr, self-update, skill-library, team-delegation, team-worker, vision |
| `finance` | invoice-ninja |
| `infra` | kanban, npm, opnsense, proxmox |
| `pm` | **kanboard**, plane |
| `security` | **passbolt-secure-credentials** |

**Bold = the customer-standard skills** that pair with the default services: `comms/m365`
(M365 Graph), `pm/kanboard` (Kanboard), `security/passbolt-secure-credentials` (the per-box
Passbolt vault). A customer box that has the default services wired (Phase 3b) can use all
three on day one.

> To change what ships: edit the `abi-skills` repo, then rebuild the image (sync runs at
> build time). Do NOT hand-edit `/opt/abi-tools/skills` on a box — it's read-only + gets
> overwritten on the next image pull.

## 3. Customer-lifted skills — `/opt/data/skills/` (per-box, read-write)

Per-customer skills that ride in the lifted volume (carried over from v3 `~/.hermes/skills/`
by `abi-v4-lift.sh`, or added per-deploy). Read-write, owned by the agent uid. This is where
the **customer M365 skill** (`comms/m365`, the 16-delegated-permission variant — see
[[m365-skill-improvements]]) lives on customer boxes: it is NOT in the hermes-agent repo and
does NOT arrive via the image — it's copied per-deploy into `/opt/data/skills/` and survives
image pulls.

## Quick rules

- **Missing a skill?** Check the layer: `/opt/hermes/skills` (upstream) → `/opt/abi-tools/skills`
  (Opteia, image-baked) → `/opt/data/skills` (customer-lifted). Customer-specific skills
  (e.g. the M365 creds skill) are layer 3, not 2.
- **Stale skill?** Layers 1+2 update on image pull (`abi-v4-update.sh`); layer 3 updates on
  lift or manual copy.
- **Adding a default skill?** Put it in the `abi-skills` repo (it'll bake into every image),
  not in this repo or on individual boxes.
