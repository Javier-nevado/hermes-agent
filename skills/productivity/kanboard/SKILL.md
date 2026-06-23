---
name: kanboard
description: "Work tasks on a shared Kanboard board. List your To Do cards, move them through the workflow, and comment with results. Kanboard JSON-RPC CLI; instance-agnostic (you authenticate as yourself)."
version: 1.3.0
author: Opteia
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [kanboard, tasks, project-management, kanban, board, backbone]
---

# Kanboard — Your Task Backbone

Kanboard is a shared board where humans (or an orchestrator agent) assign
structured work to you. Each card is a discrete, reviewable task with history.
This is your primary intake for assigned work — it complements ad-hoc
Telegram/Teams requests.

You talk to Kanboard through a small client script. All commands print JSON.

```bash
CLIENT="${HERMES_HOME:-$HOME/.hermes}/skills/productivity/kanboard/scripts/kanboard_client.py"
python3 "$CLIENT" list-mine [--column "To Do"]
python3 "$CLIENT" get <task_id>
python3 "$CLIENT" move <task_id> <column>
python3 "$CLIENT" comment <task_id> <comment>
python3 "$CLIENT" complete <task_id> [comment]
```

**You authenticate as yourself.** Credentials resolve in this order: `KANBOARD_*`
env vars (in `~/.hermes/.env`), then `$HERMES_HOME/kanban.json`, then
`~/workspace/agent/credentials/kanban.json`. The shared client resolves `$HOME`,
so the same script works for every agent and you only ever act on cards
**assigned to you**. No shared/bundled credentials. If none are configured the
client prints a clear error — ask whoever deploys you to provision a
`kanban.json` (`api_url`, `web_url`, `auth_user`, `auth_pass`=<API token>,
`project_id`).

## The board

A project has columns in workflow order — typically:

| Column | Meaning |
|--------|---------|
| **Backlog** | Not yet ready |
| **To Do** | Ready and assigned to you — your intake queue |
| **In Progress** | You are actively working it |
| **Review** | Done, awaiting a human decision / review |
| **Done** | Complete |

A card in **To Do** assigned to you = ready to pick up. The card's description is
the instruction. (Column titles — not ids — are what you pass to `move`/`complete`;
the client maps them for you.)

## How to work the board

1. `list-mine` — cards in **To Do** assigned to you (each has `id`, `title`, full `description`).
2. For each card:
   a. `move <id> "In Progress"` — claim it so it isn't re-picked.
   b. Do the work in `description` using your other tools as needed.
   c. `complete <id> "<what you did + key result>"`.
3. If `count == 0`, say the board is clear. **Do not manufacture work.**
4. If a task is unclear, blocked, or needs a human decision: `move <id> "Review"`
   with a `comment` explaining what's needed, then leave it.

## Conventions

- **One card = one outcome.** The completion comment is the handoff — concise:
  what you did, the key result, any file paths/links.
- Don't leave cards in **In Progress** between sessions — finish them or move to **Review**.
- You only see cards **assigned to you**. Cards for other agents/people are not yours to move.
- Always move by column **title** ("To Do", "Done"), never raw ids.

## Notes

- The client handles Kanboard API quirks internally (`createComment` argument
  order, the per-task swimlane for moves) — just call the commands.
- Creating cards, editing columns, and managing users are admin actions (done by
  the operator / board admin), not self-service. If asked, say so.
- Optional autonomous mode: a deploy can run a zero-LLM cron that polls your To Do
  column and wakes you only when there's work — see `references/workflow.md`.

See `references/workflow.md` for the full workflow conventions + the autonomous-poll pattern.
