#!/usr/bin/env python3
"""Self-contained Kanboard JSON-RPC client. Instance-agnostic: no bundled
credentials, no hardcoded URLs/projects. Each agent authenticates as ITSELF.

Credential resolution (first hit wins; ``os.environ`` always takes precedence,
so explicit ``KANBOARD_*`` env vars override the file):
  1. ``KANBOARD_*`` env vars (gateway/cron-loaded ``~/.hermes/.env``).
  2. ``$HERMES_HOME/kanban.json``  — repo-standard skill-creds location.
  3. ``$HOME/workspace/agent/credentials/kanban.json`` — ABI per-agent creds.

The **board URL defaults to the local Kanboard** (``http://127.0.0.1:8095``) —
agents always talk to the Kanboard container running on their own host over the
loopback; the public hostname is for *human* web access only and is never used
by agents. Set ``KANBOARD_URL`` (or a ``web_url``/``api_url`` in the creds file)
only to override that default.

``kanban.json`` shape (URL fields optional — omitted = local default)::
    {"web_url": "http://127.0.0.1:8095",   # optional; defaults to the local Kanboard
     "auth_user": "<username>", "auth_pass": "<API token>",
     "project_id": <int>}

CLI subcommands emit JSON on stdout so the agent can parse results.

Usage:
  kanboard_client.py list-mine [--column "To Do"]
  kanboard_client.py get <task_id>
  kanboard_client.py move <task_id> <column>
  kanboard_client.py comment <task_id> <comment>
  kanboard_client.py complete <task_id> [comment]

API gotchas baked in (Kanboard v1.2.52, positional JSON-RPC):
  createComment(task_id, user_id, content)  — user_id is 2nd, must = token user
  moveTaskPosition(project_id, task_id, column_id, position, swimlane_id)
    — swimlane_id MUST be the task's own (read from getTask); never hardcode.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import urllib.request
from pathlib import Path


def _hermes_home() -> Path:
    """Resolve HERMES_HOME (env var, else ~/.hermes). Mirrors the repo's
    ``skills/productivity/google-workspace/scripts/_hermes_home.py`` pattern."""
    val = os.environ.get("HERMES_HOME", "").strip()
    return Path(val) if val else Path.home() / ".hermes"


# Agents always hit the LOCAL Kanboard (the container on their own host); the
# public hostname is human web-access only. This default removes the need to put
# the URL in every creds file. Override via KANBOARD_URL for a non-standard port.
DEFAULT_LOCAL_URL = "http://127.0.0.1:8095"


def _load_env():
    """Populate KANBOARD_* from a per-agent creds file (env vars win via setdefault)."""
    if os.getenv("KANBOARD_TOKEN"):
        return  # env already complete
    candidates = [
        _hermes_home() / "kanban.json",
        Path.home() / "workspace" / "agent" / "credentials" / "kanban.json",
    ]
    for creds_file in candidates:
        if creds_file.exists():
            d = json.loads(creds_file.read_text(encoding="utf-8"))
            url = d.get("web_url") or str(d.get("api_url", "")).removesuffix("/jsonrpc.php")
            if url:
                os.environ.setdefault("KANBOARD_URL", url.rstrip("/"))
            os.environ.setdefault("KANBOARD_USER", d.get("auth_user", ""))
            os.environ.setdefault("KANBOARD_TOKEN", d.get("auth_pass", ""))
            if d.get("project_id"):
                os.environ.setdefault("KANBOARD_PROJECT_ID", str(d["project_id"]))
            return


_load_env()
URL = (os.getenv("KANBOARD_URL") or DEFAULT_LOCAL_URL).rstrip("/")
USER = os.getenv("KANBOARD_USER", "")
TOKEN = os.getenv("KANBOARD_TOKEN", "")

_seq = [0]


def _die(msg: str):
    raise SystemExit(f"ERROR: {msg}")


def _rpc(method, params=None):
    if not TOKEN:
        _die("KANBOARD_TOKEN not set. Put KANBOARD_* in ~/.hermes/.env or a kanban.json "
             "at $HERMES_HOME/kanban.json (or ~/workspace/agent/credentials/kanban.json).")
    _seq[0] += 1
    body = {"jsonrpc": "2.0", "id": _seq[0], "method": method}
    if params is not None:
        body["params"] = params
    auth = base64.b64encode(f"{USER}:{TOKEN}".encode()).decode()
    req = urllib.request.Request(
        URL + "/jsonrpc.php",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "Authorization": "Basic " + auth},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        resp = json.load(r)
    if "error" in resp:
        _die(f"{method}: {resp['error']}")
    return resp.get("result")


def _project_id() -> int:
    pid = os.getenv("KANBOARD_PROJECT_ID")
    if not pid:
        _die("KANBOARD_PROJECT_ID not set (provide project_id in creds/env).")
    return int(pid)


def _title_to_id(project_id: int) -> dict:
    return {c["title"]: int(c["id"]) for c in (_rpc("getColumns", [project_id]) or [])}


def _id_to_title(project_id: int) -> dict:
    return {int(c["id"]): c["title"] for c in (_rpc("getColumns", [project_id]) or [])}


def cmd_list_mine(args):
    pid = _project_id()
    uid = int(_rpc("getMe", [])["id"])
    cols = _title_to_id(pid)
    column = args.column or "To Do"
    if column not in cols:
        _die(f"unknown column '{column}'. known: {sorted(cols)}")
    target = cols[column]
    out = []
    for t in (_rpc("getAllTasks", [pid]) or []):
        if int(t.get("column_id", 0)) == target and int(t.get("owner_id", 0)) == uid:
            desc = t.get("description", "") or ""
            if not desc:
                full = _rpc("getTask", [int(t["id"])]) or {}
                desc = full.get("description", "")
            out.append({"id": int(t["id"]), "title": t.get("title", ""), "description": desc})
    return {"count": len(out), "column": column, "tasks": out}


def cmd_get(args):
    t = _rpc("getTask", [int(args.task_id)])
    if not t:
        _die(f"task {args.task_id} not found")
    t["column"] = _id_to_title(int(t["project_id"])).get(int(t["column_id"]), t.get("column_id"))
    return {"task": t}


def cmd_move(args):
    t = _rpc("getTask", [int(args.task_id)]) or None
    if not t:
        _die(f"task {args.task_id} not found")
    pid = int(t["project_id"])
    sw = int(t["swimlane_id"])
    cols = _title_to_id(pid)
    if args.column not in cols:
        _die(f"unknown column '{args.column}'. known: {sorted(cols)}")
    ok = _rpc("moveTaskPosition", [pid, int(args.task_id), cols[args.column], 1, sw])
    return {"moved": bool(ok), "task_id": int(args.task_id), "column": args.column}


def cmd_comment(args):
    uid = int(_rpc("getMe", [])["id"])
    cid = _rpc("createComment", [int(args.task_id), uid, args.comment])
    return {"comment_id": cid, "task_id": int(args.task_id)}


def cmd_complete(args):
    t = _rpc("getTask", [int(args.task_id)]) or None
    if not t:
        _die(f"task {args.task_id} not found")
    pid = int(t["project_id"])
    sw = int(t["swimlane_id"])
    cols = _title_to_id(pid)
    if "Done" not in cols:
        _die("no 'Done' column on this project")
    ok = _rpc("moveTaskPosition", [pid, int(args.task_id), cols["Done"], 1, sw])
    cid = None
    if args.comment:
        uid = int(_rpc("getMe", [])["id"])
        cid = _rpc("createComment", [int(args.task_id), uid, args.comment])
    return {"completed": bool(ok), "task_id": int(args.task_id), "comment_id": cid}


def main():
    p = argparse.ArgumentParser(description="Kanboard JSON-RPC client (instance-agnostic).")
    sub = p.add_subparsers(dest="cmd", required=True)
    lm = sub.add_parser("list-mine", help="cards in a column assigned to me (default: To Do)")
    lm.add_argument("--column", default="To Do")
    lm.set_defaults(fn=cmd_list_mine)
    g = sub.add_parser("get", help="full task detail")
    g.add_argument("task_id", type=int)
    g.set_defaults(fn=cmd_get)
    m = sub.add_parser("move", help="move a task to a column (by title)")
    m.add_argument("task_id", type=int)
    m.add_argument("column")
    m.set_defaults(fn=cmd_move)
    c = sub.add_parser("comment", help="add a comment (authored as me)")
    c.add_argument("task_id", type=int)
    c.add_argument("comment")
    c.set_defaults(fn=cmd_comment)
    cp = sub.add_parser("complete", help="move to Done + optional completion comment")
    cp.add_argument("task_id", type=int)
    cp.add_argument("comment", nargs="?", default=None)
    cp.set_defaults(fn=cmd_complete)
    args = p.parse_args()
    if not TOKEN:
        _die(
            "Kanboard credentials not found. Set KANBOARD_TOKEN/KANBOARD_USER/"
            "KANBOARD_PROJECT_ID in ~/.hermes/.env, or create a kanban.json at "
            "$HERMES_HOME/kanban.json (or ~/workspace/agent/credentials/kanban.json) "
            "with auth_user/auth_pass/project_id. The board URL defaults to the "
            f"local Kanboard ({DEFAULT_LOCAL_URL}); set KANBOARD_URL only to override."
        )
    print(json.dumps(args.fn(args), indent=2))


if __name__ == "__main__":
    main()
