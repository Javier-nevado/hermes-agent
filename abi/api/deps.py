"""Dependency injection for the ABI Memory API server."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

import psycopg2
import psycopg2.pool

from abi.memory.dlp import dlp_where
from abi.memory.entities import EntityExtractor
from abi.memory.pii import classify_pii

logger = logging.getLogger(__name__)

# Module-level singletons
_db_pool: Optional[psycopg2.pool.ThreadedConnectionPool] = None
_entity_extractor: Optional[EntityExtractor] = None
_start_time: Optional[float] = None
_license_manager = None
_encryptor = None
_reranker_singleton = None
_has_importance_cache: Optional[bool] = None


def _env_bool(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


def apply_pending_migrations() -> None:
    """Apply any ``abi/sql/*.sql`` not yet recorded in ``abi_schema_migrations``.

    Idempotent and per-file isolated: a failing migration is logged but does not
    abort API startup. Runs as the abi_agent DB user (table owner), so this is
    also the reliable application path for new migrations on customer docker
    boxes (the only in-repo runner otherwise globs just ``002_*`` in abi-setup).
    """
    pool = get_pool()
    conn = pool.getconn()
    try:
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute(
                "CREATE TABLE IF NOT EXISTS abi_schema_migrations ("
                "filename TEXT PRIMARY KEY, applied_at TIMESTAMPTZ DEFAULT NOW())"
            )
            cur.execute("SELECT filename FROM abi_schema_migrations")
            applied = {row[0] for row in cur.fetchall()}

        import abi
        sql_dir = Path(abi.__file__).parent / "sql"
        for sql_file in sorted(sql_dir.glob("*.sql")):
            name = sql_file.name
            if name in applied:
                continue
            logger.info("Applying migration %s", name)
            try:
                with conn.cursor() as cur:
                    cur.execute(sql_file.read_text())  # each file wraps in BEGIN/COMMIT
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO abi_schema_migrations (filename) VALUES (%s) "
                        "ON CONFLICT DO NOTHING",
                        [name],
                    )
                logger.info("Applied migration %s", name)
            except Exception as exc:
                logger.error("Migration %s failed (skipping): %s", name, exc)
    finally:
        pool.putconn(conn)


def init_deps() -> None:
    """Initialize all shared resources. Called once at app startup."""
    global _db_pool, _entity_extractor, _start_time

    import time
    _start_time = time.time()

    # DB connection pool
    db_url = os.environ.get(
        "ABI_DATABASE_URL",
        "postgresql://abi_agent:abi_local_dev_2026@localhost:5432/abi_memory",
    )
    _db_pool = psycopg2.pool.ThreadedConnectionPool(
        minconn=2, maxconn=10, dsn=db_url
    )
    logger.info("DB pool initialized (2-10 connections)")

    # Apply pending schema migrations (idempotent; per-file isolated).
    try:
        apply_pending_migrations()
    except Exception as exc:
        logger.error("Migration runner error (non-fatal): %s", exc)

    # Entity extractor
    _entity_extractor = EntityExtractor()

    # Pre-warm embeddings
    try:
        from abi.memory.embeddings import get_embedding
        get_embedding("warmup")
        logger.info("Embedding model pre-warmed")
    except Exception as e:
        logger.warning("Embedding pre-warm failed: %s", e)


def get_pool() -> psycopg2.pool.ThreadedConnectionPool:
    if _db_pool is None:
        raise RuntimeError("DB pool not initialized")
    return _db_pool


def get_extractor() -> EntityExtractor:
    if _entity_extractor is None:
        raise RuntimeError("Entity extractor not initialized")
    return _entity_extractor


def get_start_time() -> float:
    if _start_time is None:
        raise RuntimeError("Server not started")
    return _start_time


def init_license() -> None:
    """Initialize the license manager."""
    global _license_manager
    from .license import LicenseManager
    _license_manager = LicenseManager()


def get_license_manager():
    if _license_manager is None:
        raise RuntimeError("License manager not initialized")
    return _license_manager


def init_encryptor() -> None:
    """Initialize the encryption service from the cached DEK."""
    global _encryptor
    from .crypto import EncryptionService
    dek = get_license_manager().get_dek()
    if dek:
        _encryptor = EncryptionService(dek)
        logger.info("Encryption service initialized (AES-256-GCM)")
    else:
        logger.warning("No DEK available — encryption disabled, content stored plaintext")


def get_encryptor():
    """Return the EncryptionService, or None if encryption is disabled."""
    return _encryptor


def get_reranker():
    """Return the shared Reranker singleton, or None if reranking is disabled.

    Lazy-loaded on first call (the first recall triggers the one-time model
    download). Disabled unless ``ABI_MEMORY_RERANK_ENABLED`` is set.
    """
    global _reranker_singleton
    if _reranker_singleton is not None:
        return _reranker_singleton
    if not _env_bool("ABI_MEMORY_RERANK_ENABLED"):
        return None
    try:
        from abi.memory.model_cache import get_cache_root
        from abi.memory.reranker import Reranker
        _reranker_singleton = Reranker(get_cache_root())
    except Exception as exc:
        logger.warning("Reranker init failed: %s", exc)
        _reranker_singleton = None
    return _reranker_singleton


def has_importance_column() -> bool:
    """True if abi_memories.importance exists (005 migration applied). Cached."""
    global _has_importance_cache
    if _has_importance_cache is not None:
        return _has_importance_cache
    try:
        conn = get_pool().getconn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT 1 FROM information_schema.columns "
                    "WHERE table_name = 'abi_memories' AND column_name = 'importance'"
                )
                _has_importance_cache = cur.fetchone() is not None
        finally:
            get_pool().putconn(conn)
    except Exception:
        _has_importance_cache = False
    return _has_importance_cache
