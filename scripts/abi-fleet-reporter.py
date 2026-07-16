#!/usr/bin/python3
# -*- coding: utf-8 -*-
"""
abi-fleet-reporter — hourly health + usage telemetry for Opteia ABI boxes.

Runs as a SYSTEM systemd timer (User=root), on SYSTEM python3, STDLIB ONLY, with
ZERO imports from the abi/ package or the agent venv. This is deliberate: the
reporter's job is to tell us when the AGENT STACK has rotted (fake venv, broken
cron, missing migrations, dead gateway). If it imported from the venv, it would
go silent for the exact failures it exists to detect (the Jumbo 2026-07-09 case:
/opt/hermes-agent/.venv/bin/python was a symlink to /usr/bin/python3 with no
site-packages — every "missing module" error visible only to someone who SSH'd in).

It gathers one JSON document per run and POSTs it to
https://api.opteia.com/instance/health (license key in the X-License-Key header).
A Cloudflare Worker validates the key, stamps server identity + a server clock
(received_at), and writes to R2. Boxes NEVER hold R2 credentials.

Robustness contract: every section is independently try/excepted -> {ok:false,
error}; the HTTP POST has a 15s timeout; main() catches ALL exceptions and exits
0 unconditionally. The TIMER FIRING is the liveness signal — a crashed reporter
is indistinguishable from a dead box, so it must never crash.

PII boundary: this collects COUNTS + schedule metadata + infrastructure health
ONLY. No message content, no memory content, no customer PII, no credentials.
The per-job `script` field is deliberately OMITTED (can carry customer logic or
API keys in URLs); job names are secret-redacted. See the plan
(scalable-gathering-rain.md) for the full schema.

MIRRORS modules/abi-maintenance/scripts/abi_usage_collect.py for the usage SQL +
job field-mapping + memory-DB discovery + temp-file state.db read — keep in sync.

Author: Major Tom — Opteia Ground Control.
"""
import glob
import hashlib
import json
import os
import re
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

ENDPOINT = "https://api.opteia.com/instance/health"
REPORTER_VERSION = 2
SCHEMA_VERSION = 1
HTTP_TIMEOUT = 15
SPOOL_DIR = "/var/lib/abi-fleet/spool"
MARKER = "/var/lib/abi-fleet/.last_push"
SPOOL_CAP = 24                      # oldest evicted beyond this (~24h @ hourly)
VENV_PROBE_TIMEOUT = 10             # a venv import that hangs is itself decay
VENV_PROBE_MODULES = ("fastapi", "httpx", "openai", "psycopg2", "telegram", "websockets")
# Containers whose absence indicates a degraded memory stack.
EXPECTED_CONTAINERS = ("abi-memory-db", "abi-memory-api")
# Gateway systemd unit name patterns (system + user-level).
GATEWAY_UNIT_GLOBS = ("hermes-gateway*", "abi-agent*", "abi-gateway*")
# Candidate unit names to probe at the USER level (systemctl --user is-active takes a
# literal unit name, not a glob). A single-agent bare-metal box may run the gateway as a
# user-level `hermes-gateway.service` (e.g. castor) while a multi-agent box uses
# `abi-agent.service` per agent (e.g. .19) — check all + count active if ANY is up.
GATEWAY_USER_UNITS = ("hermes-gateway.service", "abi-agent.service", "abi-gateway.service")
SECRET_RE = re.compile(r"(sk-[A-Za-z0-9_-]{12,}|Bearer\s+[A-Za-z0-9._-]{8,}|xox[bpoa]-[A-Za-z0-9-]{10,})")


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def sh(cmd, timeout=15):
    """Run a shell command, return (stdout, rc). Never raises."""
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        return r.stdout, r.returncode
    except Exception:
        return "", 1


def sh_json(cmd, timeout=15):
    out, rc = sh(cmd, timeout=timeout)
    if not out.strip():
        return None
    try:
        return json.loads(out)
    except Exception:
        return None


def read_file_json(path, timeout=10):
    out, _ = sh("cat '%s' 2>/dev/null" % path, timeout=timeout)
    if not out.strip():
        return None
    try:
        return json.loads(out)
    except Exception:
        return None


def redact(s):
    """Redact obvious bearer-token / API-key patterns. Returns the string or None."""
    if s is None:
        return None
    if not isinstance(s, str):
        s = str(s)
    return SECRET_RE.sub("[redacted]", s)


