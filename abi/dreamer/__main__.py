"""ABI Dreamer v2 — nightly memory intelligence pipeline.

Dual-mode by necessity — the two halves need capabilities that live in
different places:

* **Heuristic phases (dedup, contradictions, consolidation, temporal)** run
  *in the ``abi-memory-api`` container* (``docker exec``). They need the
  cosine/entity SQL that can't go through the API (``/query`` is sandboxed to
  the customer schema), so they read via direct psycopg2. They WRITE through
  the memory API (``localhost:8010/remember-batch``) so every insight is
  encrypted + embedded + entity-extracted — not raw INSERTs.

* **Phase 5: Densification** runs *per-agent on the host* (``sudo -u <agent>``).
  It needs that agent's hermes config + LLM creds (resolved via
  ``get_text_auxiliary_client(task="dreamer")`` — the auxiliary fast model if
  configured, else the main model), which the container cannot see. The DB port
  isn't published to the host, so densification is API-only: it lists verbose
  memories (``/dreamer/candidates``), rewrites them dense via the LLM, and
  applies the rewrites in place (``/dreamer/densify`` — re-encrypt + re-embed +
  re-extract entities, preserving id/source_type).

Usage::

    # Heuristic phases — in the container (all agents):
    docker exec abi-memory-api python3 -m abi.dreamer --phase heuristic

    # Densify — on the host, per agent (that agent's own LLM):
    sudo -u atlas HERMES_HOME=/home/atlas/.hermes \\
      /opt/hermes-agent/.venv/bin/python3 -m abi.dreamer --phase densify --agent atlas

    # Densify — in the container with a shared LLM override (all agents):
    docker exec -e LLM_API_KEY=... abi-memory-api python3 -m abi.dreamer \\
      --phase densify --llm-base-url http://192.168.20.2:13000/v1 --llm-model opteia-local

    --dry-run is the default everywhere; --write persists.
"""

import argparse
import contextlib
import json
import os
import re
import signal
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from abi.memory.dlp import dlp_where

# psycopg2 is only needed for the heuristic phases (in-container, direct DB).
# The host-side densify path is API-only and must import/run without it.
try:
    import psycopg2
    import psycopg2.extras
    HAS_PSYCOPG2 = True
except ImportError:  # host venv has no psycopg2 — fine for --phase densify
    psycopg2 = None  # type: ignore
    HAS_PSYCOPG2 = False


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_CONTENT_CHARS = 10000          # API /remember content limit
DENSIFY_BATCH_SIZE = 10            # memories per LLM call
DENSIFY_MAX_TOKENS = 2000          # ~10 memories × ~100-200 tokens out
DENSIFY_DEFAULT_LIMIT = None       # None = densify everything (first run)
DEFAULT_SLEEP = 0.5                # polite pacing between LLM calls (seconds)

# LLM call hardening (ported from the memory-backfill skill). Without an
# explicit timeout the underlying HTTP client can hang indefinitely on a
# zombie/half-open connection (z.ai AND the Opteia New-API gateway have been
# observed holding a socket open without ever responding), which strands the
# single-threaded run. SIGALRM interrupts the blocking recv() at the OS level.
DEFAULT_LLM_TIMEOUT = 120.0
DEFAULT_LLM_MAX_RETRIES = 3
LLM_BACKOFF_BASE = 2.0
ABORT_CONSECUTIVE_ERRORS = 12

DEFAULT_DENSIFY_SOURCES = ("auto_extraction", "agent_tool", "api", "migration")


# ---------------------------------------------------------------------------
# LLM hardening (ported from abi-skills memory-backfill PR#4)
# ---------------------------------------------------------------------------

class _HardTimeoutError(Exception):
    """Raised by the SIGALRM backstop when the httpx read-timeout fails to fire."""


def _alarm_handler(signum, frame):  # noqa: ARG001
    raise _HardTimeoutError("hard SIGALRM timeout — the HTTP client read-timeout did not fire "
                            "(zombie / half-open socket)")


# SIGALRM only works on the main thread (this is a single-threaded CLI).
_ALARM_OK = False
try:
    signal.signal(signal.SIGALRM, _alarm_handler)
    _ALARM_OK = True
except (ValueError, OSError):
    pass


@contextlib.contextmanager
def _hard_alarm(seconds):
    """Wall-clock SIGALRM backstop AROUND the LLM call. No-op without SIGALRM."""
    if _ALARM_OK and seconds:
        signal.alarm(int(max(1, seconds)))
        try:
            yield
        finally:
            signal.alarm(0)
    else:
        yield


