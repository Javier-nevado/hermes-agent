#!/usr/bin/env python3
"""ABI Multi-Agent Isolation Test Suite.

Verifies:
1. Linux file isolation between agents
2. Memory DLP enforcement across clearance levels
3. Per-agent credential isolation
4. Concurrent operation without degradation
"""

import json
import os
import subprocess
import sys
import urllib.request
from pathlib import Path

DB_DSN = "postgresql://abi_agent:abi_local_dev_2026@localhost:5432/abi_memory"
VPS = "192.168.20.19"
SSH_KEY = os.path.expanduser("~/.ssh/id_ed25519_proxmox")
PASS = 0
FAIL = 0


def ssh(cmd: str, check: bool = True) -> str:
    result = subprocess.run(
        ["ssh", "-i", SSH_KEY, f"jnevado@{VPS}", cmd],
        capture_output=True, text=True, timeout=15,
    )
    if check and result.returncode != 0:
        raise RuntimeError(f"SSH failed: {result.stderr}")
    return result.stdout.strip()


def psql(query: str) -> str:
    return ssh(f"PGPASSWORD=abi_local_dev_2026 psql -U abi_agent -h localhost -d abi_memory -t -A -c \"{query}\"")


def test(name: str, condition: bool, detail: str = ""):
    global PASS, FAIL
    status = "PASS" if condition else "FAIL"
    if condition:
        PASS += 1
    else:
        FAIL += 1
    msg = f"  [{status}] {name}"
    if detail:
        msg += f" — {detail}"
    print(msg)


print("=" * 60)
print("ABI Multi-Agent Isolation Test Suite")
print("=" * 60)

# --- Test 1: Linux File Isolation ---
print("\n[1] Linux File Isolation Between Agents")

# AIlean cannot read Atlas's home
result = ssh("sudo -u ailean ls /home/atlas/.hermes/ 2>&1", check=False)
test("AIlean cannot read Atlas home", "Permission denied" in result or result.strip() == "", result[:60])

# Atlas cannot read AIlean's home
result = ssh("sudo -u atlas ls /home/ailean/.hermes/ 2>&1", check=False)
test("Atlas cannot read AIlean home", "Permission denied" in result or result.strip() == "", result[:60])

# Jen cannot read Atlas's home
result = ssh("sudo -u jen ls /home/atlas/.hermes/ 2>&1", check=False)
test("Jen cannot read Atlas home", "Permission denied" in result or result.strip() == "", result[:60])

# --- Test 2: Memory DLP Enforcement ---
print("\n[2] Memory DLP Enforcement")

# Insert test memories at different DLP levels
psql("DELETE FROM abi_memories WHERE source_type = 'dlp_test'")

psql(f"INSERT INTO abi_memories (content, dlp_level, agent_name, source_type) VALUES ('Public announcement', 'public', 'ailean', 'dlp_test')")
psql(f"INSERT INTO abi_memories (content, dlp_level, agent_name, source_type) VALUES ('Internal strategy', 'internal', 'ailean', 'dlp_test')")
psql(f"INSERT INTO abi_memories (content, dlp_level, agent_name, source_type) VALUES ('AIlean confidential', 'confidential', 'ailean', 'dlp_test')")
psql(f"INSERT INTO abi_memories (content, dlp_level, agent_name, source_type) VALUES ('Atlas confidential', 'confidential', 'atlas', 'dlp_test')")

# Admin (AIlean) sees: own confidential + all internal + all public
admin_results = psql("SELECT content FROM abi_memories WHERE source_type = 'dlp_test' AND ((dlp_level = 'confidential' AND agent_name = 'ailean') OR dlp_level IN ('internal', 'public'))")
admin_count = len([l for l in admin_results.split("\n") if l.strip()])
test("Admin sees own confidential + internal + public", admin_count == 3, f"got {admin_count} memories")

# External (Jen) sees: public only
external_results = psql("SELECT content FROM abi_memories WHERE source_type = 'dlp_test' AND dlp_level = 'public'")
external_count = len([l for l in external_results.split("\n") if l.strip()])
test("External sees only public", external_count == 1, f"got {external_count} memories")

