"""Shared recall ranking: temporal decay, importance weighting, and reranking.

Both recall code paths — the agent tool (:mod:`abi.memory.provider`) and the
HTTP API (:mod:`abi.api.routes.memory`) — call :func:`finalize_recall_ordering`
so the scoring math stays in one place and cannot drift between them.

Scoring model
-------------
Given each candidate's base score (RRF after graph-boost, or BM25 ``rank``):

    final = base * importance_mult * decay_mult

- ``importance_mult = 0.5 + importance``  → neutral (1.0) at the default 0.5,
  boost up to 1.5, penalty down to 0.5.
- ``decay_mult`` = exponential recency (half-life 90d, floor 0.6), **exempting**
  ``source_type = 'identity'`` anchors so team/business context never fades.

If a reranker is available, the top-``rerank_top_n`` candidates are rescored with
a cross-encoder and reordered by relevance; the rerank score then becomes the
returned score. If the reranker is unavailable/returns None, the score-based
order stands (graceful degradation to today's behaviour).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import List, Optional

logger = logging.getLogger(__name__)

HALF_LIFE_DAYS = 90.0
DECAY_FLOOR = 0.6  # old memories keep at least 60% of their score


def _to_utc(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _decay_mult(created_at: Optional[datetime], now: datetime) -> float:
    """Exponential recency multiplier in [DECAY_FLOOR, 1.0]."""
    created = _to_utc(created_at)
    if created is None:
        return 1.0
    age_days = max(0.0, (now - created).total_seconds() / 86400.0)
    recency = 0.5 ** (age_days / HALF_LIFE_DAYS)  # 1.0 today, 0.5 at 90d
    return DECAY_FLOOR + (1.0 - DECAY_FLOOR) * recency


def finalize_recall_ordering(
    rows: List[dict],
    query: str,
    *,
    decay_enabled: bool,
    reranker: Optional[object],
    rerank_top_n: int,
    limit: int,
) -> List[dict]:
    """Apply decay + importance to ``rows``, optionally rerank, return top ``limit``.

    Each row is a dict with keys: ``id``, ``content``, ``created_at`` (datetime),
    ``source_type`` (optional), ``importance`` (float|None), and a base score in
    ``rrf_score`` or ``rank``. Sets ``row['score']`` and returns the ordered rows.
    """
    now = datetime.now(timezone.utc)

    for row in rows:
        base = float(row.get("rrf_score") if row.get("rrf_score") is not None else row.get("rank", 0.0))
        importance = row.get("importance")
        importance_mult = 0.5 + float(importance if importance is not None else 0.5)

        if decay_enabled and row.get("source_type") != "identity":
            decay_mult = _decay_mult(row.get("created_at"), now)
        else:
            decay_mult = 1.0

        row["score"] = base * importance_mult * decay_mult

    rows.sort(key=lambda r: float(r.get("score", 0.0)), reverse=True)

    # Cross-encoder rerank over the top-N candidates for sharper top-k precision.
    if reranker is not None and query and rows:
        top = rows[: max(rerank_top_n, limit)]
        try:
            scores = reranker.rerank(query, [r.get("content", "") for r in top])
        except Exception as exc:  # never let reranking break recall
            logger.warning("rerank raised, falling back to score order: %s", exc)
            scores = None
        if scores is not None and len(scores) == len(top):
            for r, s in zip(top, scores):
                r["score"] = float(s)
            top.sort(key=lambda r: float(r.get("score", 0.0)), reverse=True)
            return top[:limit]

    return rows[:limit]