_SECRET_VALUE = re.compile(
    r"(?i)((?:password|passwd|pwd|secret|api[_-]?key|apikey|access[_-]?token|"
    r"auth[_-]?token|token|bearer|credentials|connection[_-]?string)"
    r"(?:\s*[:=]\s*|\s+(?:is|(?:was|got)\s+(?:set|changed|reset)\s+to|set\s+to|"
    r"changed\s+to|reset\s+to|uses|equals)\s+|\s+))"
    r"['\"]?(?=\S*[\d+/=_@!#$%^*.,;:\-])"
    r"[A-Za-z0-9+/=_@!#$%^*.,;:\-]{8,}['\"]?"
)


def redact_secrets(text: str) -> str:
    """Replace obvious secret values with [REDACTED], preserving the label."""
    return _SECRET_VALUE.sub(lambda m: (m.group(1) or "") + "[REDACTED]", text)


def parse_json_lenient(content: str):
    """Parse JSON from model output, tolerating markdown fences / leading prose.

    Local and mid-tier models (e.g. the Opteia-local Ornith-35B GGUF) often wrap
    JSON in ```json ... ``` fences despite response_format=json_object. Strips
    fences, then falls back to the outermost {...}.
    """
    s = (content or "").strip()
    if s.startswith("```"):
        s = s.split("\n", 1)[1] if "\n" in s else s[3:]
        if s.rstrip().endswith("```"):
            s = s.rstrip()[:-3]
        s = s.strip()
    try:
        return json.loads(s)
    except (json.JSONDecodeError, TypeError):
        start, end = s.find("{"), s.rfind("}")
        if start != -1 and end > start:
            return json.loads(s[start:end + 1])
        raise


DENSIFY_SYSTEM_PROMPT = (
    "You are a memory densification engine. You receive JSON: an array of "
    "{id, content} objects, each a verbose or loosely-worded memory. Rewrite "
    "each into the densest possible telegraphic form WITHOUT losing any "
    "information.\n\n"
    "DENSITY RULES:\n"
    "- Strip filler, determiners, connective tissue, narrative framing. Drop "
    "phrases like \"the user\", \"it should be noted\", \"in order to\".\n"
    "- Prefer terse noun phrases, key:value form, symbols (+, ->, /, =).\n"
    "- Third person, self-contained, telegraphic. One line is ideal.\n"
    "- NEVER drop a fact, name, date, amount, value, or decision to shorten — "
    "only compress wording.\n"
    "- If a memory is already maximally dense, return it UNCHANGED.\n\n"
    "Examples (verbose -> dense):\n"
    "- \"It should be noted that the user prefers HTML delivered via Telegram "
    "to be converted to PDF.\" -> \"Telegram file delivery: HTML as PDF, never "
    "raw HTML.\"\n"
    "- \"The working directory that is used by the agent is located at "
    "/srv/workspace.\" -> \"Working dir: /srv/workspace.\"\n"
    "- \"Neil is a colleague who needs to study for the MS-900 and AI-900 "
    "Microsoft certification exams.\" -> \"Neil preparing for MS-900 + AI-900 "
    "(Microsoft certs).\"\n\n"
    "NEVER include raw secret VALUES. Return ONLY JSON: "
    "{\"results\":[{\"id\":...,\"content\":...}]}. Echo every input id exactly. "
    "Do not wrap the JSON in markdown fences."
)


# ---------------------------------------------------------------------------
# DB connection (heuristic phases only)
# ---------------------------------------------------------------------------

def _parse_dsn(url: str) -> Dict[str, Any]:
    """Parse a postgresql://... URL into psycopg2.connect kwargs."""
    p = urllib.parse.urlparse(url)
    kw: Dict[str, Any] = {
        "host": p.hostname or "localhost",
        "port": str(p.port or 5432),
        "dbname": (p.path or "/").lstrip("/") or "abi_memory",
    }
    if p.username:
        kw["user"] = urllib.parse.unquote(p.username)
    if p.password:
        kw["password"] = urllib.parse.unquote(p.password)
    return kw


