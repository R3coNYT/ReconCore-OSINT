"""FastAPI entry point."""
from __future__ import annotations

import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.core.config import settings
from app.core.logging import get_logger, setup_logging

setup_logging()
logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    from app.db.session import SessionLocal
    from app.plugins import registry

    # The registry is synchronised on startup so plugins present in the code
    # show up in the UI immediately, without being implicitly enabled.
    with SessionLocal() as db:
        try:
            registry.sync_registry(db)
            db.commit()
        except Exception as exc:  # e.g. the database is not migrated yet
            db.rollback()
            logger.warning("Plugin registry synchronisation deferred: %s", exc)
    yield


app = FastAPI(
    title=settings.project_name,
    description=(
        "OSINT investigation platform: correlation of public information, source "
        "traceability and human validation of every hypothesis."
    ),
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs" if not settings.is_production else None,
    redoc_url=None,
    openapi_url="/openapi.json" if not settings.is_production else None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    max_age=600,
)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    """Security headers plus a per-request correlation id."""
    request_id = request.headers.get("x-request-id") or uuid.uuid4().hex[:16]
    started = time.perf_counter()
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
    response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
    if settings.is_production:
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    response.headers["X-Response-Time-ms"] = f"{(time.perf_counter() - started) * 1000:.1f}"
    return response


@app.exception_handler(ValueError)
async def value_error_handler(request: Request, exc: ValueError) -> JSONResponse:
    return JSONResponse(status_code=400, content={"detail": str(exc)})


@app.get("/health", tags=["system"])
def health() -> dict:
    """Liveness probe (used by Docker and Nginx)."""
    from sqlalchemy import text

    from app.db.session import engine

    database_ok = True
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except Exception:
        database_ok = False
    return {
        "status": "ok" if database_ok else "degraded",
        "database": database_ok,
        "environment": settings.reconcore_env,
        "version": app.version,
    }


from app.api.v1.router import api_router  # noqa: E402  (imported after app creation)

app.include_router(api_router, prefix=settings.api_v1_prefix)
