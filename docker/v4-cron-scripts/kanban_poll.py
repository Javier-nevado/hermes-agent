#!/opt/hermes/.venv/bin/python3
"""Zero-LLM Kanboard poll (Hermes no_agent cron) — agent-agnostic + version-independent.

The "eyes" of the kanboard skill: detects work needing the agent's turn and, when
there is any, fires one detached wake to this agent's OpenAI-compatible API. When
the board is clear for the agent it exits 0 with no output (idle = free, no LLM).

This is a richer M1 poll (superset of the original single-intake-column scan):
  - actionable tasks: mine in any `intake` or `active` column, across ALL my projects
    (column roles are classified from titles, so the differing P2/P4/P5 layouts all work)
  - overdue tasks: mine, is_active, date_due in the past
  - new @mentions: state-tracked (high-water comment id) so only genuinely-new
    @mentions of me wake, not replayed history.
  - review-wake: a task I created but no longer own (delegated) that the doer moved
    into a hold/review column → wakes me (the reviewer/delegator) once per entry,
    even if the worker forgot to @mention. review_seen (state) prevents re-waking.
  It builds a richer wake context (counts + the top priorities) and passes it to
  kanban_trigger.py, which composes the wake message.

Version-independence (v3 bare-metal vs v4 container):
  - State/config dir is $HERMES_HOME (v4=/opt/data, v3=/home/<u>/.hermes). Read from
    the gateway env; never hardcode ~ (in v4 $HOME is /opt/data/home, a different
    tree, which is what broke the lifted v3 copy).
  - The inter-agent API server port is API_SERVER_PORT (compose sets 8080 in v4).
    Legacy KANBOARD_API_PORT (a v3 host port) is a v3-only fallback.

Required env / .env vars: KANBOARD_URL, KANBOARD_USER, KANBOARD_TOKEN,
API_SERVER_KEY (+ API_SERVER_PORT, normally provided by the gateway env).
"""
import base64
import datetime as dt
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

# Resolve HERMES_HOME the same way the gateway does: env wins, else ~/.hermes (v3).
_HERMES = os.environ.get("HERMES_HOME") or os.path.expanduser("~/.hermes")
ENV_FILE = os.path.join(_HERMES, ".env")
TRIGGER = str(Path(__file__).with_name("kanban_trigger.py"))
# Lock lives next to THIS script (writable in both layouts) — NOT under $HOME,
# which in v4 points at /opt/data/home (wrong tree).
LOCK = Path(__file__).with_name(".kanban_poll.lock")
LOCK_TTL = 1200  # 20 min — coalesce overlapping polls while a turn is in-flight
# @mention tracking state: highest comment id already seen, so we only wake on NEW
# mentions (not replay history every tick). Lives next to the script.
STATE = Path(__file__).with_name(".kanban_poll.state")
# Only scan comments on tasks modified within this window — bounds the per-tick cost
# (getAllComments is per-task; skipping long-dormant tasks keeps the poll cheap).
MENTION_SCAN_WINDOW = 14 * 86400

# Column-role classification by title (lowercased substring). The first rule that
# matches wins (terminal/hold are checked before active/intake so a 'Done' or
# 'Review' column is never misread). Mirrors kanboard_client.py's title heuristic
# so the poll and the agent's client agree on default layouts (config overrides in
# kanboard.yaml are client-side).
_COLUMN_ROLE_RULES = [
    ("terminal", ["done", "published", "scheduled", "closed", "completed", "archived", "shipped", "cancelled", "rejected"]),
    ("hold",     ["review", "on hold", "on-hold", "blocked", "waiting", "pending", "qa", "test", "testing"]),
    ("active",   ["in progress", "in-progress", "work in progress", "wip", "working", "doing", "drafting", "started", "active", "in-flight", "in flight"]),
    ("intake",   ["to do", "todo", "ready", "queue", "next", "up next", "intake", "triage", "selected", "sprint"]),
    ("backlog",  ["backlog", "icebox", "ideas", "inbox", "new"]),
]


def _column_role(title: str, position: int, n_cols: int) -> str:
    t = title.strip().lower()
    for role, needles in _COLUMN_ROLE_RULES:
        if any(n in t for n in needles):
            return role
    # position-based fallback: first column = backlog, last = terminal, else intake
    if position == 0:
        return "backlog"
    if position == n_cols - 1:
        return "terminal"
    return "intake"


