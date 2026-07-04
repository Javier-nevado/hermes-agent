"""Tier-A automatic memory extraction pipeline (PR 3).

Turns a completed conversation turn into zero-or-more durable memories, fully
provider-independent — no LLM call on the hot path. The pipeline runs entirely
in the abi-memory-api container's extraction worker (off the request path):

1. **Candidate selection** — split the user + assistant messages into declarative
   sentences, dropping tool-call noise, code, and acknowledgements.
2. **Save-worthy gate + classification** (heuristic in 3a) — skip trivia; assign
   ``memory_type`` (preference|decision|fact|event|identity|other) and
   ``importance`` in [0,1] via keyword/structural heuristics.
3. **Dedup** — for each surviving candidate, check the agent's existing memories
   via cross-encoder similarity (reranker) when available, else cosine embedding
   similarity. Skip near-duplicates above the configured threshold.
4. **Write** — hand survivors to the caller's ``writer`` (the existing remember
   write path) with ``source_type='auto_extraction'``.

PR 3b will swap step 2's heuristics for a multilingual mDeBERTa zero-shot NLI
classifier (download-on-first-run, same posture as the reranker). The pipeline
signature stays unchanged — only the classifier call moves.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Callable, Optional

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# Heuristic lexicon (PR-3a). Keep conservative — false negatives are cheap
# (the next turn re-states it), false positives bloat the store.
# --------------------------------------------------------------------------- #

# Exact / near-exact acknowledgement & greeting tokens — never save on their own.
_TRIVIA_EXACT = {
    "ok", "okay", "k", "kk", "thanks", "thank you", "thx", "cheers", "got it",
    "gotcha", "sure", "yes", "no", "yep", "nope", "yeah", "cool", "great",
    "nice", "perfect", "agreed", "done", "understood", "sounds good", "will do",
    "make it so", "hey", "hello", "hi", "yo", "bye", "goodbye", "morning",
    "evening", "right", "exactly", "indeed", "agreed", "hmm", "ok thanks",
    "thank you!", "please", "pls",
}

# Indicators that a statement is worth keeping, grouped by memory_type.
_DECISION = (
    "decided", "let's go with", "going with", "we'll use", "we will use",
    "we'll go", "chose", "the plan is", "let's do", "agreed to", "from now on",
    "switching to", "migrate to", "rolling out", "we are moving to", "we're moving to",
)
_PREFERENCE = (
    "i prefer", "prefer ", "i want", "i'd like", "i like", "i don't like",
    "always", "never", "make sure", "make it", "should be", "needs to be",
    "has to be", "i'd rather", "keep it", "don't ", "do not ",
)
_RULE = (
    "rule:", "policy:", "must", "mandatory", "required to", "never do",
    "always do", "standard is", "by convention", "best practice",
)
_IDENTITY = (
    "i am ", "i'm ", "my name is", "we are ", "we're ", "our company",
    "i work for", "i work at", "i'm the", "i am the", "our team", "i'm based",
    "i live", "based in",
)
_EVENT = (
    "happened", "occurred", "yesterday", "last week", "last month", "we did",
    "completed", "shipped", "deployed", "merged", "fixed", "broke", "launched",
    "went down", "ran the", "sent the", "created the",
)

# Patterns that lift importance — concrete signals are more durable than prose.
_AMOUNT = re.compile(r"[\$€£]\s?\d|\d+\s?(k|eur|usd|gbp|/mo|/month|per month|/yr|/year)", re.I)
_DATELIKE = re.compile(
    r"\b\d{4}-\d{2}-\d{2}|\b\d{1,2}\s?(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)",
    re.I,
)
_VERSION = re.compile(r"\bv?\d+\.\d+(\.\d+)?\b")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_IP = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b")
_URL = re.compile(r"https?://", re.I)

# Sentence splitter — keep it simple, good enough for chat utterances.
_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+|; ")
# Skip lines that look like tool/JSON output, system notes, or code.
_NOISE_LINE = re.compile(
    r"^\s*[<\[\{\(]|tool_call|function_call|^\s*```|^\s*def |^\s*import |"
    r"^\s*SELECT |^\s*insert into |`[^`]*`\s*:|^\s*\d+\)\s",
    re.I,
)

MIN_CANDIDATE_LEN = 16
MAX_CANDIDATE_LEN = 500


@dataclass
class Candidate:
    text: str
    memory_type: str  # preference|decision|fact|event|identity|other
    importance: float  # [0,1]


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def _is_trivia(text: str) -> bool:
    raw = _clean(text)
    if not raw:
        return False
    t = raw.lower()
    t_stripped = t.rstrip(".!?")
    if len(t_stripped) > 40:
        return False
    # Exact acknowledgement / greeting.
    if t_stripped in _TRIVIA_EXACT:
        return True
    # Pure question with no payload ("what do you think?", "ready?") — checked on
    # the un-stripped text so a trailing '?' is still seen as a question.
    if t.endswith("?") and len(t) < 30:
        return True
    return False


def _classify(text: str) -> tuple[str, float]:
    """Return (memory_type, importance) using heuristic indicators."""
    low = text.lower()

    mtype = "fact"
    if any(k in low for k in _DECISION):
        mtype = "decision"
    elif any(k in low for k in _PREFERENCE):
        mtype = "preference"
    elif any(k in low for k in _RULE):
        mtype = "fact"  # rules recorded as facts until a dedicated type is needed
    elif any(k in low for k in _IDENTITY):
        mtype = "identity"
    elif any(k in low for k in _EVENT):
        mtype = "event"

    # Importance: concrete signals bump it; decisions/rules/identity are high by default.
    importance = 0.5
    if mtype in ("decision", "identity"):
        importance = 0.85
    elif mtype == "preference":
        importance = 0.75
    elif mtype == "event":
        importance = 0.65

    has_signal = any(
        p.search(text)
        for p in (_AMOUNT, _DATELIKE, _VERSION, _EMAIL, _IP, _URL)
    )
    if has_signal:
        importance = max(importance, 0.8)

    return mtype, round(min(importance, 1.0), 2)


def select_candidates(user_content: str, assistant_content: str) -> list[Candidate]:
    """Split a turn into save-worthy candidate statements.

    User utterances are the strong signal (the human states preferences/decisions/
    facts); assistant utterances are only kept when they carry a save-worthy
    indicator, to avoid echoing the agent's own prose back into memory.
    """
    out: list[Candidate] = []
    seen_norm: set[str] = set()

    def add_sentences(block: str, *, require_signal: bool) -> None:
        for raw in _SENT_SPLIT.split(block or ""):
            text = _clean(raw)
            if not text or len(text) < MIN_CANDIDATE_LEN or len(text) > MAX_CANDIDATE_LEN:
                continue
            if _NOISE_LINE.search(text):
                continue
            if _is_trivia(text):
                continue
            mtype, importance = _classify(text)
            if require_signal and importance < 0.75 and mtype not in ("decision", "identity", "preference"):
                # Assistant prose without a strong indicator — skip.
                continue
            norm = re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()
            if not norm or norm in seen_norm:
                continue
            seen_norm.add(norm)
            out.append(Candidate(text=text, memory_type=mtype, importance=importance))

    add_sentences(user_content, require_signal=False)
    add_sentences(assistant_content, require_signal=True)
    return out


def is_duplicate(
    text: str,
    agent_name: str,
    *,
    conn,
    embed_fn: Optional[Callable[[str], Optional[list[float]]]],
    reranker=None,
    encryptor=None,
    threshold: float = 0.85,
) -> bool:
    """True if a semantically near-identical memory already exists for the agent.

    Prefers the cross-encoder (semantic) when available; falls back to cosine
    embedding similarity so extraction does not depend on rerank being enabled.
    Encryption-transparent: existing rows are decrypted via ``encryptor`` before
    cross-encoding. Returns False on any uncertainty (never blocks a write on a
    probe error).
    """
    if embed_fn is None or not agent_name:
        return False
    try:
        emb = embed_fn(text)
    except Exception as exc:
        logger.debug("dedup embed failed: %s", exc)
        return False
    if emb is None:
        return False

    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT content, 1 - (embedding <=> %s::vector) AS cos_sim "
                "FROM abi_memories "
                "WHERE agent_name = %s AND superseded_by IS NULL "
                "ORDER BY embedding <=> %s::vector LIMIT 10",
                [str(emb), agent_name, str(emb)],
            )
            rows = cur.fetchall()
    except Exception as exc:
        logger.debug("dedup neighbor query failed: %s", exc)
        return False

    if not rows:
        return False

    # Fast path: cosine embedding similarity (always available).
    best_cos = max((float(r[1]) for r in rows if r[1] is not None), default=0.0)
    if best_cos >= threshold:
        return True

    # Refinement: cross-encoder on decrypted plaintext.
    if reranker is not None:
        try:
            existing = []
            for r in rows:
                c = r[0]
                if c and encryptor is not None:
                    from ..api.crypto import EncryptionService  # local import (avoid cycles)
                    if EncryptionService.is_encrypted(c):
                        c = encryptor.decrypt(c)
                if c:
                    existing.append(c)
            if existing:
                scores = reranker.rerank(text, existing)
                if scores and max(scores) >= threshold:
                    return True
        except Exception as exc:
            logger.debug("dedup cross-encoder failed: %s", exc)
    return False


def process_turn(
    payload: dict,
    *,
    conn,
    embed_fn: Optional[Callable[[str], Optional[list[float]]]],
    reranker=None,
    encryptor=None,
    writer: Callable[..., Optional[str]],
    dedup_threshold: float = 0.85,
    min_importance: float = 0.6,
) -> dict:
    """Run the full pipeline for one queued turn payload. Returns stats.

    ``writer(content, agent_name, user_id, dlp_level, memory_type, importance)``
    is the existing remember write path (supplied by deps), returning a memory_id
    or None. Never raises on a single candidate — the queue retries the whole
    payload only if the writer itself is sick (DB down).
    """
    agent_name = payload.get("agent_name") or "unknown"
    user_id = payload.get("user_id")
    user_content = payload.get("user_content") or ""
    assistant_content = payload.get("assistant_content") or ""

    candidates = select_candidates(user_content, assistant_content)

    stats = {"candidates": len(candidates), "saved": 0, "skipped_trivia_floor": 0, "skipped_dup": 0}
    for cand in candidates:
        if cand.importance < min_importance:
            stats["skipped_trivia_floor"] += 1
            continue
        try:
            if is_duplicate(
                cand.text, agent_name, conn=conn, embed_fn=embed_fn,
                reranker=reranker, encryptor=encryptor, threshold=dedup_threshold,
            ):
                stats["skipped_dup"] += 1
                continue
        except Exception as exc:
            logger.debug("dedup check errored (saving anyway): %s", exc)

        try:
            mem_id = writer(
                cand.text, agent_name, user_id,
                dlp_level=None,  # auto-classify PII in the writer
                memory_type=cand.memory_type,
                importance=cand.importance,
            )
            if mem_id:
                stats["saved"] += 1
        except Exception as exc:
            logger.warning("auto-extraction write failed for agent %s: %s", agent_name, exc)

    if stats["saved"]:
        logger.info(
            "auto-extract agent=%s: %d saved (%d dup, %d below floor, %d candidates)",
            agent_name, stats["saved"], stats["skipped_dup"],
            stats["skipped_trivia_floor"], stats["candidates"],
        )
    return stats
