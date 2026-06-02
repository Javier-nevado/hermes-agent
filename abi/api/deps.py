"""Dependency injection for the ABI Memory API server."""

from __future__ import annotations

import logging
import os
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
