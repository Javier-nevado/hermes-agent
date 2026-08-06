#!/opt/hermes/.venv/bin/python3
"""Background trigger: POST a kanban-work turn to this agent's OpenAI-compatible API.

Invoked detached by kanban_poll.py so the poll returns immediately. argv:
[context_json, api_port]. Waits for the full turn (long timeout) so the server
doesn't cancel the agent's processing when the originating cron tick ends. Runs
silent (the poll job already reported the wake-up). Inherits API_SERVER_KEY from
the poll's environment (which inherits it from the gateway env — never hardcoded).

context_json carries the poll's richer wake context: counts + the ranked
actionable/overdue tasks (id/project/column/title/due/priority). We compose a
concise, priority-ordered wake message so the agent knows WHAT to work and in what
order, not just that work exists. Falls back to a count-only message if an old poll
passes a bare integer (the two deploy together, but stay robust).
"""
import json
import os
import sys
import urllib.request

# Port precedence: argv (from the poll) > API_SERVER_PORT (v4 gateway env)
# > KANBOARD_API_PORT (v3 host port) > 8080 default.
PORT = (
    sys.argv[2] if len(sys.argv) > 2 else None
) or os.environ.get("API_SERVER_PORT") or os.environ.get("KANBOARD_API_PORT") or "8080"
API = f"http://127.0.0.1:{PORT}/v1/chat/completions"
KEY = os.environ["API_SERVER_KEY"]  # inherited from the gateway env — no hardcoded fallback
MODEL = os.environ.get("KANBOARD_TRIGGER_MODEL", "glm-5.2")


def _compose(argv1: str) -> str:
    try:
        ctx = json.loads(argv1)
    except (json.JSONDecodeError, TypeError):
        ctx = None
    if not isinstance(ctx, dict):
        n = argv1 or "1"
        return (
            f"You have {n} task(s) assigned to you on Kanboard. For each: move it to "
            "your 'In Progress' column, do the work, then complete it with a concise "
            "summary comment. Do not ask for confirmation."
        )

    actionable = ctx.get("actionable", [])
    overdue = ctx.get("overdue", [])
    mentions = ctx.get("mentions", [])
    review = ctx.get("review", [])
    # overdue items that aren't also in actionable still belong on the work list
    act_ids = {t["id"] for t in actionable}
    extra = [t for t in overdue if t["id"] not in act_ids]
    work = actionable + extra  # actionable is already ranked (overdue first)

    lines = []
    na, no, nm, nr = len(actionable), len(overdue), len(mentions), len(review)
    head = f"You have {na} actionable task(s) on Kanboard"
    if no:
        head += f" ({no} overdue)"
    if nm:
        head += f", {nm} new @mention(s)"
    if nr:
        head += f", {nr} awaiting your review"
    head += "."
    if work:
        head += " Work them in this priority order:"
    lines.append(head)

    for i, t in enumerate(work[:8], 1):  # cap the list to keep the wake tight
        bits = [f"#{t['id']}", f"[{t['project']}/{t['column']}]", repr(t.get('title', ''))]
        if t.get("overdue"):
            bits.append("(OVERDUE)")
        elif t.get("due"):
            bits.append(f"(due {t['due']})")
        lines.append(f"{i}. " + " ".join(bits))

    for m in mentions[:5]:
        lines.append(f"@mention on #{m['id']} [{m.get('project','')}] {m.get('title','')!r} "
                     f"by {m.get('by','?')}: {m.get('comment','')!r}")

    # REVIEW items: a worker finished a card you delegated and moved it to Review.
    # You are the reviewer — approve+Done, re-route to another worker, or send back.
    for t in review[:5]:
        lines.append(f"REVIEW #{t['id']} [{t.get('project','')}/{t.get('column','')}] "
                     f"{t.get('title','')!r} — worker finished; review then Done/re-route/send-back.")

    lines.append(
        "For each task: claim it (move to your active column), do the work, then "
        "complete it with a concise summary comment. If blocked, comment why and park "
        "it. Reply to @mentions on the task. For REVIEW items: act on them (Done or "
        "re-route) — don't leave them sitting. Prioritize overdue work first. "
        "Do not ask for confirmation."
    )
    return "\n".join(lines)


msg = _compose(sys.argv[1] if sys.argv[1:] else "")
payload = json.dumps({"model": MODEL, "messages": [{"role": "user", "content": msg}]}).encode()
req = urllib.request.Request(
    API, data=payload,
    headers={"Content-Type": "application/json", "Authorization": "Bearer " + KEY},
)
try:
    urllib.request.urlopen(req, timeout=1800)
except Exception:
    pass