def detect_hermes_dir():
    """Locate the code dir (has VERSION + docker.env + abi/sql/).
    Bare-metal multi-agent: /opt/hermes-agent (shared). Per-user: ~/.hermes/hermes-agent."""
    cands = ["/opt/hermes-agent", "/opt/hermes-gateway"]
    cands += sorted(glob.glob("/home/*/.hermes/hermes-agent"))
    cands += sorted(glob.glob("/root/.hermes/hermes-agent"))
    for c in cands:
        if os.path.isfile(os.path.join(c, "VERSION")) and os.path.isfile(os.path.join(c, "docker.env")):
            return c
    return None


def read_license_key(hermes_dir):
    """Read OPTEIA_LICENSE_KEY from <dir>/docker.env. Returns '' if absent."""
    if not hermes_dir:
        return ""
    out, _ = sh("grep -E '^OPTEIA_LICENSE_KEY=' '%s/docker.env' 2>/dev/null | head -1 | cut -d= -f2- | tr -d '\"'" % hermes_dir)
    return out.strip()


# --------------------------------------------------------------------------
# health probes (each returns a small dict; never raises)
# --------------------------------------------------------------------------

def probe_identity(hermes_dir):
    out = {"ok": True}
    out["hostname"] = socket.gethostname()
    out["abi_version"] = None
    if hermes_dir:
        v, _ = sh("cat '%s/VERSION' 2>/dev/null" % hermes_dir)
        out["abi_version"] = v.strip() or None
    out["uptime_s"] = None
    up, _ = sh("awk '{print int($1)}' /proc/uptime 2>/dev/null")
    try:
        out["uptime_s"] = int(up.strip()) if up.strip() else None
    except Exception:
        pass
    return out


def probe_venv(hermes_dir):
    """The Jumbo detector. A real venv has pyvenv.cfg + populated site-packages.
    A fake venv (symlink to /usr/bin/python3, no packages) -> real=False, missing core modules."""
    out = {"ok": False, "real": False, "python": None,
           "interpreter": None, "site_packages_pkgs": 0, "missing_modules": []}
    try:
        if not hermes_dir:
            out["error"] = "no hermes_dir"
            return out
        venv = os.path.join(hermes_dir, ".venv")
        py = os.path.join(venv, "bin", "python3")
        out["real"] = os.path.isfile(os.path.join(venv, "pyvenv.cfg"))
        out["interpreter"] = os.path.realpath(py) if os.path.exists(py) else None
        # version
        if os.path.exists(py):
            v, _ = sh("'%s' --version 2>&1" % py, timeout=VENV_PROBE_TIMEOUT)
            out["python"] = v.strip() or None
        # count site-packages entries (a fake venv has ~0; a real uv venv has 100+)
        for sp in sorted(glob.glob(os.path.join(venv, "lib", "python*", "site-packages"))):
            try:
                out["site_packages_pkgs"] = len(os.listdir(sp))
                break
            except Exception:
                pass
        # import-probe the curated module set, one at a time to identify which are missing
        if os.path.exists(py):
            for mod in VENV_PROBE_MODULES:
                rc = subprocess.call([py, "-c", "import %s" % mod],
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                     timeout=VENV_PROBE_TIMEOUT)
                if rc != 0:
                    out["missing_modules"].append(mod)
        out["ok"] = bool(out["real"] and not out["missing_modules"] and out["site_packages_pkgs"] >= 20)
    except subprocess.TimeoutExpired:
        out["error"] = "probe_timeout"   # a hung import is itself a decay signal
    except Exception as e:
        out["error"] = str(e)
    return out