def get_connection():
    """Connect to the ABI DB.

    The container injects ``ABI_DATABASE_URL`` (a full DSN) but NOT the legacy
    ``ABI_DB_*`` vars, so without parsing the URL the dreamer connects to dead
    ``localhost:5432``. Prefer the DSN; fall back to ABI_DB_* for dev.
    """
    url = os.environ.get("ABI_DATABASE_URL")
    if url:
        return psycopg2.connect(url)
    return psycopg2.connect(
        host=os.environ.get("ABI_DB_HOST", "localhost"),
        port=os.environ.get("ABI_DB_PORT", "5432"),
        dbname=os.environ.get("ABI_DB_NAME", "abi_memory"),
        user=os.environ.get("ABI_DB_USER", "abi_agent"),
        password=os.environ.get("ABI_DB_PASS", "abi_local_dev_2026"),
    )


def get_active_agents(conn) -> List[Dict]:
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT username, display_name, role, clearance FROM abi_agents WHERE status = 'active'")
        return cur.fetchall()


# ---------------------------------------------------------------------------
# abi-memory-api client (stdlib urllib — no requests dependency)
# ---------------------------------------------------------------------------

class MemoryAPIClient:
    def __init__(self, base: Optional[str] = None):
        self.base = (base or os.environ.get("ABI_MEMORY_API_URL") or "http://localhost:8010").rstrip("/")

    def _call(self, method: str, path: str, payload: Optional[dict] = None, timeout: float = 60.0):
        url = self.base + path
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        req = urllib.request.Request(
            url, data=data, method=method,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8") or "{}")
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")[:300]
            raise RuntimeError(f"{method} {path} -> HTTP {e.code}: {body}") from None
        except urllib.error.URLError as e:
            raise RuntimeError(f"{method} {path} -> {e}") from None

    def health(self) -> dict:
        return self._call("GET", "/health", timeout=10.0)

    def remember(self, content: str, agent_name: str, source_type: str,
                 dlp_level: str = "internal", memory_type: Optional[str] = None,
                 importance: Optional[float] = None) -> dict:
        """Write ONE dreamer-generated memory via the API (encrypted + embedded)."""
        item: Dict[str, Any] = {
            "content": content[:MAX_CONTENT_CHARS], "agent_name": agent_name,
            "dlp_level": dlp_level, "source_type": source_type,
        }
        if memory_type:
            item["memory_type"] = memory_type
        if importance is not None:
            item["importance"] = importance
        res = self._call("POST", "/remember-batch", {"items": [item]})
        results = res.get("results", []) if isinstance(res, dict) else []
        return results[0] if results else {}

    def candidates(self, agent_name: str, source_types: Optional[List[str]] = None,
                   limit: int = 500, offset: int = 0) -> dict:
        return self._call("POST", "/dreamer/candidates", {
            "agent_name": agent_name, "source_types": source_types,
            "limit": limit, "offset": offset,
        })

    def densify(self, agent_name: str, items: List[dict]) -> dict:
        return self._call("POST", "/dreamer/densify", {"agent_name": agent_name, "items": items})


# ---------------------------------------------------------------------------
# LLM resolution (densify)
# ---------------------------------------------------------------------------

def _discover_hermes_root() -> Optional[str]:
    """Find the hermes package root so its modules are importable."""
    candidates = [
        os.environ.get("HERMES_PACKAGE_ROOT"),
        "/opt/hermes-agent",
        "/opt/hermes-gateway",
    ]
    for c in candidates:
        if c and os.path.isdir(os.path.join(c, "agent")) and os.path.isdir(os.path.join(c, "hermes_cli")):
            return c
    return None


def resolve_dreamer_llm(args) -> Tuple[Optional[Any], Optional[str]]:
    """Resolve the LLM client + model for densification.

    Two paths (mirrors the memory-backfill skill):
      1. ``--llm-base-url``: bypass hermes entirely; call an OpenAI-compatible
         endpoint directly. Container-friendly (densify all agents with a shared
         LLM). The key is read from $LLM_API_KEY (or --llm-api-key-env), never a
         CLI arg, to keep it out of the process list.
      2. Otherwise: the agent's own hermes config via ``get_text_auxiliary_client``
         (the auxiliary fast model if ``auxiliary.dreamer`` is configured, else
         the main/default model). Run on the host with HERMES_HOME set.
    Returns (client, model) or (None, None).
    """
    if args.llm_base_url:
        try:
            import openai as _openai
        except ImportError as e:
            print(f"[fatal] --llm-base-url requires the openai package: {e}")
            return None, None
        key_env = args.llm_api_key_env or "LLM_API_KEY"
        api_key = os.environ.get(key_env, "")
        if not api_key:
            print(f"[fatal] --llm-base-url set but ${key_env} is empty. Export the gateway key "
                  f"under that env var (never pass it as a CLI arg).")
            return None, None
        model = args.llm_model or "opteia-fast"
        try:
            # max_retries=0 disables the openai client's INTERNAL retry: its
            # retry would catch the _HardTimeoutError from SIGALRM (one-shot)
            # and re-run the call with no alarm armed, defeating the backstop.
            client = _openai.OpenAI(base_url=args.llm_base_url, api_key=api_key,
                                    timeout=args.llm_timeout, max_retries=0)
        except Exception as e:
            print(f"[fatal] could not build direct OpenAI client: {e}")
            return None, None
        masked = "***" + api_key[-4:] if len(api_key) > 4 else "***"
        print(f"[info] DIRECT LLM override: base_url={args.llm_base_url} model={model!r} "
              f"key=${key_env}({masked})")
        return client, model

    # Path 2: hermes config (auxiliary fast model preferred).
    try:
        from agent.auxiliary_client import get_text_auxiliary_client  # type: ignore
    except ImportError:
        root = _discover_hermes_root()
        if root:
            sys.path.insert(0, root)
            try:
                from agent.auxiliary_client import get_text_auxiliary_client  # type: ignore
            except ImportError:
                get_text_auxiliary_client = None  # type: ignore
        else:
            get_text_auxiliary_client = None  # type: ignore
    if get_text_auxiliary_client is None:
        print("[fatal] could not import hermes (get_text_auxiliary_client). Run with the hermes "
              "venv python and/or set HERMES_PACKAGE_ROOT — or use --llm-base-url.")
        return None, None
    try:
        client, model = get_text_auxiliary_client(task="dreamer")
    except Exception as e:
        print(f"[fatal] get_text_auxiliary_client raised: {e}")
        return None, None
    if not client or not model:
        print("[fatal] could not resolve an LLM via hermes config. Set auxiliary.dreamer.{provider,"
              "model} in config.yaml, or use --llm-base-url.")
        return None, None
    # Best-effort: disable the client's internal retry so SIGALRM isn't defeated.
    try:
        client.max_retries = 0  # type: ignore[attr-defined]
    except (AttributeError, TypeError):
        pass
    print(f"[info] LLM via hermes auxiliary(task=dreamer): model={model!r}")
    return client, model


def densify_batch(client, model: str, items: List[dict],
                  timeout: float = DEFAULT_LLM_TIMEOUT,
                  max_retries: int = DEFAULT_LLM_MAX_RETRIES) -> Tuple[dict, Optional[str], int]:
    """Densify a batch of memories in one LLM call. items=[{id, content}].

    Returns (results_by_id {id: dense_content}, error_or_None, tokens).
    """
    if not items:
        return {}, None, 0
    user_prompt = "Rewrite each memory denser. Input JSON:\n" + json.dumps(
        {"items": [{"id": it["id"], "content": it["content"]} for it in items]}
    )
    resp = None
    last_err = None
    for attempt in range(1, max_retries + 1):
        try:
            with _hard_alarm(timeout):
                resp = client.chat.completions.create(
                    model=model,
                    messages=[
                        {"role": "system", "content": DENSIFY_SYSTEM_PROMPT},
                        {"role": "user", "content": user_prompt},
                    ],
                    response_format={"type": "json_object"},
                    temperature=0.1,
                    max_tokens=DENSIFY_MAX_TOKENS,
                    timeout=timeout,
                )
            break
        except TypeError as e:
            last_err = f"LLM error (attempt {attempt}/{max_retries}, timeout-kwarg rejected): {e}"
            if "timeout" in str(e).lower() and attempt == 1:
                try:
                    with _hard_alarm(timeout):
                        resp = client.chat.completions.create(
                            model=model,
                            messages=[
                                {"role": "system", "content": DENSIFY_SYSTEM_PROMPT},
                                {"role": "user", "content": user_prompt},
                            ],
                            response_format={"type": "json_object"},
                            temperature=0.1,
                            max_tokens=DENSIFY_MAX_TOKENS,
                        )
                    break
                except Exception as e2:
                    last_err = f"LLM error (attempt {attempt}/{max_retries}, no-timeout fallback): {e2}"
            if attempt < max_retries:
                time.sleep(min(LLM_BACKOFF_BASE * (2 ** (attempt - 1)), 30.0))
        except Exception as e:
            last_err = f"LLM error (attempt {attempt}/{max_retries}): {e}"
            if attempt < max_retries:
                time.sleep(min(LLM_BACKOFF_BASE * (2 ** (attempt - 1)), 30.0))
    if resp is None:
        return {}, last_err or "LLM error (no response after retries)", 0
    content = (resp.choices[0].message.content or "") if resp.choices else ""
    usage = getattr(resp, "usage", None)
    tokens = 0
    if usage:
        tokens = (getattr(usage, "prompt_tokens", 0) or 0) + (getattr(usage, "completion_tokens", 0) or 0)
    if not content.strip():
        return {}, "empty LLM content (model returned nothing)", tokens
    try:
        data = parse_json_lenient(content)
    except (json.JSONDecodeError, TypeError):
        return {}, f"malformed JSON: {content[:80]!r}", tokens
    raw = data.get("results", []) if isinstance(data, dict) else []
    out: Dict[str, str] = {}
    for r in raw:
        if not isinstance(r, dict):
            continue
        rid = r.get("id")
        text = (r.get("content") or "").strip()
        if rid and text:
            out[str(rid)] = redact_secrets(text[:MAX_CONTENT_CHARS])
    return out, None, tokens


# --- Heuristic Phase 1: Deduplication ---

def phase_dedup(conn, agent_name: str, dry_run: bool) -> Dict:
    """Find and remove near-duplicate memories (>0.90 embedding similarity)."""
    stats = {"scanned": 0, "duplicates_found": 0, "removed": 0}

    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("""
            SELECT m1.id AS id1, m2.id AS id2,
                   1 - (m1.embedding <=> m2.embedding) AS similarity,
                   m1.created_at AS created1, m2.created_at AS created2
            FROM abi_memories m1
            JOIN abi_memories m2 ON m1.id < m2.id
                AND m1.agent_name = m2.agent_name
                AND m1.embedding IS NOT NULL
                AND m2.embedding IS NOT NULL
                AND 1 - (m1.embedding <=> m2.embedding) > 0.90
            WHERE m1.agent_name = %s
              AND m1.source_type != 'dreamer'
              AND m2.source_type != 'dreamer'
              AND m1.superseded_by IS NULL
              AND m2.superseded_by IS NULL
            ORDER BY similarity DESC
            LIMIT 50
        """, [agent_name])
        pairs = cur.fetchall()
        stats["scanned"] = len(pairs)

    to_delete = set()
    for pair in pairs:
        stats["duplicates_found"] += 1
        if pair["created1"] > pair["created2"]:
            to_delete.add(str(pair["id2"]))
        else:
            to_delete.add(str(pair["id1"]))

    if not dry_run and to_delete:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM abi_memories WHERE id::text = ANY(%s) AND agent_name = %s",
                [list(to_delete), agent_name],
            )
            stats["removed"] = cur.rowcount
            conn.commit()

    return stats


