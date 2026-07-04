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
from typing import List

import psycopg2.extras
from fastapi import APIRouter, Depends, HTTPException

from abi.memory.dlp import dlp_where
from abi.memory.pii import classify_pii
from abi.memory.ranking import finalize_recall_ordering

from ..deps import get_pool, get_extractor, get_encryptor, get_reranker, has_importance_column
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
)

logger = logging.getLogger(__name__)
router = APIRouter()


def _decay_enabled() -> bool:
    return os.environ.get("ABI_MEMORY_DECAY_ENABLED", "").strip().lower() in ("1", "true", "yes", "on")


def _rerank_enabled() -> bool:
    return os.environ.get("ABI_MEMORY_RERANK_ENABLED", "").strip().lower() in ("1", "true", "yes", "on")


def _rerank_topn() -> int:
    return int(os.environ.get("ABI_MEMORY_RERANK_TOPN", "20"))


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

@router.post("/remember", response_model=RememberResponse, dependencies=[Depends(require_license)])
def remember(req: RememberRequest):
    """Store a memory with DLP classification, entity extraction, and temporal supersession."""
    pool = get_pool()
    extractor = get_extractor()

    content = req.content
    dlp_level = req.dlp_level
    agent_name = req.agent_name or "api"
    user_id = req.user_id

    # Auto-classify DLP if not specified
    if not dlp_level:
        has_pii = classify_pii(content)
        dlp_level = "confidential" if has_pii else "internal"

    embedding = _get_embedding(content)
    memory_id = str(uuid.uuid4())

    # Save plaintext for entity extraction and FTS before encrypting
    plaintext = content
    encryptor = get_encryptor()
    if encryptor:
        content = encryptor.encrypt(plaintext)

    conn = pool.getconn()
    try:
        conn.autocommit = True
        with conn.cursor() as cur:
            if embedding:
                if encryptor:
                    # Encrypted path: pass fts explicitly from plaintext
                    cur.execute(
                        """
                        INSERT INTO abi_memories (id, content, embedding, dlp_level, agent_name, user_id, source_type, fts)
                        VALUES (%s, %s, %s::vector, %s, %s, %s, 'api', to_tsvector('english', %s))
                        RETURNING id
                        """,
                        [memory_id, content, str(embedding), dlp_level, agent_name, user_id, plaintext],
                    )
                else:
                    # Plaintext path: trigger generates fts automatically
                    cur.execute(
                        """
                        INSERT INTO abi_memories (id, content, embedding, dlp_level, agent_name, user_id, source_type)
                        VALUES (%s, %s, %s::vector, %s, %s, %s, 'api')
                        RETURNING id
                        """,
                        [memory_id, content, str(embedding), dlp_level, agent_name, user_id],
                    )
            else:
                if encryptor:
                    cur.execute(
                        """
                        INSERT INTO abi_memories (id, content, dlp_level, agent_name, user_id, source_type, fts)
                        VALUES (%s, %s, %s, %s, %s, 'api', to_tsvector('english', %s))
                        RETURNING id
                        """,
                        [memory_id, content, dlp_level, agent_name, user_id, plaintext],
                    )
                else:
                    cur.execute(
                        """
                        INSERT INTO abi_memories (id, content, dlp_level, agent_name, user_id, source_type)
                        VALUES (%s, %s, %s, %s, %s, 'api')
                        RETURNING id
                        """,
                        [memory_id, content, dlp_level, agent_name, user_id],
                    )

        # Entity extraction + storage (always from plaintext)
        entity_count = 0
        edge_count = 0
        superseded_count = 0
        try:
            entities = extractor.extract(plaintext)
            if entities:
                entity_count, edge_count = _store_entities(
                    conn, memory_id, entities, plaintext
                )
                superseded_count = _supersede_old_memories(
                    conn, memory_id, entities, agent_name
                )
        except Exception as e:
            logger.warning("Entity extraction failed for memory %s: %s", memory_id, e)

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

                sql = f"""
                    WITH vector_results AS (
                        SELECT id, content, dlp_level, agent_name, user_id,
                               source_type, created_at, metadata, {imp_cte}
                               ROW_NUMBER() OVER (ORDER BY embedding <=> %s::vector) AS vector_rank
                        FROM abi_memories
                        WHERE {where_clause}
                        ORDER BY embedding <=> %s::vector
                        LIMIT %s
                    ),
                    bm25_results AS (
                        SELECT id, content, dlp_level, agent_name, user_id,
                               source_type, created_at, metadata, {imp_cte}
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
                           {imp_final}
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
                _sql = (
                    "SELECT id, content, dlp_level, agent_name, user_id, "
                    "source_type, created_at, metadata, " + imp_sel +
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

        memories = []
        for row in results:
            mem = MemoryItem(
                id=str(row["id"]),
                content=row["content"],
                dlp_level=row["dlp_level"],
                created_at=row["created_at"].isoformat() if row.get("created_at") else None,
                score=round(float(row.get("score", 0.0)), 4),
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
