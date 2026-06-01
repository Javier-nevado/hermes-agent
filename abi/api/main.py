"""FastAPI application factory for the ABI Memory API server."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from .deps import init_deps, init_license, get_license_manager, get_pool

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: initialize DB pool, embeddings, entity extractor, license."""
    init_deps()
    init_license()
    mgr = get_license_manager()
    await mgr.start_refresh()
    logger.info("ABI Memory API server ready (license: %s)", mgr.get_status()["status"])
    yield
    # Shutdown: cancel refresh, close DB pool
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

    app.include_router(memory_router, tags=["memory"])
    app.include_router(health_router, tags=["health"])
    app.include_router(entities_router, tags=["entities"])

    return app


app = create_app()