# --- Heuristic Phase 2: Contradiction Detection ---

def phase_contradictions(conn, api: MemoryAPIClient, agent_name: str, clearance: str, dry_run: bool) -> Dict:
    """Find memories sharing entities but with potentially conflicting content."""
    stats = {"checked": 0, "contradictions_flagged": 0}

    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("""
            SELECT e.name AS entity_name, e.type AS entity_type,
                   array_agg(DISTINCT m.id::text) AS memory_ids,
                   array_agg(DISTINCT m.content) AS contents,
                   count(DISTINCT m.id) AS memory_count
            FROM abi_memory_entities me
            JOIN abi_entities e ON me.entity_id = e.id
            JOIN abi_memories m ON me.memory_id = m.id
            WHERE m.agent_name = %s
              AND m.source_type != 'dreamer'
              AND m.source_type != 'contradiction_alert'
              AND m.superseded_by IS NULL
            GROUP BY e.name, e.type
            HAVING count(DISTINCT m.id) >= 3
            ORDER BY memory_count DESC
            LIMIT 20
        """, [agent_name])
        entity_groups = cur.fetchall()

    for group in entity_groups:
        stats["checked"] += 1
        if len(group["contents"]) >= 3:
            stats["contradictions_flagged"] += 1
            if not dry_run:
                alert = (
                    f"Entity '{group['entity_name']}' ({group['entity_type']}) "
                    f"has {group['memory_count']} memories. "
                    f"Consider reviewing for potential contradictions or consolidation."
                )
                api.remember(alert, agent_name, source_type="contradiction_alert")

    return stats


