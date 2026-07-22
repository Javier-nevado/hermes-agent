#!/usr/bin/env python3
"""abi-capacity-reporter — daily capacity-equivalent self-report (phones home).

Mirrors the fleet-reporter (stdlib-only transport, survives a broken venv) but for
the DAILY capacity metric from AIlean's spec: each agent self-classifies its day's
work into the configured categories + estimates human-equivalent hours (UNCAPPED);
this script values them at per-category Malta rates and POSTs ONE report/day to
POST /instance/capacity. The Worker response carries the LATEST capacity config
(categories+rates) back; we cache it so category/rate changes propagate to the whole
fleet WITHOUT a release (the living-config loop).

LLM: this script is stdlib-only (no venv import) by design — it shells out to
`hermes -z` AS each agent user (mirrors abi_summarize_runner.sh / the monthly
self-summary), so each agent uses its OWN configured provider. On a provider-less
trial (pre-/login) `hermes -z` errors → that agent is skipped + logged.

Author: Major Tom — Opteia Ground Control. Persistent home: scripts/ (ships in the
release tarball; installs alongside abi-fleet-reporter).
"""
import datetime
import glob
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

ENDPOINT = "https://api.opteia.com/instance/capacity"
REPORTER_VERSION = 1
SCHEMA_VERSION = 1
HTTP_TIMEOUT = 20
ONE_SHOT_TIMEOUT = 360          # per-agent LLM call (generous for busy boxes)
SPOOL_DIR = "/var/lib/abi-capacity/spool"
SPOOL_CAP = 14                  # keep ~2 weeks of failed pushes
CONFIG_CACHE = "/opt/abi-tools/capacity-config.json"
HERE = os.path.dirname(os.path.abspath(__file__))
PROMPT_TEMPLATE = os.path.join(HERE, "abi_capacity_prompt.txt")

# Offline fallback (used only before the first successful phone-home fetches the
# central config). MUST stay in sync with reference/capacity-config.seed.json; the
# cached central config always wins once the box has phoned home once.
BOOTSTRAP_CATEGORIES = [
    ("Administration", 8.50, 10.50, "Email triage, scheduling, calendar, task management, follow-ups"),
    ("Communications", 9.50, 12.00, "Drafting emails, messaging, customer responses, coordination"),
    ("Data Analysis", 12.00, 15.00, "Report generation, metrics, data queries, forecasting"),
    ("Software Development", 13.00, 18.00, "Code review, debugging, scripting, deployment, architecture"),
    ("Document Processing", 8.00, 10.00, "Document creation, editing, formatting, PDF generation"),
    ("Research", 10.50, 13.00, "Web research, competitive intelligence, market analysis"),
    ("CRM / Sales", 9.50, 12.50, "Pipeline updates, lead qualification, contact management"),
    ("Finance", 10.50, 13.00, "Invoicing, reconciliation, expense tracking, ledger entries"),
    ("IT Operations", 11.50, 15.00, "Monitoring, service desk, infrastructure, sysadmin tasks"),
    ("Marketing / Social Media", 10.00, 13.00, "Content marketing, social posts, campaigns, SEO copy"),
    ("Legal & Compliance", 14.00, 18.00, "Compliance checks, AML/KYC, contract review, policy drafting"),
    ("HR / People", 10.00, 13.00, "Recruitment admin, onboarding, leave, people ops"),
    ("QA & Testing", 12.00, 16.00, "Test design, regression, bug verification, release checks"),
    ("Security", 13.00, 17.00, "Vulnerability scans, access reviews, hardening, incident triage"),
    ("Hospitality / Property Operations", 9.00, 12.00, "Reservations, housekeeping/maintenance coordination, guest comms"),
    ("Data Engineering", 13.00, 18.00, "Pipelines, ETL, schema/data modelling, warehouse ops"),
    ("Other", 9.50, 11.50, "Catch-all — provide a generic free-text sub_classification"),
]

SECRET_RE = re.compile(r"(sk-[A-Za-z0-9_-]{6,}|Bearer\s+[A-Za-z0-9_-]{6,})", re.I)


def log(msg):
    print("[capacity-reporter] %s" % msg, flush=True)


def sh(cmd, timeout=15):
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        return r.stdout, r.returncode
    except Exception:
        return "", 1


def redact(s):
    return SECRET_RE.sub("[redacted]", s if isinstance(s, str) else str(s))


# --- hermes / license / agent discovery --------------------------------------

