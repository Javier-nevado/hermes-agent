"""FastAPI application factory for the ABI Memory API server."""

from __future__ import annotations

import logging
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI

from .deps import (
    init_deps,
    init_license,
    get_license_manager,
    get_pool,
    init_encryptor,
    init_extraction_queue,
    shutdown_extraction_queue,
)

logger = logging.getLogger(__name__)


def _configure_abi_logging() -> None:
    """Attach a stderr handler to the ``abi`` logger so app INFO lines surface.

    uvicorn's default ``LOGGING_CONFIG`` leaves the root logger with **no
    handler** (``root = {}``). Its own ``uvicorn``/``uvicorn.access`` loggers
    carry dedicated handlers with ``propagate=False``, so they emit fine — but
    every logger that propagates to root (everything under ``abi.*``: the
    auto-extraction per-turn stats, "DB pool initialized", "Extraction queue
    started", migration lines) is silently dropped. Configuring the ``abi``
    logger directly is the contained fix — it doesn't touch global logging, and
    ``propagate=False`` prevents any future root handler from double-emitting.
    """
    abi = logging.getLogger("abi")
    if abi.handlers:
        return
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    abi.addHandler(handler)
    abi.setLevel(logging.INFO)
    abi.propagate = False


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: initialize DB pool, embeddings, entity extractor, license, extraction queue."""
    init_deps()
    init_license()
    mgr = get_license_manager()
    await mgr.start_refresh()
    init_encryptor()
    # Auto-extraction queue last — it depends on the pool/encryptor/reranker being up.
    # No-op (and never fatal) when ABI_MEMORY_AUTO_EXTRACT_ENABLED is unset.
    init_extraction_queue()
    logger.info("ABI Memory API server ready (license: %s)", mgr.get_status()["status"])
    yield
    # Shutdown: drain extraction queue, cancel refresh, close DB pool
    shutdown_extraction_queue()
    mgr.stop_refresh()
    try:
        pool = get_pool()
        pool.closeall()
        logger.info("DB pool closed")
    except Exception:
        pass


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(
        title="ABI Memory API",
        version="1.0.0",
        description="REST API for ABI memory operations",
        lifespan=lifespan,
    )

    # Register routers
    from .routes.memory import router as memory_router
    from .routes.health import router as health_router
    from .routes.entities import router as entities_router
    from .routes.tables import router as tables_router

    app.include_router(memory_router, tags=["memory"])
    app.include_router(health_router, tags=["health"])
    app.include_router(entities_router, tags=["entities"])
    app.include_router(tables_router, tags=["tables"])

    return app


_configure_abi_logging()
app = create_app()