# --- Heuristic Phase 3: Consolidation ---

def phase_consolidate(conn, api: MemoryAPIClient, agent_name: str, clearance: str, dry_run: bool) -> Dict:
    """Generate summary insights from entity-grouped memories."""
    stats = {"groups_processed": 0, "insights_generated": 0}

    where_clause, params = dlp_where(clearance, agent_name)

    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(f"""
            SELECT e.name AS entity_name, e.type AS entity_type,
                   array_agg(DISTINCT m.content) AS contents,
                   count(DISTINCT m.id) AS memory_count
            FROM abi_memory_entities me
            JOIN abi_entities e ON me.entity_id = e.id
            JOIN abi_memories m ON me.memory_id = m.id
            WHERE m.agent_name = %s
              AND {where_clause}
              AND m.source_type != 'dreamer'
              AND m.source_type != 'contradiction_alert'
              AND m.created_at > NOW() - INTERVAL '7 days'
              AND m.superseded_by IS NULL
            GROUP BY e.name, e.type
            HAVING count(DISTINCT m.id) >= 2
            ORDER BY memory_count DESC
            LIMIT 20
        """, [agent_name] + params)
        groups = cur.fetchall()

    for group in groups:
        stats["groups_processed"] += 1
        contents = group["contents"]
        insight = (
            f"Entity '{group['entity_name']}' ({group['entity_type']}): "
            f"{len(contents)} related memories this week. "
            f"Key topics: {', '.join(c[:60] for c in contents[:3])}"
        )
        stats["insights_generated"] += 1
        if not dry_run:
            api.remember(insight, agent_name, source_type="dreamer")

    return stats


