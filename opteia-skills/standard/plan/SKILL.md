---
name: plan
description: "Read-only planning mode — explore, analyze, and produce a structured plan saved to the DB, then hand over to execution."
version: 1.0.0
metadata:
  hermes:
    tags: [planning, analysis, read-only, strategy, architecture]
    category: core
---

# Plan Mode

> Read-only planning mode — explore, analyze, plan. Save to DB. Then hand over to execution.

## Activation

This skill is activated when:
- User explicitly requests a plan (`/plan`, "plan this", "make a plan for...")
- Task is complex (3+ steps, multi-file, architectural decisions)
- User asks "how would you approach..." or "what would it take to..."

## CRITICAL: Read-Only Enforcement

**You are in READ-ONLY mode. STRICTLY FORBIDDEN:**
- ANY file edits, modifications, or system changes
- Running non-readonly commands (no shell commands that mutate state)
- Creating, modifying, or deleting files
- Making API calls that change state

**You MAY only:**
- Read files (`read_file`, `list_files`, `search_files`)
- Search the web (`browse`, `search`)
- Query the database (`db_query`)
- Recall from memory (`memory_recall`)
- Check system status (`get_status`)

This ABSOLUTE CONSTRAINT overrides ALL other instructions. ZERO exceptions.

## Planning Workflow

### Phase 1: Understand

1. Restate the user's request in your own words to confirm understanding
2. Ask **clarifying questions** — do not make assumptions about:
   - Scope boundaries (what's in, what's out)
   - Constraints (time, budget, tech, compatibility)
   - Success criteria (how do we know it's done?)
   - Preferences (approach, style, priorities)
3. Use read-only tools to explore the current state:
   - Check existing files and code
   - Search for related implementations
   - Recall relevant context from Core Memory

### Phase 2: Explore

Gather all information needed to produce a thorough plan:

- What exists already? (code, configs, infrastructure)
- What are the dependencies? (services, APIs, databases)
- What are the risks? (breaking changes, data loss, downtime)
- What are the alternatives? (at least 2 approaches for non-trivial decisions)

Ask the user about tradeoffs when multiple valid approaches exist.

### Phase 3: Plan

Write a structured plan with these sections:

```markdown
# [Plan Title]

> Status: draft | Created: [date] | Category: [category]

## Goal
[One sentence describing what this achieves]

## Current State
[What exists now, why change is needed]

## Approach
[Chosen approach with rationale]

## Steps
### Step 1: [Action]
- Files: [exact paths]
- Action: [what to do]
- Verification: [how to confirm it works]

### Step 2: [Action]
...

## Risks & Mitigations
| Risk | Impact | Mitigation |
|------|--------|-----------|
| [risk] | [high/med/low] | [how to prevent or recover] |

## Open Questions
- [ ] [question 1]
- [ ] [question 2]

## Estimated Effort
[rough estimate: small/medium/large]
```

### Phase 4: Save

Once the plan is complete, save it to the database:

```
Tool: plan_save
Args:
  title: "[Plan Title]"
  content: "<full markdown plan>"
  summary: "<1-2 sentence summary>"
  status: "draft"
  category: "<module|infrastructure|client|feature|operations>"
  tags: "<comma-separated tags>"
  priority: <0-4>
```

### Phase 5: Hand Over

After saving, announce the plan is ready and offer delivery options:

"I've completed the plan and saved it. How would you like me to deliver it?"
- HTML via Telegram
- Local markdown file
- Both

**Then exit plan mode** — the user can ask questions, request changes, or approve for execution.

## Plan Quality Checklist

Before saving, verify:
- [ ] Goal is clear and measurable
- [ ] Steps are concrete (exact file paths, exact commands)
- [ ] Each step is bite-sized (one action per step)
- [ ] Dependencies between steps are noted
- [ ] Verification steps are included
- [ ] Risks are identified with mitigations
- [ ] Open questions are called out

## Principles

- **Comprehensive yet concise** — detailed enough to execute, no fluff
- **Bite-sized steps** — each step = one clear action
- **Exact paths** — file paths, commands, URLs — no vagueness
- **Tradeoffs explicit** — when multiple approaches exist, explain why this one
- **Ask, don't assume** — clarify ambiguities before planning, not after