def detect_hermes_dir():
    """The code dir (has VERSION + docker.env + abi/sql/). Shared /opt/hermes-agent
    on bare-metal; per-user ~/.hermes/hermes-agent otherwise."""
    cands = ["/opt/hermes-agent", "/opt/hermes-gateway"]
    cands += sorted(glob.glob("/home/*/.hermes/hermes-agent"))
    cands += sorted(glob.glob("/root/.hermes/hermes-agent"))
    for c in cands:
        if os.path.isfile(os.path.join(c, "VERSION")) and os.path.isfile(os.path.join(c, "docker.env")):
            return c
    return None


def read_license_key(hermes_dir):
    if not hermes_dir:
        return ""
    out, _ = sh("grep -E '^OPTEIA_LICENSE_KEY=' '%s/docker.env' 2>/dev/null | head -1 | cut -d= -f2- | tr -d '\"'" % hermes_dir)
    return out.strip()


def find_agent_homes():
    """Real agent homes: ~/.hermes with a state.db (interactive) OR cron/jobs.json
    (scheduled). Interactive-only trials have no jobs.json — count them too."""
    homes = []
    for pat in ("/home/*/.hermes", "/root/.hermes"):
        for p in sorted(glob.glob(pat)):
            if os.path.isdir(p) and (os.path.isfile(os.path.join(p, "state.db"))
                                     or os.path.isfile(os.path.join(p, "cron", "jobs.json"))):
                homes.append(p)
    return homes


def owner_of(path):
    out, _ = sh("stat -c '%%U' '%s' 2>/dev/null" % path)
    return out.strip() or "unknown"


def find_hermes_bin(agent):
    """Locate the hermes CLI for an agent (mirrors abi_summarize_runner.sh)."""
    home = "/home/%s" % agent
    for h in ("/opt/hermes-agent/.venv/bin/hermes",
              "%s/.hermes/hermes-agent/.venv/bin/hermes" % home,
              "%s/.hermes/hermes-agent/venv/bin/hermes" % home):
        if os.path.isfile(h):
            return h
    return ""


def read_jobs(hermes_home):
    """Job names + execution counts from cron/jobs.json (no scripts/PII)."""
    path = os.path.join(hermes_home, "cron", "jobs.json")
    if not os.path.isfile(path):
        return []
    try:
        obj = json.load(open(path))
    except Exception:
        return []
    jl = obj.get("jobs", []) if isinstance(obj, dict) else obj
    out = []
    for j in jl or []:
        if not isinstance(j, dict):
            continue
        rep = j.get("repeat") or {}
        out.append({"name": redact(j.get("name") or "?"),
                    "executions": int(rep.get("completed") or 0)})
    return out


def read_model_best_effort(agent):
    """Best-effort model label from the agent's config.yaml (for meta only)."""
    path = "/home/%s/.hermes/config.yaml" % agent
    try:
        for line in open(path):
            line = line.strip()
            if line.startswith("default:") and not line.startswith("default_"):
                return line.split(":", 1)[1].strip().strip('"').strip("'")
    except Exception:
        pass
    return None


# --- config (cached central config, bootstrap fallback) ----------------------

def load_config():
    try:
        return json.load(open(CONFIG_CACHE))
    except Exception:
        return {"default_rate": "low",
                "categories": [{"name": n, "low": lo, "high": hi, "blurb": bl}
                               for (n, lo, hi, bl) in BOOTSTRAP_CATEGORIES]}


def cache_config(cfg):
    try:
        os.makedirs(os.path.dirname(CONFIG_CACHE), exist_ok=True)
        tmp = CONFIG_CACHE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(cfg, f, indent=2)
        os.replace(tmp, CONFIG_CACHE)
    except Exception as e:
        log("could not cache config: %s" % e)


# --- per-agent self-classification via hermes -z -----------------------------

def build_prompt(template, report_date, categories, jobs):
    cat_lines = "\n".join("  - %s (€%.2f–€%.2f/hr): %s" % (c["name"], c.get("low", 0), c.get("high", 0), c.get("blurb", ""))
                          for c in categories)
    if jobs:
        job_lines = "\n".join("  - %s (%d runs)" % (j["name"], j["executions"]) for j in jobs)
    else:
        job_lines = "  (no scheduled jobs)"
    return (template.replace("{{REPORT_DATE}}", report_date)
                    .replace("{{CATEGORIES}}", cat_lines)
                    .replace("{{JOBS}}", job_lines))