# --- Heuristic Phase 4: Temporal Maintenance ---

def phase_temporal(conn, api: MemoryAPIClient, agent_name: str, dry_run: bool) -> Dict:
    """Flag stale facts and auto-expire dated information."""
    stats = {"stale_flagged": 0, "auto_expired": 0}

    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("""
            SELECT id, content, created_at
            FROM abi_memories
            WHERE agent_name = %s
              AND source_type NOT IN ('dreamer', 'contradiction_alert')
              AND superseded_by IS NULL
              AND valid_to IS NULL
              AND created_at < NOW() - INTERVAL '90 days'
            LIMIT 20
        """, [agent_name])
        stale = cur.fetchall()

    for mem in stale:
        stats["stale_flagged"] += 1
        if not dry_run:
            alert = (
                f"Stale fact review: Memory from {mem['created_at'].strftime('%Y-%m-%d')} "
                f"may be outdated: {mem['content'][:80]}..."
            )
            api.remember(alert, agent_name, source_type="dreamer")

    stats["auto_expired"] = 0  # TODO: date parsing from entity names + auto-expiry
    return stats


# --- Phase 5: Densification (host-side, LLM-powered) ---

def _fetch_candidate_snapshot(api: MemoryAPIClient, agent_name: str, limit: Optional[int],
                              source_types: Optional[List[str]] = None) -> List[dict]:
    """Fetch all un-densified candidates for an agent BEFORE any densify writes.

    Paging by offset against a stable result set (no densify writes yet), so the
    snapshot is consistent regardless of how densification reorders later.
    """
    out: List[dict] = []
    offset = 0
    page_size = 1000
    while True:
        want = page_size if limit is None else min(page_size, limit - len(out))
        if want <= 0:
            break
        page = api.candidates(agent_name, source_types=source_types, limit=want, offset=offset)
        items = page.get("items", []) if isinstance(page, dict) else []
        if not items:
            break
        out.extend(items)
        if len(items) < want or (limit is not None and len(out) >= limit):
            break
        offset += len(items)
    if limit is not None:
        out = out[:limit]
    return out


