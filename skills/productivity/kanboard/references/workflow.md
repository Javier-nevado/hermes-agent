# Kanboard workflow conventions

Reference for working the board consistently across the team.

## Columns (workflow order)

| Column | Meaning | Who acts |
|--------|---------|----------|
| **Backlog** | Queued, not ready | Operator/orchestrator stages work here |
| **To Do** | Assigned + ready — **the autonomous wake trigger** | The assignee picks it up |
| **In Progress** | Being worked right now | The assignee |
| **Review** | Done, awaiting a human/orchestrator decision | The reviewer |
| **Done** | Complete | — |

A card lands in **To Do** with an `owner_id` = the responsible agent. That is the
signal to act. Move it to **In Progress** immediately on pickup (so a concurrent
poll doesn't re-trigger), then to **Done** with a completion comment.

## Assignment rules

- Every card has an `owner_id` = the responsible agent. You only see/act on cards
  where `owner_id` = you (the client filters `list-mine` by your own id via
  `getMe`).
- An `owner_id` must be a **project member** — the board admin adds agents to the
  project before they can be assigned. If assignment silently fails, membership
  is the usual cause.
- **Orchestrator vs worker:** an orchestrator agent (or a human) creates/delegates
  cards; worker agents execute their assigned cards. Don't move cards you don't own.

## Comments

- **Completion comment** when moving → Done: what you did + the key result (+ any
  file paths/links). This is the handoff — make it self-contained.
- **Block comment** when moving → Review: what you need, what's unclear, what
  decision is required.
- Comments are authored as **you** (the client passes your own user id). Keep
  titles short and action-oriented.

## Optional: autonomous zero-LLM poll

A deploy can make the board self-driving: a lightweight cron checks each agent's
**To Do** column on a schedule with **no LLM cost**, and only wakes that agent
(via its OpenAI-compatible API endpoint) when it has work. This skill is how the
woken agent then works its cards.

The poll/trigger scripts are **deploy-side runtime infrastructure** (per-agent
`.env`, API ports/keys, cron registration) — they are NOT part of this skill.
Setup outline for an operator:

1. Provision each agent: a Kanboard user + API token + project membership, and a
   per-agent `kanban.json` (`auth_user`/`auth_pass`/`project_id`; the board URL
   defaults to the local Kanboard — the same file this skill reads).
2. A `no_agent` cron script that, for the agent, calls `getMe` + `getAllTasks`,
   filters To Do + `owner_id` == self, and on a hit POSTs a wake-up turn to the
   agent's API (`http://127.0.0.1:<port>/v1/chat/completions`). Empty board → exit
   silent (free). A per-agent lock coalesces overlapping polls.
3. The woken agent loads this skill and works its cards To Do → Done.

Idle polls cost nothing; a turn fires only when there's real work assigned.