def _ranked(items):
    """Overdue first, then priority desc, then due asc — the top is what to do next."""
    return sorted(items, key=lambda b: (0 if b.get("overdue") else 1, -b.get("priority", 0),
                                        b.get("due") or "9999", b["id"]))


def _load_env(path):
    """Load .env with setdefault so the gateway's inherited env wins (compose
    overrides like KANBOARD_URL=http://opteia-kanboard:80 are not clobbered)."""
    try:
        text = Path(path).read_text()
    except (FileNotFoundError, PermissionError):
        return  # creds may all be present in the inherited env already
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


_load_env(ENV_FILE)
URL = os.environ.get("KANBOARD_URL", "").rstrip("/")
USER = os.environ.get("KANBOARD_USER", "")
TOK = os.environ.get("KANBOARD_TOKEN", "")
if not (URL and USER and TOK):
    sys.exit(0)  # no creds — silent (a misconfigured agent shouldn't spam)
# v4: API_SERVER_PORT (8080, from the gateway env). v3 fallback: KANBOARD_API_PORT.
PORT = os.environ.get("API_SERVER_PORT") or os.environ.get("KANBOARD_API_PORT") or "8080"


def rpc(method, params=None):
    body = {"jsonrpc": "2.0", "id": 1, "method": method}
    if params is not None:
        body["params"] = params
    auth = base64.b64encode(f"{USER}:{TOK}".encode()).decode()
    req = urllib.request.Request(
        URL + "/jsonrpc.php", data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "Authorization": "Basic " + auth},
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.load(r).get("result")
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError):
        sys.exit(0)  # transient/network — silent, retry next tick (don't stack a wake)


me = rpc("getMe", []) or {}
uid = int(me.get("id", 0))
my_name = me.get("name") or me.get("username") or ""
# @mention tokens to match in comment text (username + display name parts, >=2 chars).
mention_tokens = {tok.lower() for tok in {*my_name.split(), me.get("username", "")}
                  if tok and len(tok) >= 2}

now = int(time.time())
actionable = []   # mine, intake/active, open
overdue = []      # mine, open, date_due in the past
mention_candidates = []  # (task_id, project, title) I own/created, modified recently
review = []       # tasks I created (delegated) now sitting in a hold/review column

# Load wake state ONCE, up front: review_seen is needed inside the task loop
# (it detects hold-column transitions → wake the delegator on review);
# last_comment_id is consumed by the @mention scan below. first_run seeds the
# watermarks without firing a historical wake storm.
state = {}
try:
    state = json.loads(STATE.read_text())
except (FileNotFoundError, ValueError, PermissionError):
    pass
last_comment_id = int(state.get("last_comment_id", 0))
first_run = not state
# JSON stringifies int dict keys on round-trip → coerce back, else .get(int_tid)
# would always miss and we'd re-wake on every tick a card sits in Review.
review_seen = {int(k): int(v) for k, v in state.get("review_seen", {}).items()}
new_review_seen = {}                          # rebuilt fresh each tick (no stale growth)

for proj in (rpc("getMyProjects", []) or []):
    pid = int(proj["id"])
    cols = rpc("getColumns", [pid]) or []
    if not cols:
        continue
    cols_sorted = sorted(cols, key=lambda c: c["position"])
    n = len(cols_sorted)
    role_by_col = {int(c["id"]): _column_role(c["title"], i, n) for i, c in enumerate(cols_sorted)}
    col_title = {int(c["id"]): c["title"] for c in cols_sorted}
    for t in (rpc("getAllTasks", [pid]) or []):
        mine = int(t.get("owner_id", 0)) == uid
        created = int(t.get("creator_id", 0)) == uid
        if mine and int(t.get("is_active", 1)):
            role = role_by_col.get(int(t.get("column_id", 0)), "?")
            due_ts = int(t.get("date_due") or 0)
            is_overdue = bool(due_ts and due_ts < now)
            brief = {
                "id": int(t["id"]),
                "project": proj["name"],
                "column": col_title.get(int(t.get("column_id", 0)), "?"),
                "role": role,
                "title": t.get("title", ""),
                "due": dt.datetime.fromtimestamp(due_ts).strftime("%Y-%m-%d") if due_ts else "",
                "priority": int(t.get("priority", 0)),
                "overdue": is_overdue,
            }
            if role in ("intake", "active"):
                actionable.append(brief)
            if is_overdue:
                overdue.append(brief)
        # @mention candidates: tasks I own OR created, modified within the scan
        # window (a new comment bumps date_modification, so this bounds comment calls).
        if (mine or created) and int(t.get("date_modification") or 0) >= now - MENTION_SCAN_WINDOW:
            mention_candidates.append((int(t["id"]), proj["name"], t.get("title", "")))

        # review-wake: a task I created but no longer own (I delegated it) that the
        # doer moved into a hold/review column → I am the reviewer; wake me once per
        # ENTRY into that column. review_seen (last tick) suppresses re-wakes while
        # the card sits in Review; leaving hold re-arms it (new_review_seen is rebuilt
        # fresh, so a card no longer in hold simply drops out → next entry re-wakes).
        # This is the structural fix for the "commented-but-didn't-@mention" stall:
        # the delegator is woken on Review even when the worker forgot to ping.
        if created and not mine:
            rrole = role_by_col.get(int(t.get("column_id", 0)), "?")
            tid = int(t["id"])
            cid = int(t.get("column_id", 0))
            if rrole == "hold":
                new_review_seen[tid] = cid
                if review_seen.get(tid) != cid:
                    review.append({"id": tid, "project": proj["name"],
                                   "column": col_title.get(cid, "?"),
                                   "title": t.get("title", "")})

