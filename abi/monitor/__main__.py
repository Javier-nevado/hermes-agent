#!/usr/bin/env python3
"""ABI Agent Health Monitor.

Usage:
    python3 -m abi.monitor          # Full report
    python3 -m abi.monitor --json   # JSON output for alerting
"""

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

DB_HOST = os.environ.get("ABI_DB_HOST", "localhost")
DB_PORT = os.environ.get("ABI_DB_PORT", "5432")
DB_NAME = os.environ.get("ABI_DB_NAME", "abi_memory")
DB_USER = os.environ.get("ABI_DB_USER", "abi_agent")
DB_PASS = os.environ.get("ABI_DB_PASS", "abi_local_dev_2026")


def get_active_agents():
    import psycopg2
    conn = psycopg2.connect(host=DB_HOST, port=DB_PORT, dbname=DB_NAME, user=DB_USER, password=DB_PASS)
    with conn.cursor() as cur:
        cur.execute("SELECT username, display_name, role, clearance FROM abi_agents WHERE status = 'active'")
        cols = ["username", "display_name", "role", "clearance"]
        agents = [dict(zip(cols, row)) for row in cur.fetchall()]
    conn.close()
    return agents


def check_service(username):
    uid = subprocess.run(["id", "-u", username], capture_output=True, text=True).stdout.strip()
    result = subprocess.run(
        ["sudo", "-u", username, "env",
         f"XDG_RUNTIME_DIR=/run/user/{uid}",
         f"DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/{uid}/bus",
         "systemctl", "--user", "status", "abi-agent.service"],
        capture_output=True, text=True,
    )
    output = result.stdout + result.stderr

    status = "unknown"
    if "active (running)" in output:
        status = "running"
    elif "inactive" in output:
        status = "stopped"
    elif "failed" in output:
        status = "failed"

    # Parse memory from cgroup via systemd status
    mem = "?"
    for line in output.split("\n"):
        if "Memory:" in line:
            # Memory: 281.3M (max 768M, available 486.7M, peak 293.1M)
            parts = line.split("Memory:")[1].strip()
            mem = parts.split("(")[0].strip()
            break

    return {"status": status, "memory": mem}


def check_mcp(username):
    try:
        content = subprocess.run(
            ["sudo", "cat", f"/home/{username}/.hermes/logs/agent.log"],
            capture_output=True, text=True,
        ).stdout
        for line in reversed(content.split("\n")):
            if "MCP: registered" in line:
                tools = int(line.split("registered")[1].split("tool")[0].strip())
                servers = int(line.split("from")[1].split("server")[0].strip())
                return {"tools": tools, "servers": servers}
    except Exception:
        pass
    return {"tools": 0, "servers": 0}


def check_memory_24h(username):
    import psycopg2
    conn = psycopg2.connect(host=DB_HOST, port=DB_PORT, dbname=DB_NAME, user=DB_USER, password=DB_PASS)
    with conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*), MAX(created_at) FROM abi_memories WHERE agent_name = %s AND created_at > NOW() - INTERVAL '24 hours'",
            (username,)
        )
        row = cur.fetchone()
    conn.close()
    return {"count": row[0], "last": str(row[1])[:19] if row[1] else "never"}


def run_monitor(json_output=False):
    agents = get_active_agents()
    results = []

    for agent in agents:
        u = agent["username"]
        svc = check_service(u)
        mcp = check_mcp(u)
        mem = check_memory_24h(u)

        issues = []
        if svc["status"] != "running":
            issues.append(f"Service {svc['status']}")
        if mcp["tools"] == 0:
            issues.append("No MCP tools")

        results.append({
            "username": u,
            "display_name": agent["display_name"],
            "role": agent["role"],
            "clearance": agent["clearance"],
            "service": svc["status"],
            "memory": svc["memory"],
            "mcp_tools": mcp["tools"],
            "mcp_servers": mcp["servers"],
            "memories_24h": mem["count"],
            "last_memory": mem["last"],
            "healthy": len(issues) == 0,
            "issues": issues,
        })

    # Disk
    df = subprocess.run(["df", "-h", "/"], capture_output=True, text=True).stdout
    disk_line = df.split("\n")[1].split()

    if json_output:
        print(json.dumps({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "agents": results,
            "disk": {"total": disk_line[1], "used": disk_line[2], "percent": disk_line[4]},
        }, indent=2))
    else:
        print(f"=== ABI Agent Health — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} ===\n")
        print(f"{'Agent':<12} {'Status':<10} {'Memory':<15} {'MCP Tools':<12} {'Mem/24h':<10} {'Issues'}")
        print("-" * 75)
        for h in results:
            issues_str = ", ".join(h["issues"]) if h["issues"] else "OK"
            print(f"{h['display_name']:<12} {h['service']:<10} {h['memory']:<15} {h['mcp_tools']:<12} {h['memories_24h']:<10} {issues_str}")
        print(f"\nDisk: {disk_line[2]}/{disk_line[1]} used ({disk_line[4]})")

    return results


def main():
    import argparse
    p = argparse.ArgumentParser(description="ABI Agent Health Monitor")
    p.add_argument("--json", action="store_true")
    run_monitor(json_output=p.parse_args().json)


if __name__ == "__main__":
    main()
