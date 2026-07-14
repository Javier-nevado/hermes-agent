"""Memory operation routes — remember, recall, forget, batch.

Ported from abi/memory/provider.py ABIMemoryProvider methods.
Business logic is identical; this module wraps it in FastAPI endpoints
with Pydantic validation.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import List

import psycopg2.extras
from fastapi import APIRouter, Depends, HTTPException

from abi.memory.dlp import dlp_where
from abi.memory.pii import classify_pii
from abi.memory.ranking import finalize_recall_ordering

from ..deps import (
    get_pool,
    get_extractor,
    get_encryptor,
    get_reranker,
    get_extraction_queue,
    get_extraction_stats,
    has_importance_column,
    has_access_tracking,
)
from ..license import require_license
from ..schemas import (
    RememberRequest,
    RememberResponse,
    RecallRequest,
    RecallResponse,
    MemoryItem,
    ForgetRequest,
    ForgetResponse,
    RememberBatchRequest,
    RememberBatchResponse,
    TurnIngestRequest,
    TurnIngestResponse,
    DreamerCandidateItem,
    DreamerCandidatesRequest,
    DreamerCandidatesResponse,
    DreamerDensifyRequest,
    DreamerDensifyResponse,
)

logger = logging.getLogger(__name__)
router = APIRouter()


def _decay_enabled() -> bool:
    return os.environ.get("ABI_MEMORY_DECAY_ENABLED", "").strip().lower() in ("1", "true", "yes", "on")


def _rerank_enabled() -> bool:
    return os.environ.get("ABI_MEMORY_RERANK_ENABLED", "").strip().lower() in ("1", "true", "yes", "on")


def _rerank_topn() -> int:
    return int(os.environ.get("ABI_MEMORY_RERANK_TOPN", "20"))


def _update_access_tracking(conn, results: list) -> None:
    """Bump last_accessed/access_count for recalled memories (non-fatal).

    Records the 'frequently used' signal that future relevance/decay and any
    retention policy depend on. No-op on pre-005 DBs (no columns) and on any
    error — must never break recall. The recall transaction is read-only up to
    this point, so committing here is safe.
    """
    if not results or not has_access_tracking():
        return
    try:
        ids = [str(r.get("id")) for r in results if r.get("id")]
        if not ids:
            return
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE abi_memories SET last_accessed = NOW(), "
                "access_count = COALESCE(access_count, 0) + 1 "
                "WHERE id::text = ANY(%s)",
                [ids],
            )
        conn.commit()
    except Exception as e:
        try:
            conn.rollback()
        except Exception:
            pass
        logger.warning("Access tracking update failed: %s", e)


def _get_embedding(text: str):
    """Get embedding vector for text. Returns None if unavailable."""
    try:
        from abi.memory.embeddings import get_embedding
        return get_embedding(text)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# POST /remember
# ---------------------------------------------------------------------------

def _persist_memory(
    conn,
    plaintext: str,
    *,
    agent_name: str,
    user_id,
    dlp_level: str,
    source_type: str = "api",
    memory_type: str = None,
    importance: float = None,
    extractor=None,
    encryptor=None,
):
    """Single memory write path: INSERT + optional type/importance + entities + supersession.

    Shared by ``/remember``, ``/remember-batch``, and the auto-extraction worker
    so all writes go through one code path (the encrypted/plaintext × embedding
    branches live here once). ``importance``/``memory_type`` land via a cheap
    post-INSERT UPDATE on 005+ DBs; legacy DBs ignore them. Caller controls the
    transaction (``conn.autocommit`` set by the caller). Returns
    ``(memory_id, entity_count, edge_count, superseded_count)``.
    """
    if extractor is None:
        extractor = get_extractor()

    embedding = _get_embedding(plaintext)
    memory_id = str(uuid.uuid4())
    content = encryptor.encrypt(plaintext) if encryptor else plaintext

    with conn.cursor() as cur:
        if embedding:
            if encryptor:
                # Encrypted path: pass fts explicitly from plaintext
                cur.execute(
                    """
                    INSERT INTO abi_memories (id, content, embedding, dlp_level, agent_name, user_id, source_type, fts)
                    VALUES (%s, %s, %s::vector, %s, %s, %s, %s, to_tsvector('english', %s))
                    RETURNING id
                    """,
                    [memory_id, content, str(embedding), dlp_level, agent_name, user_id, source_type, plaintext],
                )
            else:
                # Plaintext path: trigger generates fts automatically
                cur.execute(
                    """
                    INSERT INTO abi_memories (id, content, embedding, dlp_level, agent_name, user_id, source_type)
                    VALUES (%s, %s, %s::vector, %s, %s, %s, %s)
                    RETURNING id
                    """,
                    [memory_id, content, str(embedding), dlp_level, agent_name, user_id, source_type],
                )
        else:
            if encryptor:
                cur.execute(
                    """
                    INSERT INTO abi_memories (id, content, dlp_level, agent_name, user_id, source_type, fts)
                    VALUES (%s, %s, %s, %s, %s, %s, to_tsvector('english', %s))
                    RETURNING id
                    """,
                    [memory_id, content, dlp_level, agent_name, user_id, source_type, plaintext],
                )
            else:
                cur.execute(
                    """
                    INSERT INTO abi_memories (id, content, dlp_level, agent_name, user_id, source_type)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    RETURNING id
                    """,
                    [memory_id, content, dlp_level, agent_name, user_id, source_type],
                )

        # Optional importance/memory_type (005 migration). One cheap UPDATE; no-op
        # on legacy DBs and when neither is provided.
        if has_importance_column() and (memory_type is not None or importance is not None):
            sets, vals = [], []
            if memory_type is not None:
                sets.append("memory_type = %s")
                vals.append(memory_type)
            if importance is not None:
                sets.append("importance = %s")
                vals.append(float(importance))
            vals.append(memory_id)
            cur.execute(
                f"UPDATE abi_memories SET {', '.join(sets)} WHERE id = %s", vals
            )

    entity_count = 0
    edge_count = 0
    superseded_count = 0
    try:
        entities = extractor.extract(plaintext)
        if entities:
            entity_count, edge_count = _store_entities(conn, memory_id, entities, plaintext)
            superseded_count = _supersede_old_memories(conn, memory_id, entities, agent_name)
    except Exception as e:
        logger.warning("Entity extraction failed for memory %s: %s", memory_id, e)

    return memory_id, entity_count, edge_count, superseded_count


def make_extraction_writer(pool, encryptor):
    """Build the writer callable used by the auto-extraction worker.

    ``writer(content, agent_name, user_id, *, dlp_level, memory_type, importance)``
    checks out its own connection, writes one memory with
    ``source_type='auto_extraction'``, and returns the memory_id (or None). Each
    call is independent — the worker processes turns sequentially.
    """

    def writer(content, agent_name, user_id, *, dlp_level=None, memory_type=None, importance=None):
        dlp = dlp_level or ("confidential" if classify_pii(content) else "internal")
        conn = pool.getconn()
        try:
            conn.autocommit = True
            mem_id, _ec, _edc, _sc = _persist_memory(
                conn, content,
                agent_name=agent_name, user_id=user_id, dlp_level=dlp,
                source_type="auto_extraction",
                memory_type=memory_type, importance=importance,
                encryptor=encryptor,
            )
            return mem_id
        finally:
            pool.putconn(conn)

    return writer


@router.post("/remember", response_model=RememberResponse, dependencies=[Depends(require_license)])
def remember(req: RememberRequest):
    """Store a memory with DLP classification, entity extraction, and temporal supersession."""
    pool = get_pool()

    content = req.content
    agent_name = req.agent_name or "api"
    user_id = req.user_id

    dlp_level = req.dlp_level
    if not dlp_level:
        dlp_level = "confidential" if classify_pii(content) else "internal"

    conn = pool.getconn()
    try:
        conn.autocommit = True
        memory_id, entity_count, edge_count, superseded_count = _persist_memory(
            conn, content,
            agent_name=agent_name, user_id=user_id, dlp_level=dlp_level,
            source_type=req.source_type or "api",
            memory_type=req.memory_type, importance=req.importance,
            encryptor=get_encryptor(),
        )
        return RememberResponse(
            status="remembered",
            memory_id=memory_id,
            dlp_level=dlp_level,
            entity_count=entity_count,
            edge_count=edge_count,
            superseded_count=superseded_count,
        )
    except Exception as e:
        logger.error("Remember failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        pool.putconn(conn)


# ---------------------------------------------------------------------------
# POST /turns/ingest — auto-extraction entry point (PR 3)
# ---------------------------------------------------------------------------

@router.post("/turns/ingest", response_model=TurnIngestResponse)
def turns_ingest(req: TurnIngestRequest):
    """Accept a completed turn from an abi_memory_api client and queue it for extraction.

    Non-blocking by design: the worker drains ``abi_extraction.db`` off the
    request path. Returns 202-equivalent (``queued``) immediately. If
    auto-extraction is disabled the endpoint still answers (``disabled``) so the
    client's fire-and-forget POST never errors.
    """
    queue = get_extraction_queue()
    if queue is None:
        return TurnIngestResponse(status="disabled", queued=0)
    queue.enqueue({
        "agent_name": req.agent_name,
        "user_id": req.user_id,
        "user_content": req.user_content,
        "assistant_content": req.assistant_content,
        "session_id": req.session_id,
        "clearance": req.clearance,
    })
    return TurnIngestResponse(status="queued", queued=1)


# ---------------------------------------------------------------------------
# GET /extraction/stats — auto-extraction ops visibility (PR 3)
# ---------------------------------------------------------------------------

@router.get("/extraction/stats")
def extraction_stats():
    """Cumulative auto-extraction counters + queue backlog + enabled flag.

    Read-only ops endpoint: lets Ground Control watch the extractor save/drop
    ratios (``abi.*`` loggers default to WARNING, which hid the per-turn INFO
    line). Counters reset on container restart; ``pending`` is the durable
    queue backlog awaiting the worker. No license gate — ops-only, no memory
    content is exposed.
    """
    return get_extraction_stats()


# ---------------------------------------------------------------------------
# GET /session-prime
# ---------------------------------------------------------------------------

@router.get("/session-prime")
def session_prime(agent_name: str, recent: int = 8, clusters: int = 10):
    """Compact session-start briefing for Hermes agents: recent activity + key topics.

    Lets an agent recover context instantly at session start (e.g. after a Telegram
    topic session closes/reopens) instead of cold-loading the full history. ``recent``
    = top-N non-superseded memories by recency (decrypted snippet in-process);
    ``clusters`` = top entities by memory-count for the agent. DEK-gated: ``recent``
    snippets are blank on boxes without a DEK; ``clusters`` (entity names stored
    unencrypted) still returned. Bounded; read-only; decrypt never leaves the box.
    """
    recent_n = max(1, min(int(recent), 20))
    clusters_n = max(1, min(int(clusters), 25))
    pool = get_pool()
    conn = pool.getconn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT id::text AS id, content, created_at
                FROM abi_memories
                WHERE agent_name = %s AND superseded_by IS NULL
                ORDER BY created_at DESC LIMIT %s
                """,
                [agent_name, recent_n],
            )
            rows = cur.fetchall()
            cur.execute(
                """
                SELECT e.name AS name, e.type AS type, count(*) AS n
                FROM abi_memory_entities me
                JOIN abi_entities e ON me.entity_id = e.id
                JOIN abi_memories m ON me.memory_id = m.id
                WHERE m.agent_name = %s AND m.superseded_by IS NULL
                GROUP BY e.name, e.type
                ORDER BY n DESC LIMIT %s
                """,
                [agent_name, clusters_n],
            )
            cluster_rows = cur.fetchall()

        encryptor = get_encryptor()
        from ..crypto import EncryptionService
        recent_items = []
        for row in rows:
            ct = row.get("content")
            plain = ct
            if ct and encryptor and EncryptionService.is_encrypted(ct):
                try:
                    plain = encryptor.decrypt(ct)
                except Exception:
                    plain = ""
            snippet = " ".join(str(plain or "").split())
            if len(snippet) > 140:
                snippet = snippet[:137] + "…"
            ts = row["created_at"].isoformat() if row.get("created_at") else None
            recent_items.append({"ts": ts, "snippet": snippet})

        cluster_items = [
            {"name": r["name"], "type": r["type"], "n": int(r["n"])}
            for r in cluster_rows
        ]
        return {
            "agent_name": agent_name,
            "recent": recent_items,
            "clusters": cluster_items,
        }
    except Exception as e:
        logger.error("session-prime failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        pool.putconn(conn)


# ---------------------------------------------------------------------------
# POST /recall
# ---------------------------------------------------------------------------

@router.post("/recall", response_model=RecallResponse)
def recall(req: RecallRequest):
    """Search memories with hybrid BM25+vector search (RRF) and graph boost."""
    pool = get_pool()
    extractor = get_extractor()

    query = req.query
    limit = req.limit
    clearance = req.clearance
    agent_name = req.agent_name or ""

    embedding = _get_embedding(query)

    conn = pool.getconn()
    try:
        where_clause, params = dlp_where(clearance, agent_name)

        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            if embedding:
                # Hybrid BM25 + vector with Reciprocal Rank Fusion
                rrf_k = 60
                fetch_limit = limit * 5

                bm25_where = where_clause + " AND fts @@ websearch_to_tsquery('english', %s)"

                # importance (005 migration) is optional — neutral if absent.
                imp_cte = "importance, " if has_importance_column() else ""
                imp_final = ("COALESCE(v.importance, b.importance) AS importance, "
                             if has_importance_column() else "")
                # memory_type is a sibling of importance in the 005 migration —
                # same column probe gates it. Surfaced in the recall response so
                # agents can see the type/importance the ranker computed.
                mtype_cte = "memory_type, " if has_importance_column() else ""
                mtype_final = ("COALESCE(v.memory_type, b.memory_type) AS memory_type, "
                               if has_importance_column() else "")

                sql = f"""
                    WITH vector_results AS (
                        SELECT id, content, dlp_level, agent_name, user_id,
                               source_type, created_at, metadata, {imp_cte} {mtype_cte}
                               ROW_NUMBER() OVER (ORDER BY embedding <=> %s::vector) AS vector_rank
                        FROM abi_memories
                        WHERE {where_clause}
                        ORDER BY embedding <=> %s::vector
                        LIMIT %s
                    ),
                    bm25_results AS (
                        SELECT id, content, dlp_level, agent_name, user_id,
                               source_type, created_at, metadata, {imp_cte} {mtype_cte}
                               ROW_NUMBER() OVER (ORDER BY ts_rank_cd(fts, websearch_to_tsquery('english', %s)) DESC) AS bm25_rank
                        FROM abi_memories
                        WHERE {bm25_where}
                        LIMIT %s
                    )
                    SELECT COALESCE(v.id, b.id) AS id,
                           COALESCE(v.content, b.content) AS content,
                           COALESCE(v.dlp_level, b.dlp_level) AS dlp_level,
                           COALESCE(v.agent_name, b.agent_name) AS agent_name,
                           COALESCE(v.source_type, b.source_type) AS source_type,
                           COALESCE(v.created_at, b.created_at) AS created_at,
                           COALESCE(v.metadata, b.metadata) AS metadata,
                           {imp_final} {mtype_final}
                           COALESCE(1.0 / ({rrf_k} + v.vector_rank), 0) +
                           COALESCE(1.0 / ({rrf_k} + b.bm25_rank), 0) AS rrf_score
                    FROM vector_results v
                    FULL OUTER JOIN bm25_results b USING (id)
                    ORDER BY rrf_score DESC
                    LIMIT %s
                """
                cur.execute(sql, [
                    str(embedding)] + params + [str(embedding), fetch_limit]
                    + [query] + params + [query, fetch_limit]
                    + [limit])
            else:
                # BM25-only fallback
                imp_sel = "importance, " if has_importance_column() else ""
                mtype_sel = "memory_type, " if has_importance_column() else ""
                _sql = (
                    "SELECT id, content, dlp_level, agent_name, user_id, "
                    "source_type, created_at, metadata, " + imp_sel + mtype_sel +
                    "ts_rank_cd(fts, websearch_to_tsquery('english', %s)) AS rank "
                    "FROM abi_memories "
                    "WHERE " + where_clause + " "
                    "AND fts @@ websearch_to_tsquery('english', %s) "
                    "ORDER BY rank DESC, created_at DESC "
                    "LIMIT %s"
                )
                cur.execute(_sql, [query] + params + [query, limit])

            results = cur.fetchall()

        # Decrypt content if encryption is active
        encryptor = get_encryptor()
        from ..crypto import EncryptionService
        encrypted_count = 0
        for row in results:
            if row["content"] and EncryptionService.is_encrypted(row["content"]):
                encrypted_count += 1
                if encryptor:
                    row["content"] = encryptor.decrypt(row["content"])
                else:
                    logger.error(
                        "Memory %s is encrypted but no DEK available — returning ciphertext. "
                        "License/encryption may be misconfigured.",
                        row["id"],
                    )
                    row["content"] = "[encrypted — decryption key unavailable]"
        if encrypted_count and not encryptor:
            logger.error(
                "%d/%d memories are encrypted but encryptor is disabled. "
                "Check DEK delivery from license Worker.",
                encrypted_count, len(results),
            )

        # Graph boost
        results = _graph_boost(conn, results, query, extractor)

        # Temporal decay + importance weighting + optional cross-encoder rerank.
        results = finalize_recall_ordering(
            results, query,
            decay_enabled=_decay_enabled(),
            reranker=get_reranker() if _rerank_enabled() else None,
            rerank_top_n=_rerank_topn(),
            limit=limit,
        )

        # Record the 'frequently used' signal for returned memories (non-fatal).
        _update_access_tracking(conn, results)

        memories = []
        for row in results:
            mem = MemoryItem(
                id=str(row["id"]),
                content=row["content"],
                dlp_level=row["dlp_level"],
                created_at=row["created_at"].isoformat() if row.get("created_at") else None,
                score=round(float(row.get("score", 0.0)), 4),
                memory_type=row.get("memory_type"),
                importance=round(float(row["importance"]), 2) if row.get("importance") is not None else None,
                source_type=row.get("source_type"),
            )
            memories.append(mem)

        return RecallResponse(memories=memories, count=len(memories))
    except Exception as e:
        logger.error("Recall failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        pool.putconn(conn)


# ---------------------------------------------------------------------------
# DELETE /forget
# ---------------------------------------------------------------------------

@router.delete("/forget", response_model=ForgetResponse, dependencies=[Depends(require_license)])
def forget(req: ForgetRequest):
    """Delete a memory by ID. Only the owning agent can delete."""
    pool = get_pool()
    agent_name = req.agent_name or ""

    conn = pool.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM abi_memories WHERE id = %s AND agent_name = %s RETURNING id",
                [req.memory_id, agent_name],
            )
            deleted = cur.fetchone()
            if deleted:
                return ForgetResponse(status="forgotten", memory_id=req.memory_id)
            return ForgetResponse(status="not_found", error="Memory not found or not owned")
    except Exception as e:
        logger.error("Forget failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        pool.putconn(conn)


# ---------------------------------------------------------------------------
# POST /remember-batch
# ---------------------------------------------------------------------------

@router.post("/remember-batch", response_model=RememberBatchResponse, dependencies=[Depends(require_license)])
def remember_batch(req: RememberBatchRequest):
    """Batch store memories (max 50)."""
    results = []
    for item in req.items:
        try:
            result = remember(item)
            results.append(result)
        except HTTPException as e:
            # Skip failed items, don't abort the whole batch
            results.append(RememberResponse(
                status="error",
                memory_id="",
                dlp_level=item.dlp_level or "internal",
                entity_count=0,
                edge_count=0,
                superseded_count=0,
            ))
    return RememberBatchResponse(results=results, count=len(results))


# ---------------------------------------------------------------------------
# POST /dreamer/candidates + POST /dreamer/densify — Phase 5 densification
#
# The Dreamer's LLM densifier runs per-agent on the HOST (it needs that agent's
# hermes config + LLM creds, which the container cannot see) and the DB port is
# not published to the host, so densification is API-only: these endpoints feed
# the loop. /candidates returns decrypted content; /densify rewrites in place
# (re-encrypt + re-embed + re-extract entities), preserving id/source_type.
# ---------------------------------------------------------------------------

# Verbose source_types worth densifying by default. Excludes session_mined
# (backfill output is already dense) and dreamer/contradiction_alert/identity
# (already terse or ranking-protected).
_DREAMER_DENSIFY_DEFAULT_SOURCES = ("auto_extraction", "agent_tool", "api", "migration")


@router.post("/dreamer/candidates", response_model=DreamerCandidatesResponse,
             dependencies=[Depends(require_license)])
def dreamer_candidates(req: DreamerCandidatesRequest):
    """List un-densified memories for an agent (decrypted) for the densifier.

    Filters to the verbose source set by default, excludes superseded rows, and
    gates on ``metadata->>'densified' IS NULL`` so re-runs are idempotent.
    Content is decrypted exactly as ``/recall`` does.
    """
    pool = get_pool()
    source_types = list(req.source_types) if req.source_types else list(_DREAMER_DENSIFY_DEFAULT_SOURCES)
    imp_sel = ", importance, memory_type" if has_importance_column() else ""

    conn = pool.getconn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                f"""
                SELECT id::text AS id, content, source_type{imp_sel}
                FROM abi_memories
                WHERE agent_name = %s
                  AND superseded_by IS NULL
                  AND source_type = ANY(%s)
                  AND (metadata->>'densified') IS NULL
                ORDER BY created_at ASC
                LIMIT %s OFFSET %s
                """,
                [req.agent_name, source_types, req.limit, req.offset],
            )
            rows = cur.fetchall()

        encryptor = get_encryptor()
        from ..crypto import EncryptionService
        items = []
        for row in rows:
            ct = row.get("content")
            plain = ct
            if ct and EncryptionService.is_encrypted(ct):
                plain = encryptor.decrypt(ct) if encryptor else "[encrypted — decryption key unavailable]"
            items.append(DreamerCandidateItem(
                id=row["id"],
                content=plain or "",
                memory_type=row.get("memory_type"),
                importance=(float(row["importance"]) if row.get("importance") is not None else None),
                source_type=row.get("source_type"),
            ))
        return DreamerCandidatesResponse(agent_name=req.agent_name, items=items, count=len(items))
    except Exception as e:
        logger.error("dreamer/candidates failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        pool.putconn(conn)


@router.post("/dreamer/densify", response_model=DreamerDensifyResponse,
             dependencies=[Depends(require_license)])
def dreamer_densify(req: DreamerDensifyRequest):
    """Apply dense rewrites to memories in place.

    Per item: re-embed + re-encrypt the new content, refresh entities (delete old
    links/edges then re-extract), UPDATE in place preserving id/source_type/
    created_at. Stamps ``metadata.densified=1`` + ``densified_at`` and, on first
    densify only, stashes ``metadata.original_content`` as an audit trail.
    Refuses to proceed if encryption is disabled — densifying into plaintext
    would leak the decrypted content ``/candidates`` just returned.
    """
    pool = get_pool()
    encryptor = get_encryptor()
    if encryptor is None:
        raise HTTPException(
            status_code=409,
            detail="Encryption is disabled (no DEK) — densify refused to avoid storing plaintext rewrites.",
        )
    extractor = get_extractor()

    updated = skipped = errors = 0
    conn = pool.getconn()
    try:
        conn.autocommit = False
        for item in req.items:
            try:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    cur.execute(
                        "SELECT content, metadata FROM abi_memories "
                        "WHERE id = %s::uuid AND agent_name = %s AND superseded_by IS NULL",
                        [item.memory_id, req.agent_name],
                    )
                    row = cur.fetchone()

                if row is None:
                    errors += 1
                    logger.warning("densify: memory %s not found / not owned by %s",
                                   item.memory_id, req.agent_name)
                    conn.rollback()
                    continue

                from ..crypto import EncryptionService
                old_ct = row["content"]
                old_plain = (encryptor.decrypt(old_ct)
                             if EncryptionService.is_encrypted(old_ct) else old_ct) or ""
                new_plain = item.content.strip()

                # Metadata patch: always set densified + densified_at; stash
                # original_content only on first densify (never overwrite the
                # audit trail on re-runs).
                meta = dict(row["metadata"] or {})
                if "original_content" not in meta:
                    meta["original_content"] = old_plain
                meta["densified"] = 1
                meta["densified_at"] = datetime.now(timezone.utc).isoformat()

                # Already maximally dense (LLM returned it unchanged): just stamp
                # so it isn't re-candidate'd; no content/embedding/entity change.
                if new_plain == (old_plain or "").strip():
                    with conn.cursor() as cur:
                        cur.execute(
                            "UPDATE abi_memories SET metadata = %s::jsonb "
                            "WHERE id = %s::uuid AND agent_name = %s",
                            [psycopg2.extras.Json(meta), item.memory_id, req.agent_name],
                        )
                    conn.commit()
                    skipped += 1
                    continue

                embedding = _get_embedding(new_plain)
                content_enc = encryptor.encrypt(new_plain)

                # Drop stale entity links/edges for this memory, then re-extract
                # from the dense content (_store_entities only appends).
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM abi_memory_entities WHERE memory_id = %s::uuid",
                                [item.memory_id])
                    cur.execute("DELETE FROM abi_edges WHERE memory_id = %s::uuid",
                                [item.memory_id])
                    cur.execute(
                        """UPDATE abi_memories
                           SET content = %s,
                               embedding = %s::vector,
                               fts = to_tsvector('english', %s),
                               metadata = %s::jsonb
                           WHERE id = %s::uuid AND agent_name = %s""",
                        [content_enc,
                         (str(embedding) if embedding else None),
                         new_plain, psycopg2.extras.Json(meta),
                         item.memory_id, req.agent_name],
                    )

                entities = extractor.extract(new_plain)
                if entities:
                    _store_entities(conn, item.memory_id, entities, new_plain)

                conn.commit()
                updated += 1
            except Exception as e:
                try:
                    conn.rollback()
                except Exception:
                    pass
                errors += 1
                logger.warning("densify: item %s failed: %s", item.memory_id, e)
        return DreamerDensifyResponse(
            agent_name=req.agent_name, updated=updated, skipped=skipped, errors=errors,
        )
    except Exception as e:
        try:
            conn.rollback()
        except Exception:
            pass
        logger.error("dreamer/densify failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        try:
            conn.autocommit = True
        except Exception:
            pass
        pool.putconn(conn)


# ---------------------------------------------------------------------------
# Internal helpers (ported from ABIMemoryProvider)
# ---------------------------------------------------------------------------

def _store_entities(conn, memory_id: str, entities: list, content: str) -> tuple:
    """Store extracted entities and infer relations."""
    extractor = get_extractor()
    entity_ids = {}
    with conn.cursor() as cur:
        for entity in entities:
            cur.execute(
                """INSERT INTO abi_entities (name, type) VALUES (%s, %s)
                   ON CONFLICT (name, type) DO UPDATE SET name = EXCLUDED.name
                   RETURNING id""",
                [entity.name, entity.type],
            )
            entity_id = cur.fetchone()[0]
            entity_ids[entity.name.lower()] = entity_id

            cur.execute(
                "INSERT INTO abi_memory_entities (memory_id, entity_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                [memory_id, entity_id],
            )

        edges = extractor.infer_relations(entities, content)
        for edge in edges:
            src_id = entity_ids.get(edge.source.name.lower())
            tgt_id = entity_ids.get(edge.target.name.lower())
            if src_id and tgt_id:
                cur.execute(
                    """INSERT INTO abi_edges (source_id, target_id, relation, memory_id)
                       VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING""",
                    [src_id, tgt_id, edge.relation, memory_id],
                )

    return len(entity_ids), len(edges)


def _supersede_old_memories(conn, new_memory_id: str, entities: list, agent_name: str) -> int:
    """Mark older memories about the same entities as superseded."""
    if len(entities) < 2:
        return 0

    entity_names = [e.name.lower() for e in entities]
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id::text FROM abi_entities WHERE lower(name) = ANY(%s)",
                [entity_names],
            )
            entity_ids = [row[0] for row in cur.fetchall()]
            if len(entity_ids) < 2:
                return 0

            cur.execute(
                """SELECT me.memory_id, count(DISTINCT me.entity_id) AS shared
                   FROM abi_memory_entities me
                   WHERE me.entity_id::text = ANY(%s)
                   AND me.memory_id::text != %s
                   GROUP BY me.memory_id
                   HAVING count(DISTINCT me.entity_id) >= 2""",
                [entity_ids, new_memory_id],
            )
            candidates = cur.fetchall()

            superseded = 0
            for row in candidates:
                old_id = row[0]
                cur.execute(
                    """SELECT 1 - (m1.embedding <=> m2.embedding) AS sim
                       FROM abi_memories m1, abi_memories m2
                       WHERE m1.id::text = %s AND m2.id::text = %s
                       AND m1.agent_name = %s
                       AND m1.superseded_by IS NULL""",
                    [str(old_id), new_memory_id, agent_name],
                )
                sim_row = cur.fetchone()
                if sim_row and sim_row[0] and float(sim_row[0]) > 0.75:
                    cur.execute(
                        "UPDATE abi_memories SET superseded_by = %s::uuid, valid_to = NOW() WHERE id::text = %s AND superseded_by IS NULL",
                        [new_memory_id, str(old_id)],
                    )
                    superseded += 1
            return superseded
    except Exception as e:
        logger.warning("Temporal supersession failed: %s", e)
        return 0


def _graph_boost(conn, results: list, query: str, extractor) -> list:
    """Boost scores for memories sharing entities with the query."""
    if not results:
        return results

    try:
        query_entities = extractor.extract(query)
        if not query_entities:
            return results

        query_names = [e.name.lower() for e in query_entities]
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, lower(name) FROM abi_entities WHERE lower(name) = ANY(%s)",
                [query_names],
            )
            query_entity_ids = [str(row[0]) for row in cur.fetchall()]

        if not query_entity_ids:
            return results

        result_ids = [str(r["id"]) for r in results]
        with conn.cursor() as cur:
            cur.execute(
                """SELECT me.memory_id, count(DISTINCT me.entity_id) AS shared_count
                   FROM abi_memory_entities me
                   WHERE me.entity_id::text = ANY(%s)
                   AND me.memory_id::text = ANY(%s)
                   GROUP BY me.memory_id""",
                [query_entity_ids, result_ids],
            )
            boost_map = {str(row[0]): row[1] for row in cur.fetchall()}

        for result in results:
            mem_id = str(result.get("id", ""))
            shared = boost_map.get(mem_id, 0)
            if shared > 0 and "rrf_score" in result:
                result["rrf_score"] = float(result["rrf_score"]) + shared * 0.005

        results.sort(key=lambda r: float(r.get("rrf_score", 0)), reverse=True)
    except Exception as e:
        logger.warning("Graph boost failed: %s", e)

    return results