# --- @mention detection (state-tracked: only NEW mentions wake) ---
mentions = []
max_comment_id = last_comment_id
mention_re = re.compile(r"@ ?(" + "|".join(re.escape(t) for t in mention_tokens) + r")\b") \
    if mention_tokens else None
try:
    for tid, proj, title in mention_candidates:
        for c in (rpc("getAllComments", [tid]) or []):
            cid = int(c.get("id", 0))
            if cid > max_comment_id:
                max_comment_id = cid
            if cid <= last_comment_id or int(c.get("user_id", 0)) == uid:
                continue
            content = (c.get("comment") or "")
            if mention_re and mention_re.search(content.lower()):
                mentions.append({
                    "id": tid, "project": proj, "title": title,
                    "by": c.get("name") or c.get("username") or "?",
                    "comment": content[:200],
                })
except Exception:
    mentions = []  # never let mention scanning break the core actionable/overdue wake

# Persist the high-water mark (mentions) AND the review-seen map so we don't
# replay comments or re-wake on cards still sitting in Review. On the first run
# after deploy (no state) we seed BOTH watermarks WITHOUT waking — avoids a
# historical storm; only genuinely-new mentions/reviews wake from the next tick.
STATE.write_text(json.dumps({"last_comment_id": max_comment_id,
                             "review_seen": new_review_seen}))
if first_run:
    mentions = []
    review = []

# A task can be both actionable and overdue; report each list ranked, de-duped
# across lists is not needed (the trigger presents them distinctly).
context = {
    "me": my_name,
    "counts": {"actionable": len(actionable), "overdue": len(overdue),
               "mentions": len(mentions), "review": len(review)},
    "actionable": _ranked(actionable),
    "overdue": _ranked(overdue),
    "mentions": mentions,
    "review": review,   # already in project order; briefs lack overdue/priority → not ranked
}

if not actionable and not overdue and not mentions and not review:
    # Board clear for me: any prior in-flight trigger has finished. Clear the lock
    # so the next real card fires immediately instead of waiting the TTL.
    try:
        LOCK.unlink()
    except FileNotFoundError:
        pass
    sys.exit(0)  # nothing to do — silent, zero LLM cost

# Race guard: a turn triggered within LOCK_TTL is still in-flight (the card may
# still show in an intake column until the agent moves it). Stay silent, don't
# stack a duplicate. The lock auto-expires by mtime, so a crashed/stalled turn
# is retried on the next poll after the TTL.
if LOCK.exists() and (time.time() - LOCK.stat().st_mtime) < LOCK_TTL:
    sys.exit(0)

LOCK.touch(exist_ok=True)
# fire-and-forget: detached trigger so this cron script returns at once. The
# context is passed as compact JSON in argv (exec-style, no shell quoting issues).
subprocess.Popen(
    [sys.executable, TRIGGER, json.dumps(context, separators=(",", ":")), PORT],
    start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
)
top = ", ".join(f"#{t['id']} [{t['project']}/{t['column']}]" for t in context["actionable"][:3])
parts = [f"{context['counts']['actionable']} actionable", f"{context['counts']['overdue']} overdue"]
if context["counts"]["mentions"]:
    parts.append(f"{context['counts']['mentions']} @mention")
if context["counts"]["review"]:
    parts.append(f"{context['counts']['review']} review")
print(f"kanban poll: {' '.join(parts)} — top: {top} — woke {my_name or 'agent'} on port {PORT}.")