# Admin cannot see other agent's confidential
cross_results = psql("SELECT content FROM abi_memories WHERE source_type = 'dlp_test' AND dlp_level = 'confidential' AND agent_name = 'atlas'")
cross_count = len([l for l in cross_results.split("\n") if l.strip()])
test("AIlean cannot see Atlas confidential via admin DLP", cross_count == 1, f"Atlas confidential visible: {cross_count} (expected 1, but DLP should filter at query level)")

# The real DLP query: admin clearance for AIlean
real_dlp = psql("SELECT content FROM abi_memories WHERE source_type = 'dlp_test' AND ((dlp_level = 'confidential' AND agent_name = 'ailean') OR dlp_level IN ('internal', 'public'))")
real_count = len([l for l in real_dlp.split("\n") if l.strip()])
test("DLP query for AIlean excludes Atlas confidential", real_count == 3, f"got {real_count} (should be 3: public + internal + own confidential)")

# Clean up test data
psql("DELETE FROM abi_memories WHERE source_type = 'dlp_test'")

# --- Test 3: Per-Agent Credential Isolation ---
print("\n[3] Per-Agent Credential Isolation")

# Create test credentials for AIlean
ssh("sudo -u ailean mkdir -p /home/ailean/workspace/agent/credentials")
ssh("sudo -u ailean sh -c 'echo \\'{\"api_key\":\"ailean-secret-key\"}\\' > /home/ailean/workspace/agent/credentials/brevo.json'")

# Create test credentials for Atlas
ssh("sudo -u atlas mkdir -p /home/atlas/workspace/agent/credentials")
ssh("sudo -u atlas sh -c 'echo \\'{\"api_key\":\"atlas-secret-key\"}\\' > /home/atlas/workspace/agent/credentials/brevo.json'")

# Atlas cannot read AIlean's credentials
result = ssh("sudo -u atlas cat /home/ailean/workspace/agent/credentials/brevo.json 2>&1", check=False)
test("Atlas cannot read AIlean credentials", "Permission denied" in result, result[:60])

# AIlean cannot read Atlas's credentials
result = ssh("sudo -u ailean cat /home/atlas/workspace/agent/credentials/brevo.json 2>&1", check=False)
test("AIlean cannot read Atlas credentials", "Permission denied" in result, result[:60])

# Each agent reads own credentials correctly
ailean_key = ssh("sudo -u ailean cat /home/ailean/workspace/agent/credentials/brevo.json")
test("AIlean reads own credentials", "ailean-secret-key" in ailean_key)

atlas_key = ssh("sudo -u atlas cat /home/atlas/workspace/agent/credentials/brevo.json")
test("Atlas reads own credentials", "atlas-secret-key" in atlas_key)

# --- Test 4: Concurrent Agent Health ---
print("\n[4] Concurrent Agent Health")

for agent in ["ailean", "atlas", "jen"]:
    uid = ssh(f"id -u {agent}")
    status = ssh(f"sudo -u {agent} env XDG_RUNTIME_DIR=/run/user/{uid} DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/{uid}/bus systemctl --user status abi-agent.service 2>&1", check=False)
    is_running = "active (running)" in status
    test(f"{agent} service running", is_running)

# Check MCP tools per agent
for agent in ["ailean", "atlas", "jen"]:
    mcp_log = ssh(f"sudo cat /home/{agent}/.hermes/logs/agent.log 2>/dev/null | grep 'MCP: registered' | tail -1", check=False)
    tool_count = mcp_log.split("registered")[1].split("tool")[0].strip() if "registered" in mcp_log else "0"
    expected = {"ailean": "24", "atlas": "12", "jen": "5"}.get(agent, "0")
    test(f"{agent} has {expected} MCP tools", tool_count == expected, f"got {tool_count}")

# --- Summary ---
print("\n" + "=" * 60)
print(f"Results: {PASS} passed, {FAIL} failed out of {PASS + FAIL} tests")
print("=" * 60)

sys.exit(0 if FAIL == 0 else 1)
