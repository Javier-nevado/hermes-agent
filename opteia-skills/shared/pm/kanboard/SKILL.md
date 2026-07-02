---
name: kanboard
description: "Work tasks on the shared Kanboard board. Poll your To Do column, execute each task, move it through the workflow, and comment with results. The ABI task backbone for human→agent work."
version: 1.1.0
tags: [kanboard, tasks, project-management, backbone]
---

# Kanboard — Your Task Backbone

Kanboard is the shared board where humans assign structured work to you. Each card is a discrete, reviewable task with history. This is your primary intake for assigned work (it complements ad-hoc Telegram/Teams requests).

You talk to Kanboard with a small client script via `execute_code`. All commands print JSON.

```bash
python3 /opt/abi-tools/skills/pm/kanboard/kanboard_client.py list-mine [--column "To Do"]
python3 /opt/abi-tools/skills/pm/kanboard/kanboard_client.py get <task_id>
python3 /opt/abi-tools/skills/pm/kanboard/kanboard_client.py move <task_id> <column>
python3 /opt/abi-tools/skills/pm/kanboard/kanboard_client.py comment <task_id> <comment>
python3 /opt/abi-tools/skills/pm/kanboard/kanboard_client.py complete <task_id> [comment]
```

Credentials live in `credentials/credentials.env` next to the client (read automatically).

## The board

Project **Opteia Tasks** has five columns, in workflow order:

| Column | Meaning |
|--------|---------|
| **Backlog** | Not yet ready |
| **To Do** | Ready and assigned to you — your intake queue |
| **In Progress** | You are actively working it |
| **Review** | Done, awaiting a human decision / review |
| **Done** | Complete |

A card in **To Do** assigned to you = ready to pick up. The card's description is the instruction.

## How to work the board

1. Run `kanboard_client.py list-mine` (lists cards in **To Do** assigned to you; each has `id`, `title`, full `description`).
2. For each card:
   a. `kanboard_client.py move <id> "In Progress"` — claim it so it isn't re-picked next poll.
   b. Do the work described in `description`. Use your other tools (execute_code, file tools, web) as needed.
   c. `kanboard_client.py complete <id> "<what you did + key result>"`.
3. If `count == 0`, reply with one line saying the board is clear. **Do not manufacture work.**
4. If a task is unclear, blocked, or needs a human decision: `move <id> "Review"` with a `comment` explaining what's needed, then leave it.

## Conventions

- **One card = one outcome.** The completion comment is the handoff — concise: what you did, the key result, any file paths/links.
- Don't leave cards in **In Progress** between polls — finish them or move to **Review**.
- You only see cards **assigned to you**. Cards for other agents/people are not yours to move.
- Always move by column **title** ("To Do", "Done"), never raw ids.

## Notes

- The client handles the Kanboard API quirks (the `createComment` argument order, the per-task swimlane for moves) internally — just call the commands.
- Creating cards, editing columns, and managing users are not self-service actions yet (humans / Major Tom do those). If asked, say so.
