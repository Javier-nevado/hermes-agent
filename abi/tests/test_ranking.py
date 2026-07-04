"""Unit tests for abi.memory.ranking — temporal decay, importance, rerank.

Pure-Python (no DB, no ONNX). Validates the scoring math shared by both recall
paths (provider tool + HTTP API).
"""

from datetime import datetime, timedelta, timezone

from abi.memory.ranking import (
    DECAY_FLOOR,
    HALF_LIFE_DAYS,
    _decay_mult,
    finalize_recall_ordering,
)


def _row(rid, score=0.1, days_old=0, source_type="agent_tool", importance=0.5, content=None):
    created = datetime.now(timezone.utc) - timedelta(days=days_old)
    return {
        "id": rid,
        "content": content or rid,
        "rrf_score": score,
        "created_at": created,
        "source_type": source_type,
        "importance": importance,
    }


def test_decay_mult_recency_curve():
    now = datetime.now(timezone.utc)
    assert _decay_mult(now, now) == 1.0
    at_half = _decay_mult(now - timedelta(days=HALF_LIFE_DAYS), now)
    assert abs(at_half - 0.8) < 1e-9  # 0.6 + 0.4*0.5
    ancient = _decay_mult(now - timedelta(days=3650), now)
    assert DECAY_FLOOR <= ancient < DECAY_FLOOR + 0.01
    assert _decay_mult(None, now) == 1.0  # unknown age → no penalty


def test_decay_ranks_newer_first():
    rows = [_row("new", days_old=0), _row("old", days_old=365)]
    out = finalize_recall_ordering(rows, "q", decay_enabled=True,
                                   reranker=None, rerank_top_n=10, limit=2)
    assert [r["id"] for r in out] == ["new", "old"]


def test_decay_disabled_keeps_score_order():
    # Same score, decay off → stable (insertion) order, no recency effect.
    rows = [_row("a", days_old=0), _row("b", days_old=365)]
    out = finalize_recall_ordering(rows, "q", decay_enabled=False,
                                   reranker=None, rerank_top_n=10, limit=2)
    # Equal scores → stable sort preserves order.
    assert [r["id"] for r in out] == ["a", "b"]


def test_decay_exempts_identity_anchors():
    # An old identity anchor must outrank a newer ordinary memory when scores equal.
    rows = [_row("ordinary", days_old=0, source_type="agent_tool"),
            _row("anchor", days_old=365, source_type="identity")]
    out = finalize_recall_ordering(rows, "q", decay_enabled=True,
                                   reranker=None, rerank_top_n=10, limit=2)
    assert out[0]["id"] == "anchor"  # identity not decayed


def test_importance_boost_and_penalty():
    rows = [_row("low", importance=0.0), _row("high", importance=1.0),
            _row("mid", importance=0.5)]
    out = finalize_recall_ordering(rows, "q", decay_enabled=False,
                                   reranker=None, rerank_top_n=10, limit=3)
    assert [r["id"] for r in out] == ["high", "mid", "low"]


def test_rerank_reorders_by_relevance():
    class FakeReranker:
        def rerank(self, query, candidates):
            # candidates arrive score-desc as [a, b]; score b higher to flip.
            return [0.1, 0.9]

    rows = [_row("a", score=0.5), _row("b", score=0.4)]  # a ranks first by base score
    out = finalize_recall_ordering(rows, "q", decay_enabled=False,
                                   reranker=FakeReranker(), rerank_top_n=10, limit=2)
    assert [r["id"] for r in out] == ["b", "a"]  # rerank overrode base order
    assert out[0]["score"] == 0.9


def test_rerank_none_falls_back_to_score():
    class BrokenReranker:
        def rerank(self, query, candidates):
            return None  # model unavailable

    rows = [_row("a", score=0.5), _row("b", score=0.4)]
    out = finalize_recall_ordering(rows, "q", decay_enabled=False,
                                   reranker=BrokenReranker(), rerank_top_n=10, limit=2)
    assert [r["id"] for r in out] == ["a", "b"]  # score order preserved


def test_rerank_exception_does_not_break_recall():
    class ExplodingReranker:
        def rerank(self, query, candidates):
            raise RuntimeError("boom")

    rows = [_row("a", score=0.5)]
    out = finalize_recall_ordering(rows, "q", decay_enabled=False,
                                   reranker=ExplodingReranker(), rerank_top_n=10, limit=1)
    assert [r["id"] for r in out] == ["a"]


def test_limit_truncates():
    rows = [_row(str(i), score=0.1 * (10 - i)) for i in range(10)]
    out = finalize_recall_ordering(rows, "q", decay_enabled=False,
                                   reranker=None, rerank_top_n=10, limit=3)
    assert len(out) == 3