def probe_gateway():
    """Active gateway signals across shapes. Reports three independent signals and
    takes the max as active_count (robust — any one proving liveness avoids a false
    'gateway down'):
      - processes:   count of `hermes gateway`/`abi-agent` processes (works even when
                     systemd's user-bus isn't reachable; the ExecStart is `hermes gateway`)
      - system_units: system-level hermes-gateway/abi-agent services (customer system mode)
      - user_units:   per-user abi-agent.service for multi-agent boxes (.19). MUST run as
                     `sudo -u <user>` — root can't reach another user's user-systemd bus
                     via XDG_RUNTIME_DIR alone ("Operation not permitted")."""
    out = {"ok": True, "processes": 0, "system_units": [], "user_units": {}, "active_count": 0}
    try:
        psout, _ = sh("ps -eo args= 2>/dev/null | grep -E 'hermes.*gateway|abi-agent|gateway run' | grep -v grep")
        out["processes"] = len([l for l in psout.splitlines() if l.strip()])
        globs = " ".join("'%s'" % g for g in GATEWAY_UNIT_GLOBS)
        raw, _ = sh("systemctl list-units --type=service --all --no-legend --plain %s 2>/dev/null" % globs)
        for line in raw.splitlines():
            parts = line.split()
            if len(parts) >= 4 and parts[0].endswith(".service"):
                out["system_units"].append({"name": parts[0], "load": parts[1],
                                            "active": parts[2], "sub": parts[3]})
        for d in sorted(glob.glob("/home/*/.hermes")):
            user = os.path.basename(os.path.dirname(d))
            uid_out, _ = sh("id -u '%s' 2>/dev/null" % user)
            uid = uid_out.strip()
            if not uid:
                continue
            # A user-level gateway may be named hermes-gateway.service (single-agent
            # bare-metal, e.g. castor) or abi-agent.service (multi-agent, e.g. .19).
            # Probe each candidate; active if ANY is up (value records which unit).
            state = "unknown"
            for unit in GATEWAY_USER_UNITS:
                st, _ = sh("sudo -u '%s' XDG_RUNTIME_DIR=/run/user/%s systemctl --user is-active '%s' 2>/dev/null"
                           % (user, uid, unit))
                st = st.strip()
                if st == "active":
                    state = "active:%s" % unit
                    break
                if st and st != "inactive" and state == "unknown":
                    state = st
            out["user_units"][user] = state
        sys_active = sum(1 for u in out["system_units"] if u["active"] == "active")
        usr_active = sum(1 for v in out["user_units"].values() if str(v).startswith("active"))
        out["active_count"] = max(out["processes"], sys_active, usr_active)
    except Exception as e:
        out["error"] = str(e)
    return out


def probe_containers():
    out = {"ok": True, "containers": [], "expected_present": {}}
    try:
        raw, _ = sh("docker ps -a --format '{{.Names}}\t{{.Status}}\t{{.State}}' 2>/dev/null")
        for line in raw.splitlines():
            p = line.split("\t")
            if len(p) >= 3:
                out["containers"].append({"name": p[0], "status": p[1], "state": p[2]})
        names = {c["name"] for c in out["containers"]}
        for exp in EXPECTED_CONTAINERS:
            match = next((c for c in out["containers"] if c["name"] == exp or exp in c["name"]), None)
            out["expected_present"][exp] = match["state"] if match else "absent"
    except Exception as e:
        out["error"] = str(e)
    return out


def probe_memory_api():
    """GET :8010/health — proves the memory API + DB pool + license + embeddings work."""
    out = {"ok": False, "status": {}, "http": None}
    try:
        req = urllib.request.Request("http://127.0.0.1:8010/health")
        with urllib.request.urlopen(req, timeout=8) as r:
            out["http"] = r.getcode()
            try:
                out["status"] = r.read().decode("utf-8", "replace")
                out["status"] = json.loads(out["status"]) if out["status"] else {}
            except Exception:
                pass
        out["ok"] = out["http"] == 200
    except Exception as e:
        out["error"] = str(e)
    return out


def discover_db():
    """Find the memory DB container + working creds. Returns (container, db, user) or None.
    Mirrors abi_usage_collect.collect_memory_db's auth discovery."""
    cands, _ = sh("docker ps --format '{{.Names}}' 2>/dev/null")
    names = cands.split()
    db_ctrs = [c for c in names if any(k in c.lower() for k in ("pgvector", "postgres", "-db", "_db"))
               and "api" not in c.lower()]
    mem_ctrs = [c for c in names if "memory" in c.lower() and "api" not in c.lower()]
    container = (db_ctrs + mem_ctrs + [""])[0]
    if not container:
        return None
    for db in ("abi_memory", "opteia", "hermes", "postgres"):
        for user in ("abi_agent", "postgres", "hermes"):
            probe, _ = sh("docker exec %s psql -U %s -d %s -tAc 'SELECT 1' 2>/dev/null"
                          % (container, user, db), timeout=15)
            if probe.strip() == "1":
                return (container, db, user)
    return None