def run_oneshot(agent, prompt_text):
    """Run `hermes -z` AS the agent user; return its stdout text (or '')."""
    hermes = find_hermes_bin(agent)
    if not hermes:
        log("  %s: hermes binary not found — skip" % agent)
        return ""
    fd, promptfile = tempfile.mkstemp(prefix="cap-prompt-", suffix=".txt")
    try:
        os.write(fd, prompt_text.encode()); os.close(fd)
        os.chmod(promptfile, 0o644)
        inner = ('export HOME=/home/%s; cd /home/%s; exec "%s" -z "$(cat %s)"'
                 % (agent, agent, hermes, promptfile))
        r = subprocess.run(["sudo", "-u", agent, "bash", "-c", inner],
                           capture_output=True, text=True, timeout=ONE_SHOT_TIMEOUT)
        if r.returncode != 0:
            log("  %s: hermes -z rc=%d — skip (provider-less or error)" % (agent, r.returncode))
            return ""
        return r.stdout.strip()
    except subprocess.TimeoutExpired:
        log("  %s: hermes -z timed out — skip" % agent)
        return ""
    except Exception as e:
        log("  %s: hermes -z failed (%s) — skip" % (agent, e))
        return ""
    finally:
        try:
            os.unlink(promptfile)
        except Exception:
            pass


def parse_agent_json(text):
    """Extract the first {...} JSON object from the agent's text (defensive against
    markdown fences / preamble). Returns dict or None."""
    if not text:
        return None
    text = text.strip().strip("`")
    if text.startswith("json"):
        text = text[4:].strip()
    lo, hi = text.find("{"), text.rfind("}")
    if lo < 0 or hi <= lo:
        return None
    try:
        obj = json.loads(text[lo:hi + 1])
        return obj if isinstance(obj, dict) else None
    except Exception:
        return None


def value_report(parsed, categories, default_rate):
    """Apply per-category rates to the agent's self-classification → monetary_value
    + totals. Returns a normalized agent report dict (or None if unusable)."""
    if not parsed:
        return None
    by_name = {c["name"].lower(): c for c in categories}
    rate_key = "high" if default_rate == "high" else "low"
    cats = []
    tot_tasks = tot_hours = tot_value = 0
    for e in parsed.get("categories", []):
        if not isinstance(e, dict):
            continue
        name = e.get("category") or "Other"
        cdef = by_name.get(name.lower()) or by_name.get("other", {})
        rate = float(cdef.get(rate_key, 9.50))
        try:
            hours = float(e.get("human_equivalent_hours") or 0)
            tasks = int(e.get("tasks_count") or 0)
        except (TypeError, ValueError):
            continue
        value = round(hours * rate, 2)
        out = {"category": cdef.get("name", name), "tasks_count": tasks,
               "human_equivalent_hours": round(hours, 2), "hourly_rate": rate,
               "monetary_value": value}
        sub = e.get("sub_classification")
        if sub:
            out["sub_classification"] = redact(str(sub))[:120]
        cats.append(out)
        tot_tasks += tasks; tot_hours += hours; tot_value += value
    return {"agent_role": str(parsed.get("agent_role") or "ABI Assistant")[:80],
            "categories": cats,
            "totals": {"tasks": tot_tasks, "hours": round(tot_hours, 2),
                       "monetary_value": round(tot_value, 2)}}


# --- transport (mirrors abi-fleet-reporter; returns response body) -----------

def _post_bytes(payload_bytes, license_key):
    """POST raw bytes. Returns (status_code, body_text) — body parsed for the
    bidirectional config response. (0, '') on transport failure."""
    try:
        req = urllib.request.Request(
            ENDPOINT, data=payload_bytes,
            headers={"Content-Type": "application/json", "X-License-Key": license_key,
                     "User-Agent": "abi-capacity-reporter/%s" % REPORTER_VERSION},
            method="POST")
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as r:
            return r.getcode(), r.read().decode()
    except urllib.error.HTTPError as he:
        return he.code, he.read().decode()
    except Exception:
        return 0, ""


def _spool_write(payload_bytes):
    try:
        os.makedirs(SPOOL_DIR, exist_ok=True)
        path = os.path.join(SPOOL_DIR, "%d.json" % int(time.time()))
        with open(path, "wb") as f:
            f.write(payload_bytes)
        for old in sorted(glob.glob(os.path.join(SPOOL_DIR, "*.json")), key=os.path.getmtime)[:-SPOOL_CAP]:
            try:
                os.unlink(old)
            except Exception:
                pass
    except Exception:
        pass