def phase_densify(agent_name: str, api: MemoryAPIClient, client, model: str,
                  dry_run: bool, batch_size: int = DENSIFY_BATCH_SIZE,
                  limit: Optional[int] = DENSIFY_DEFAULT_LIMIT,
                  source_types: Optional[List[str]] = None,
                  sleep_s: float = DEFAULT_SLEEP) -> Dict:
    """Rewrite verbose memories into dense form via the LLM, in place."""
    stats = {"candidates": 0, "densified": 0, "skipped_unchanged": 0, "errors": 0, "tokens": 0}

    try:
        snapshot = _fetch_candidate_snapshot(api, agent_name, limit, source_types)
    except Exception as e:
        print(f"  [densify] candidates fetch failed: {e}")
        stats["errors"] += 1
        return stats

    stats["candidates"] = len(snapshot)
    if not snapshot:
        return stats

    consec_errors = 0
    for i in range(0, len(snapshot), batch_size):
        batch = snapshot[i:i + batch_size]
        results, err, tokens = densify_batch(
            client, model, batch,
            timeout=DEFAULT_LLM_TIMEOUT, max_retries=DEFAULT_LLM_MAX_RETRIES,
        )
        stats["tokens"] += tokens
        if err:
            stats["errors"] += len(batch)
            consec_errors += 1
            print(f"  [densify] batch {i // batch_size + 1} error: {err}")
            if consec_errors >= ABORT_CONSECUTIVE_ERRORS:
                print(f"  [densify] aborting after {consec_errors} consecutive errors "
                      f"(partial progress kept; re-run later).")
                break
            continue
        consec_errors = 0

        write_items: List[dict] = []
        for it in batch:
            new = (results.get(it["id"]) or "").strip()
            if not new:
                stats["errors"] += 1
                continue
            if dry_run:
                if new != (it.get("content") or "").strip():
                    print(f"    [{it['id'][:8]}] {(it.get('content') or '')[:70]!r} -> {new[:70]!r}")
                    stats["densified"] += 1
                else:
                    stats["skipped_unchanged"] += 1
            else:
                write_items.append({"memory_id": it["id"], "content": new})

        if not dry_run and write_items:
            try:
                res = api.densify(agent_name, write_items)
                stats["densified"] += int(res.get("updated", 0))
                stats["skipped_unchanged"] += int(res.get("skipped", 0))
                stats["errors"] += int(res.get("errors", 0))
            except Exception as e:
                print(f"  [densify] densify write failed: {e}")
                stats["errors"] += len(write_items)

        if sleep_s:
            time.sleep(sleep_s)

    return stats


# ---------------------------------------------------------------------------
# Run dispatch
# ---------------------------------------------------------------------------

HEURISTIC_PHASES = ("dedup", "contradictions", "consolidation", "temporal")


def run_heuristic(agent_filter: Optional[str], dry_run: bool,
                  phases: Optional[List[str]] = None) -> Dict:
    """Run the in-container heuristic phases (needs psycopg2). Writes via the API."""
    if not HAS_PSYCOPG2:
        print("[heuristic] psycopg2 unavailable — skipping heuristic phases. "
              "(Run inside the abi-memory-api container: docker exec abi-memory-api python3 -m abi.dreamer --phase heuristic)")
        return {"agents_processed": 0}
    selected = set(phases) if phases else set(HEURISTIC_PHASES)

    conn = get_connection()
    api = MemoryAPIClient()
    try:
        agents = get_active_agents(conn)
    except Exception as e:
        print(f"[heuristic] could not read abi_agents: {e}")
        conn.close()
        return {"agents_processed": 0}

    if agent_filter:
        agents = [a for a in agents if a["username"] == agent_filter]
    if not agents:
        print("No active agents found.")
        conn.close()
        return {"agents_processed": 0}

    summary = {"agents_processed": 0, "phases": {}}
    for agent in agents:
        name = agent["username"]
        clearance = agent["clearance"]
        print(f"\n[{name}] Heuristic phases ({', '.join(sorted(selected)) or 'none'})...")
        res: Dict[str, Any] = {}
        if "dedup" in selected:
            s = phase_dedup(conn, name, dry_run)
            print(f"  dedup: {s['duplicates_found']} duplicates found, {s['removed']} removed")
            res["dedup"] = s
        if "contradictions" in selected:
            s = phase_contradictions(conn, api, name, clearance, dry_run)
            print(f"  contradictions: {s['checked']} entities checked, {s['contradictions_flagged']} flagged")
            res["contradictions"] = s
        if "consolidation" in selected:
            s = phase_consolidate(conn, api, name, clearance, dry_run)
            print(f"  consolidation: {s['groups_processed']} groups, {s['insights_generated']} insights")
            res["consolidation"] = s
        if "temporal" in selected:
            s = phase_temporal(conn, api, name, dry_run)
            print(f"  temporal: {s['stale_flagged']} stale facts flagged")
            res["temporal"] = s
        summary["agents_processed"] += 1
        summary["phases"][name] = res

    conn.close()
    return summary