def probe_memory_db(hermes_dir):
    """Table rowcounts + migrations + embedding count in one auth session."""
    out = {"ok": False, "available": False, "container": None, "db": None, "user": None,
           "tables": {}, "memory_records": 0, "table_rows_total": 0,
           "migrations": {"applied": [], "expected": [], "missing": []},
           "embedding_rows": None}
    try:
        found = discover_db()
        if not found:
            return out
        container, db, user = found
        out["container"], out["db"], out["user"] = container, db, user
        # table rowcounts
        q = "SELECT relname, n_live_tup FROM pg_stat_user_tables ORDER BY n_live_tup DESC;"
        raw, _ = sh("docker exec %s psql -U %s -d %s -tAc \"%s\" 2>/dev/null" % (container, user, db, q), timeout=25)
        for line in raw.splitlines():
            if "|" in line:
                name, cnt = line.rsplit("|", 1)
                try:
                    out["tables"][name.strip()] = int(cnt)
                except ValueError:
                    pass
        out["memory_records"] = sum(out["tables"].get(t, 0) for t in ("abi_memories", "episodes", "entities", "content_ideas"))
        out["table_rows_total"] = sum(out["tables"].values())
        # migrations applied
        mmapplied, _ = sh("docker exec %s psql -U %s -d %s -tAc "
                          "\"SELECT filename FROM abi_schema_migrations ORDER BY filename\" 2>/dev/null"
                          % (container, user, db), timeout=15)
        applied = [ln.strip() for ln in mmapplied.splitlines() if ln.strip()]
        out["migrations"]["applied"] = applied
        # expected = shipped SQL files (basename)
        expected = []
        if hermes_dir:
            expected = sorted(os.path.basename(p) for p in glob.glob(os.path.join(hermes_dir, "abi", "sql", "*.sql")))
        out["migrations"]["expected"] = expected
        out["migrations"]["missing"] = [e for e in expected if e not in applied]
        # embedding rows (read-only; proves embeddings exist + are queryable w/o surfacing content)
        ec, _ = sh("docker exec %s psql -U %s -d %s -tAc "
                   "\"SELECT COUNT(*) FROM abi_memories WHERE embedding IS NOT NULL\" 2>/dev/null"
                   % (container, user, db), timeout=15)
        try:
            out["embedding_rows"] = int(ec.strip()) if ec.strip() else None
        except ValueError:
            pass
        out["available"] = True
        out["ok"] = True
    except Exception as e:
        out["error"] = str(e)
    return out


def probe_disk_load():
    out = {"ok": True, "disk": {}, "load": None}
    try:
        st = shutil.disk_usage("/")
        out["disk"] = {"total_gb": round(st.total / 1e9, 1),
                       "free_gb": round(st.free / 1e9, 1),
                       "used_pct": round(100 * (st.used / st.total), 1) if st.total else None}
        out["load"] = list(os.getloadavg())   # [1m, 5m, 15m]
    except Exception as e:
        out["error"] = str(e)
    return out


def probe_cron(jobs):
    """Aggregate cron health from the already-collected per-agent jobs lists."""
    out = {"ok": True, "jobs_total": 0, "error_count": 0, "stale_count": 0, "recent_errors": []}
    try:
        bad_states = {"error", "failed", "timeout", "exception", "dead"}
        now = time.time()
        for j in jobs:
            out["jobs_total"] += 1
            st = (j.get("last_status") or "").lower()
            if st in bad_states:
                out["error_count"] += 1
                if len(out["recent_errors"]) < 10:
                    out["recent_errors"].append({"name": redact(j.get("name")), "last_status": st,
                                                 "last_run_at": j.get("last_run_at")})
            lra = j.get("last_run_at")
            if j.get("enabled") and lra:
                try:
                    age_h = (now - _parse_ts(lra)) / 3600.0
                    # stale = enabled but last run > 26h ago (hourly/daily jobs should fire within a day)
                    if age_h > 26:
                        out["stale_count"] += 1
                except Exception:
                    pass
    except Exception as e:
        out["error"] = str(e)
    return out


def _parse_ts(s):
    """Parse an ISO-ish timestamp to epoch seconds. Best-effort."""
    s = (s or "").strip().replace("Z", "+00:00")
    try:
        import datetime
        return datetime.datetime.fromisoformat(s).timestamp()
    except Exception:
        # strip fractional + tz, try strptime
        try:
            import datetime
            return datetime.datetime.strptime(s[:19], "%Y-%m-%dT%H:%M:%S").timestamp()
        except Exception:
            return float("inf")


