#!/usr/bin/env python3
"""Self-contained Kanboard JSON-RPC client for the agent poll loop.

Reads credentials from credentials/credentials.env next to this file (falls
back to KANBOARD_* env vars). CLI subcommands emit JSON on stdout so the
agent can parse results.

Usage:
  kanboard_client.py list-mine [--column "To Do"]
  kanboard_client.py get <task_id>
  kanboard_client.py move <task_id> <column>
  kanboard_client.py comment <task_id> <comment>
  kanboard_client.py complete <task_id> [comment]

API gotchas baked in (Kanboard v1.2.52, positional JSON-RPC):
  createComment(task_id, user_id, content)  — user_id is 2nd
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

DEFAULT_URL = "http://127.0.0.1:8095"
DEFAULT_PROJECT_NAME = "Opteia Tasks"


def _load_env():
    """Load credentials from credentials/credentials.env, then os.environ."""
    creds = Path(__file__).resolve().parent / "credentials" / "credentials.env"
    if creds.exists():
        for line in creds.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


_load_env()
URL = os.getenv("KANBOARD_URL", DEFAULT_URL).rstrip("/")
USER = os.getenv("KANBOARD_USER", "")
TOKEN = os.getenv("KANBOARD_TOKEN", "")

_seq = [0]


def _rpc(method, params=None):
    if not TOKEN:
        raise SystemExit("ERROR: KANBOARD_TOKEN not set (credentials/credentials.env or env)")
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
        raise SystemExit(f"ERROR {method}: {resp['error']}")
    return resp.get("result")


def _project_id():
    pid = os.getenv("KANBOARD_PROJECT_ID")
    if pid:
        return int(pid)
    name = os.getenv("KANBOARD_PROJECT_NAME", DEFAULT_PROJECT_NAME)
    proj = _rpc("getProjectByName", [name])
    if not proj:
        raise SystemExit(f"project not found: {name}")
    return int(proj["id"])


def _title_to_id(project_id):
    return {c["title"]: int(c["id"]) for c in (_rpc("getColumns", [project_id]) or [])}


def _id_to_title(project_id):
    return {int(c["id"]): c["title"] for c in (_rpc("getColumns", [project_id]) or [])}


def cmd_list_mine(args):
    pid = _project_id()
    uid = int(_rpc("getMe", [])["id"])
    cols = _title_to_id(pid)
    column = args.column or "To Do"
    if column not in cols:
        raise SystemExit(f"unknown column '{column}'. known: {sorted(cols)}")
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
        raise SystemExit(f"task {args.task_id} not found")
    t["column"] = _id_to_title(int(t["project_id"])).get(int(t["column_id"]), t.get("column_id"))
    return {"task": t}


def cmd_move(args):
    t = _rpc("getTask", [int(args.task_id)])
    if not t:
        raise SystemExit(f"task {args.task_id} not found")
    pid = int(t["project_id"])
    sw = int(t["swimlane_id"])
    cols = _title_to_id(pid)
    if args.column not in cols:
        raise SystemExit(f"unknown column '{args.column}'. known: {sorted(cols)}")
    ok = _rpc("moveTaskPosition", [pid, int(args.task_id), cols[args.column], 1, sw])
    return {"moved": bool(ok), "task_id": int(args.task_id), "column": args.column}


def cmd_comment(args):
    uid = int(_rpc("getMe", [])["id"])
    cid = _rpc("createComment", [int(args.task_id), uid, args.comment])
    return {"comment_id": cid, "task_id": int(args.task_id)}


def cmd_complete(args):
    t = _rpc("getTask", [int(args.task_id)])
    if not t:
        raise SystemExit(f"task {args.task_id} not found")
    pid = int(t["project_id"])
    sw = int(t["swimlane_id"])
    cols = _title_to_id(pid)
    if "Done" not in cols:
        raise SystemExit("no 'Done' column")
    ok = _rpc("moveTaskPosition", [pid, int(args.task_id), cols["Done"], 1, sw])
    cid = None
    if args.comment:
        uid = int(_rpc("getMe", [])["id"])
        cid = _rpc("createComment", [int(args.task_id), uid, args.comment])
    return {"completed": bool(ok), "task_id": int(args.task_id), "comment_id": cid}


def main():
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    lm = sub.add_parser("list-mine")
    lm.add_argument("--column", default="To Do")
    lm.set_defaults(fn=cmd_list_mine)
    g = sub.add_parser("get")
    g.add_argument("task_id", type=int)
    g.set_defaults(fn=cmd_get)
    m = sub.add_parser("move")
    m.add_argument("task_id", type=int)
    m.add_argument("column")
    m.set_defaults(fn=cmd_move)
    c = sub.add_parser("comment")
    c.add_argument("task_id", type=int)
    c.add_argument("comment")
    c.set_defaults(fn=cmd_comment)
    cp = sub.add_parser("complete")
    cp.add_argument("task_id", type=int)
    cp.add_argument("comment", nargs="?", default=None)
    cp.set_defaults(fn=cmd_complete)
    args = p.parse_args()
    print(json.dumps(args.fn(args), indent=2))


if __name__ == "__main__":
    main()