def run_densify(args, api: MemoryAPIClient) -> Dict:
    """Run Phase 5 densification. Requires an LLM + --agent (host) or --llm-base-url."""
    client, model = resolve_dreamer_llm(args)
    if not client:
        return {"agents_processed": 0}

    source_types = list(args.source_types) if args.source_types else list(DEFAULT_DENSIFY_SOURCES)

    # Determine agent set. On the host (no psycopg2) --agent is required; in the
    # container with --llm-base-url we can enumerate all active agents.
    agents: List[str] = []
    if args.agent:
        agents = [args.agent]
    elif HAS_PSYCOPG2:
        try:
            conn = get_connection()
            agents = [a["username"] for a in get_active_agents(conn)]
            conn.close()
        except Exception as e:
            print(f"[densify] could not enumerate agents from DB: {e}")
    if not agents:
        print("[densify] no agent selected. Pass --agent <name> (host) or run in the container "
              "with --llm-base-url to densify all agents.")
        return {"agents_processed": 0}

    summary = {"agents_processed": 0, "phases": {}}
    for name in agents:
        print(f"\n[{name}] Densification ({'DRY-RUN' if args.dry_run else 'WRITE'})...")
        s = phase_densify(
            name, api, client, model, dry_run=args.dry_run,
            batch_size=args.batch_size, limit=args.limit,
            source_types=source_types, sleep_s=args.sleep,
        )
        print(f"  candidates={s['candidates']} densified={s['densified']} "
              f"skipped_unchanged={s['skipped_unchanged']} errors={s['errors']} tokens={s['tokens']}")
        summary["agents_processed"] += 1
        summary["phases"][name] = s
    return summary


def main() -> int:
    ap = argparse.ArgumentParser(description="ABI Dreamer v2 — nightly memory intelligence pipeline")
    ap.add_argument("--agent", default=None, help="only process this agent username (required for --phase densify on the host)")
    ap.add_argument("--phase", default="all",
                    help="heuristic | dedup | densify | all (default all — runs what the context allows)")
    ap.add_argument("--dry-run", action="store_true", help="analyze without writing (default for densify)")
    ap.add_argument("--write", action="store_true", help="persist changes (heuristic insights / densified rewrites)")
    # densify knobs
    ap.add_argument("--limit", type=int, default=DENSIFY_DEFAULT_LIMIT, help="cap candidates per agent (densify sizing)")
    ap.add_argument("--batch-size", type=int, default=DENSIFY_BATCH_SIZE, help="memories per LLM call (densify)")
    ap.add_argument("--source-types", nargs="*", default=None,
                    help="densify only these source_types (default: auto_extraction agent_tool api migration)")
    ap.add_argument("--sleep", type=float, default=DEFAULT_SLEEP, help="seconds between LLM calls")
    ap.add_argument("--llm-timeout", type=float, default=DEFAULT_LLM_TIMEOUT)
    ap.add_argument("--llm-retries", type=int, default=DEFAULT_LLM_MAX_RETRIES)
    ap.add_argument("--llm-base-url", default=None,
                    help="bypass hermes; call this OpenAI-compatible endpoint directly (container densify-all)")
    ap.add_argument("--llm-model", default=None, help="with --llm-base-url: model name (default opteia-fast)")
    ap.add_argument("--llm-api-key-env", default="LLM_API_KEY",
                    help="env var holding the API key for --llm-base-url (default LLM_API_KEY)")
    ap.add_argument("--api-url", default=None, help="abi-memory-api base URL (default $ABI_MEMORY_API_URL or http://localhost:8010)")
    args = ap.parse_args()

    print(f"=== ABI Dreamer v2 — {datetime.now(timezone.utc).isoformat()} ===")
    print(f"[info] psycopg2={'yes' if HAS_PSYCOPG2 else 'no (host)'} phase={args.phase!r} "
          f"mode={'WRITE' if args.write else 'DRY-RUN'}")

    api = MemoryAPIClient(args.api_url)
    try:
        h = api.health()
        print(f"[info] api health: status={h.get('status')} db={h.get('db')} "
              f"embeddings={h.get('embeddings')} encryption={h.get('encryption')}")
    except Exception as e:
        print(f"[fatal] cannot reach abi-memory-api at {api.base}: {e}")
        return 2

    dry_run = not args.write
    phase = (args.phase or "all").lower()

    if phase in ("heuristic", "dedup"):
        phases = HEURISTIC_PHASES if phase == "heuristic" else ["dedup"]
        r = run_heuristic(args.agent, dry_run, phases)
        print(f"\nHeuristic done: {r['agents_processed']} agents processed")
    elif phase == "densify":
        r = run_densify(args, api)
        print(f"\nDensify done: {r['agents_processed']} agents processed")
    elif phase == "all":
        rh = run_heuristic(args.agent, dry_run)
        print(f"\nHeuristic done: {rh['agents_processed']} agents processed")
        rd = run_densify(args, api)
        print(f"\nDensify done: {rd['agents_processed']} agents processed")
    else:
        print(f"[fatal] unknown --phase {args.phase!r} (use heuristic|dedup|densify|all)")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