# --------------------------------------------------------------------------
# usage collection — MIRRORS abi_usage_collect.py (keep in sync)
# --------------------------------------------------------------------------

def collect_interactions(hermes_home):
    """Read interaction counts from <home>/state.db. Copy-to-temp for a lock-free
    read (live agent writes constantly -> SQLITE_BUSY). No content/PII: counts only."""
    out = {"available": False}
    path = os.path.join(hermes_home, "state.db")
    if not os.path.exists(path):
        return out
    fd, tmp = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        shutil.copy2(path, tmp)
        wal = path + "-wal"
        if os.path.exists(wal):
            try:
                shutil.copy2(wal, tmp + "-wal")
            except Exception:
                pass
        con = sqlite3.connect(tmp)
        cur = con.cursor()

        def q(s):
            try:
                return cur.execute(s).fetchall()
            except Exception:
                return []
        out["available"] = True
        out["sessions"] = (q("SELECT COUNT(*) FROM sessions") or [(0,)])[0][0]
        out["messages_total"] = (q("SELECT COUNT(*) FROM messages") or [(0,)])[0][0]
        out["messages_by_role"] = {r[0]: r[1] for r in q("SELECT role,COUNT(*) FROM messages GROUP BY role")}
        out["tool_calls"] = (q("SELECT COUNT(*) FROM messages WHERE tool_name IS NOT NULL") or [(0,)])[0][0]
        out["top_tools"] = [{"name": r[0], "count": r[1]} for r in
                            q("SELECT tool_name,COUNT(*) FROM messages WHERE tool_name IS NOT NULL "
                              "GROUP BY tool_name ORDER BY 2 DESC LIMIT 12")]
        r = q("SELECT MIN(timestamp),MAX(timestamp) FROM messages")
        out["msg_ts_range"] = r[0] if r else None
        con.close()
        return out
    except Exception:
        return {"available": False}
    finally:
        for p in (tmp, tmp + "-wal"):
            try:
                os.unlink(p)
            except Exception:
                pass


def collect_agent(hermes_home):
    """Per-agent usage block — field names MIRROR abi_usage_collect.collect_agent
    exactly (so downstream consumers don't fork a vocabulary), EXCEPT the `script`
    field is OMITTED from each job (can carry customer logic / API keys)."""
    data = {"hermes_home": hermes_home, "jobs": [], "sessions": 0, "skills_agent": 0}
    owner, _ = sh("stat -c '%%U' '%s' 2>/dev/null" % hermes_home)
    data["owner"] = owner.strip() or "unknown"

    jobs_obj = read_file_json(os.path.join(hermes_home, "cron", "jobs.json"))
    if isinstance(jobs_obj, dict):
        job_list = jobs_obj.get("jobs", [])
    elif isinstance(jobs_obj, list):
        job_list = jobs_obj
    else:
        job_list = []
    for j in job_list:
        if not isinstance(j, dict):
            continue
        rep = j.get("repeat") or {}
        data["jobs"].append({
            "name": redact(j.get("name")),
            "schedule": j.get("schedule_display") or (j.get("schedule") or {}).get("display"),
            "enabled": bool(j.get("enabled", False)),
            "state": j.get("state"),
            "last_status": j.get("last_status"),
            "zero_llm": bool(j.get("no_agent")) or bool(j.get("script")),
            "executions": int(rep.get("completed") or 0),
            "created_at": j.get("created_at"),
            "last_run_at": j.get("last_run_at"),
            # NOTE: "script" deliberately omitted (PII / secret risk).
        })

    data["interactions"] = collect_interactions(hermes_home)
    if data["interactions"].get("available"):
        data["sessions"] = data["interactions"].get("sessions", 0)
    else:
        sess = read_file_json(os.path.join(hermes_home, "sessions", "sessions.json"))
        data["sessions"] = len(sess) if isinstance(sess, (dict, list)) else 0
    data["messages_total"] = data["interactions"].get("messages_total", 0)
    data["tool_calls_total"] = data["interactions"].get("tool_calls", 0)

    ls, _ = sh("ls -1 '%s/skills/' 2>/dev/null" % hermes_home)
    data["skills_agent"] = len([x for x in ls.split() if x])

    data["jobs_total"] = len(data["jobs"])
    data["jobs_enabled"] = sum(1 for j in data["jobs"] if j["enabled"])
    data["jobs_zero_llm"] = sum(1 for j in data["jobs"] if j["zero_llm"])
    data["executions_total"] = sum(j["executions"] for j in data["jobs"])
    return data