def post_payload(payload, license_key):
    """Push; on 200 parse the config from the response + flush the spool. Returns
    (ok, config_or_None)."""
    payload_bytes = json.dumps(payload).encode("utf-8")
    code, body = _post_bytes(payload_bytes, license_key)
    config = None
    if code == 200 and body:
        try:
            config = json.loads(body).get("config")
        except Exception:
            config = None
    if code == 200:
        for f in sorted(glob.glob(os.path.join(SPOOL_DIR, "*.json")), key=os.path.getmtime):
            try:
                with open(f, "rb") as fh:
                    c2, _ = _post_bytes(fh.read(), license_key)
                if c2 == 200:
                    os.unlink(f)
            except Exception:
                pass
        return True, config
    if code == 0 or code == 429 or code >= 500:
        _spool_write(payload_bytes)   # transient — retry later
    # 4xx (non-429): server rejected the payload — don't spool forever
    return False, None


# --- main --------------------------------------------------------------------

def main():
    dry = "--print" in sys.argv or "--dry-run" in sys.argv
    hermes_dir = detect_hermes_dir()
    license_key = read_license_key(hermes_dir)
    abi_version = None
    if hermes_dir:
        v, _ = sh("cat '%s/VERSION' 2>/dev/null" % hermes_dir)
        abi_version = v.strip() or None

    cfg = load_config()
    categories = cfg.get("categories") or [{"name": n, "low": lo, "high": hi, "blurb": bl}
                                            for (n, lo, hi, bl) in BOOTSTRAP_CATEGORIES]
    default_rate = cfg.get("default_rate", "low")

    report_date = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
    log("report_date=%s  default_rate=%s  categories=%d  cfg_version=%s"
        % (report_date, default_rate, len(categories), cfg.get("version", "bootstrap")))

    try:
        template = open(PROMPT_TEMPLATE).read()
    except Exception as e:
        log("FATAL: cannot read prompt template %s (%s)" % (PROMPT_TEMPLATE, e))
        return 1

    homes = find_agent_homes()
    if not homes:
        log("no agent homes found — nothing to report")
    agent_reports = []
    for home in homes:
        agent = owner_of(home)
        jobs = read_jobs(home)
        prompt = build_prompt(template, report_date, categories, jobs)
        log("  classifying %s (%d jobs)…" % (agent, len(jobs)))
        parsed = parse_agent_json(run_oneshot(agent, prompt))
        valued = value_report(parsed, categories, default_rate)
        if not valued:
            log("  %s: no usable self-classification — skip" % agent)
            continue
        valued["meta"] = {"abi_version": abi_version, "model": read_model_best_effort(agent)}
        log("  %s: %s — %d cats, %.1fh, €%.0f" % (agent, valued["agent_role"], len(valued["categories"]),
                                                  valued["totals"]["hours"], valued["totals"]["monetary_value"]))
        agent_reports.append(valued)

    payload = {
        "schema_version": SCHEMA_VERSION,
        "reporter_version": REPORTER_VERSION,
        "report_date": report_date,
        "pushed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "identity": {"hostname": socket.gethostname(), "abi_version": abi_version},
        "agents": agent_reports,
        "totals": {
            "agents": len(agent_reports),
            "tasks": sum(a["totals"]["tasks"] for a in agent_reports),
            "hours": round(sum(a["totals"]["hours"] for a in agent_reports), 2),
            "monetary_value": round(sum(a["totals"]["monetary_value"] for a in agent_reports), 2),
        },
    }

    if dry:
        print(json.dumps(payload, indent=2))
        return 0

    if not license_key:
        log("no OPTEIA_LICENSE_KEY in %s/docker.env — cannot push." % (hermes_dir or "?"))
        return 1

    ok, config = post_payload(payload, license_key)
    if ok:
        if config:
            old = cfg.get("version")
            cache_config(config)
            log("pushed ok (report_date=%s, %d agents, €%.0f). config updated v%s→v%s."
                % (report_date, len(agent_reports), payload["totals"]["monetary_value"],
                   old, config.get("version")))
        else:
            log("pushed ok (report_date=%s, %d agents, €%.0f). no config in response."
                % (report_date, len(agent_reports), payload["totals"]["monetary_value"]))
    else:
        log("push FAILED — spooled. (report_date=%s)" % report_date)
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
