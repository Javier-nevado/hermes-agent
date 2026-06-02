"""Health and stats routes."""

from __future__ import annotations

import logging
import time

from fastapi import APIRouter, HTTPException

from ..deps import get_pool, get_start_time, get_license_manager, get_encryptor
from ..schemas import HealthResponse, StatsResponse

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/health", response_model=HealthResponse)
def health():
    """Check DB connection, embedding availability, and uptime."""
    pool = get_pool()
    start_time = get_start_time()

    # DB check
    db_status = "ok"
    try:
        conn = pool.getconn()
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
        pool.putconn(conn)
    except Exception as e:
        db_status = f"error: {e}"

    # Embedding check
    emb_status = "ok"
    try:
        from abi.memory.embeddings import get_embedding
        result = get_embedding("health check")
        if result is None:
            emb_status = "unavailable"
    except Exception as e:
        emb_status = f"error: {e}"

    # License check
    license_mgr = get_license_manager()
    lic = license_mgr.get_status()

    # Encryption check
    enc_status = "active" if get_encryptor() else "disabled"

    # Tables check
    tables_status = "enabled" if get_license_manager().tables_enabled() else "disabled"

    return HealthResponse(
        status="ok" if db_status == "ok" else "degraded",
        db=db_status,
        embeddings=emb_status,
        uptime_seconds=round(time.time() - start_time, 1),
        license_status=lic["status"],
        license_tier=lic.get("tier"),
        license_expires=lic.get("expires"),
        encryption=enc_status,
        tables=tables_status,
    )


@router.get("/agent/clearance")
def agent_clearance(agent_name: str):
    """Look up an agent's clearance from abi_agents table."""
    pool = get_pool()
    conn = pool.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT clearance, display_name, role FROM abi_agents WHERE username = %s",
                [agent_name],
            )
            row = cur.fetchone()
            if row:
                return {
                    "agent_name": agent_name,
                    "clearance": row[0],
                    "display_name": row[1],
                    "role": row[2],
                }
            return {"agent_name": agent_name, "clearance": "external"}
    except Exception as e:
        logger.error("Clearance lookup failed: %s", e)
        return {"agent_name": agent_name, "clearance": "external"}
    finally:
        pool.putconn(conn)


@router.get("/stats", response_model=StatsResponse)
def stats():
    """Return memory, entity, and edge counts."""
    pool = get_pool()
    conn = pool.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM abi_memories")
            memory_count = cur.fetchone()[0]

            cur.execute("SELECT count(*) FROM abi_entities")
            entity_count = cur.fetchone()[0]

            cur.execute("SELECT count(*) FROM abi_edges")
            edge_count = cur.fetchone()[0]

        return StatsResponse(
            memory_count=memory_count,
            entity_count=entity_count,
            edge_count=edge_count,
        )
    except Exception as e:
        logger.error("Stats query failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        pool.putconn(conn)