def find_baremetal_agents():
    homes = []
    for p in sorted(glob.glob("/home/*/.hermes")) + sorted(glob.glob("/root/.hermes")) + sorted(glob.glob("/opt/*/.hermes")):
        if os.path.exists(os.path.join(p, "cron", "jobs.json")) or os.path.exists(os.path.join(p, "state.db")):
            homes.append(p)
    return homes


def collect_all_agents():
    baremetal = find_baremetal_agents()
    if baremetal:
        return "bare-metal", [collect_agent(h) for h in baremetal]
    return ("unknown", []) if not _has_docker_agents() else ("docker", _collect_docker_agents())


def _has_docker_agents():
    out, _ = sh("docker ps --format '{{.Names}}' 2>/dev/null")
    return any(any(k in c.lower() for k in ("hermes", "abi-agent", "agent")) and "memory" not in c.lower() and "api" not in c.lower()
               for c in out.split())


def _collect_docker_agents():
    """Minimal docker-agent collection (parity with collector's find_docker_agents;
    counts + job metadata only). Most boxes are bare-metal."""
    out = []
    cands, _ = sh("docker ps --format '{{.Names}}' 2>/dev/null")
    agent_ctrs = [c for c in cands.split() if any(k in c.lower() for k in ("hermes", "abi-agent", "agent"))
                  and "memory" not in c.lower() and "api" not in c.lower()]
    for c in agent_ctrs:
        for home in ("/home/abi/.hermes", "/root/.hermes"):
            probe, _ = sh("docker exec %s sh -c 'ls %s/cron/jobs.json 2>/dev/null' 2>/dev/null" % (c, home))
            if probe and "jobs.json" in probe:
                jobs_raw, _ = sh("docker exec %s cat %s/cron/jobs.json 2>/dev/null" % (c, home))
                try:
                    jobs_obj = json.loads(jobs_raw)
                except Exception:
                    continue
                jl = jobs_obj.get("jobs", []) if isinstance(jobs_obj, dict) else jobs_obj
                jobs = []
                for j in jl or []:
                    if not isinstance(j, dict):
                        continue
                    rep = j.get("repeat") or {}
                    jobs.append({
                        "name": redact(j.get("name")),
                        "enabled": bool(j.get("enabled", False)),
                        "state": j.get("state"),
                        "last_status": j.get("last_status"),
                        "zero_llm": bool(j.get("no_agent")) or bool(j.get("script")),
                        "executions": int(rep.get("completed") or 0),
                        "last_run_at": j.get("last_run_at"),
                    })
                out.append({
                    "hermes_home": "%s:%s" % (c, home), "owner": c, "container": c,
                    "jobs": jobs, "sessions": 0, "skills_agent": 0,
                    "jobs_total": len(jobs),
                    "jobs_enabled": sum(1 for j in jobs if j["enabled"]),
                    "jobs_zero_llm": sum(1 for j in jobs if j["zero_llm"]),
                    "executions_total": sum(j["executions"] for j in jobs),
                })
                break
    return out


# --------------------------------------------------------------------------
# transport — spool + post
# --------------------------------------------------------------------------

def _spool_write(payload_bytes):
    try:
        os.makedirs(SPOOL_DIR, exist_ok=True)
        path = os.path.join(SPOOL_DIR, "%d.json" % int(time.time()))
        with open(path, "wb") as f:
            f.write(payload_bytes)
        # cap: evict oldest beyond SPOOL_CAP
        files = sorted(glob.glob(os.path.join(SPOOL_DIR, "*.json")), key=os.path.getmtime)
        for old in files[:-SPOOL_CAP]:
            try:
                os.unlink(old)
            except Exception:
                pass
    except Exception:
        pass


def _post_bytes(payload_bytes, license_key):
    """POST raw bytes. Returns True on HTTP 200."""
    try:
        req = urllib.request.Request(
            ENDPOINT, data=payload_bytes,
            headers={"Content-Type": "application/json", "X-License-Key": license_key, "User-Agent": "abi-fleet-reporter/%s" % REPORTER_VERSION},
            method="POST")
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as r:
            return r.getcode() == 200
    except urllib.error.HTTPError as he:
        # 4xx other than 429/5xx: the server rejected the payload — don't spool forever
        return False
    except Exception:
        return False


