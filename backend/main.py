"""
CodeSense — FastAPI Application Entry Point
Wires together all routers, middleware, and lifecycle events.
"""
from __future__ import annotations

import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse

from backend.core.config import get_settings, logger, setup_logging
from backend.routers.api import (
    health_router,
    analysis_router,
    explain_router,
    execute_router,
    mentor_router,
    practice_router,
)
from backend.routers.websocket import ws_router
from backend.services.llm_service import llm_service


# ─── LIFESPAN ─────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup / shutdown lifecycle."""
    settings = get_settings()
    setup_logging(settings.debug)
    logger.info("codesense_startup", version=settings.app_version, host=settings.host, port=settings.port)

    # Resolve LLM backend on startup (warm-up)
    backend = await llm_service._resolve_backend()
    logger.info("llm_ready", backend=backend)

    yield

    logger.info("codesense_shutdown")


# ─── APP FACTORY ──────────────────────────────────────────────────────────────

def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title="CodeSense API",
        description=(
            "Backend for CodeSense — the Python learning platform that teaches "
            "understanding, not vibe coding. Provides AST analysis, LLM explanations, "
            "sandboxed execution, and Socratic mentoring."
        ),
        version=settings.app_version,
        docs_url="/api/docs",
        redoc_url="/api/redoc",
        lifespan=lifespan,
    )

    # ── Middleware ──────────────────────────────────────────────────────────
    app.add_middleware(GZipMiddleware, minimum_size=500)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ── Request timing middleware ───────────────────────────────────────────
    @app.middleware("http")
    async def add_timing_header(request: Request, call_next):
        t0 = time.perf_counter()
        response = await call_next(request)
        elapsed_ms = (time.perf_counter() - t0) * 1000
        response.headers["X-Response-Time-Ms"] = str(round(elapsed_ms, 2))
        return response

    # ── Global exception handler ────────────────────────────────────────────
    @app.exception_handler(Exception)
    async def global_exception_handler(request: Request, exc: Exception):
        logger.error("unhandled_exception", path=request.url.path, error=str(exc))
        return JSONResponse(
            status_code=500,
            content={"error": "Internal server error", "detail": str(exc)[:200]},
        )

    # ── Routers ─────────────────────────────────────────────────────────────
    app.include_router(health_router)
    app.include_router(analysis_router)
    app.include_router(explain_router)
    app.include_router(execute_router)
    app.include_router(mentor_router)
    app.include_router(practice_router)
    app.include_router(ws_router)

    return app


app = create_app()


# ─── ENTRY POINT ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        "main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.debug,
        log_level="debug" if settings.debug else "info",
        ws_ping_interval=20,
        ws_ping_timeout=30,
    )