def post_payload(payload, license_key):
    """Push the current payload; on success flush the spool. Returns True if the
    current payload reached the Worker (200). Best-effort spool otherwise."""
    payload_bytes = json.dumps(payload).encode("utf-8")
    ok = _post_bytes(payload_bytes, license_key)
    if ok:
        # flush spool (old entries) best-effort
        for f in sorted(glob.glob(os.path.join(SPOOL_DIR, "*.json")), key=os.path.getmtime):
            try:
                with open(f, "rb") as fh:
                    data = fh.read()
                if _post_bytes(data, license_key):
                    os.unlink(f)
            except Exception:
                pass
        try:
            with open(MARKER, "w", encoding="utf-8") as fh:
                fh.write(payload.get("pushed_at", ""))
        except Exception:
            pass
    else:
        _spool_write(payload_bytes)
    return ok


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

def build_payload(hermes_dir, license_key):
    pushed_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    backfill = not os.path.exists(MARKER)

    shape, agents = collect_all_agents()
    all_jobs = [j for a in agents for j in a.get("jobs", [])]
    memory_db = probe_memory_db(hermes_dir)

    payload = {
        "schema_version": SCHEMA_VERSION,
        "reporter_version": REPORTER_VERSION,
        "pushed_at": pushed_at,          # box UTC; the Worker stamps authoritative received_at
        "backfill": backfill,
        "identity": probe_identity(hermes_dir),
        "shape": shape,
        "license_key_last4": (license_key[-4:] if license_key else None),
        "health": {
            "venv": probe_venv(hermes_dir),
            "gateway": probe_gateway(),
            "containers": probe_containers(),
            "memory_api": probe_memory_api(),
            "disk_load": probe_disk_load(),
            "cron": probe_cron(all_jobs),
        },
        "memory_db": memory_db,
        "agents": agents,
        "totals": {
            "agents": len(agents),
            "jobs_total": sum(a.get("jobs_total", 0) for a in agents),
            "jobs_enabled": sum(a.get("jobs_enabled", 0) for a in agents),
            "jobs_zero_llm": sum(a.get("jobs_zero_llm", 0) for a in agents),
            "executions_total": sum(a.get("executions_total", 0) for a in agents),
            "sessions": sum(a.get("sessions", 0) for a in agents),
            "messages_total": sum(a.get("messages_total", 0) for a in agents),
            "tool_calls_total": sum(a.get("tool_calls_total", 0) for a in agents),
        },
    }
    return payload


def main():
    dry_run = "--dry-run" in sys.argv or "--print" in sys.argv
    try:
        hermes_dir = detect_hermes_dir()
        license_key = read_license_key(hermes_dir)
        payload = build_payload(hermes_dir, license_key)

        h = payload["health"]
        summary = "venv=%s gateway=%s api=%s agents=%d" % (
            "ok" if h["venv"].get("ok") else "BAD",
            h["gateway"].get("active_count", 0),
            h["memory_api"].get("http"),
            payload["totals"]["agents"],
        )
        if h["venv"].get("missing_modules"):
            summary += " missing=%s" % ",".join(h["venv"]["missing_modules"])
        if payload.get("memory_db", {}).get("migrations", {}).get("missing"):
            summary += " migrations_behind=%d" % len(payload["memory_db"]["migrations"]["missing"])

        if dry_run:
            # Print the payload + exit WITHOUT posting or touching the marker/spool.
            # Use this for PII sweeps + decay-sim verification.
            print(json.dumps(payload, indent=2))
            return

        if not license_key:
            print("[fleet-reporter] no OPTEIA_LICENSE_KEY in %s/docker.env — cannot push. (%s)"
                  % (hermes_dir or "?", summary))
            return
        ok = post_payload(payload, license_key)
        if ok:
            spooled = len(glob.glob(os.path.join(SPOOL_DIR, "*.json")))
            print("[fleet-reporter] pushed ok (%s) spool=%d" % (summary, spooled))
        else:
            spooled = len(glob.glob(os.path.join(SPOOL_DIR, "*.json")))
            print("[fleet-reporter] push FAILED — spooled (%d in spool). (%s)" % (spooled, summary))
    except Exception as e:
        # NEVER crash the box. The timer firing is the liveness signal.
        try:
            print("[fleet-reporter] error (continuing): %s" % e)
        except Exception:
            pass
    sys.exit(0)


if __name__ == "__main__":
    main()
