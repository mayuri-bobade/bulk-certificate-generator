"""FastAPI application entrypoint."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.config import get_settings
from app.database import SessionLocal, init_db
from app.middleware import RequestContextMiddleware
from app.routers import certificates, jobs, verify
from app.worker import recover_incomplete_jobs, shutdown_executor

settings = get_settings()

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("app.main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings.storage_path.mkdir(parents=True, exist_ok=True)
    init_db()
    recovered = recover_incomplete_jobs()
    if recovered:
        logger.info("Re-dispatched %s incomplete job(s) on startup", recovered)
    yield
    shutdown_executor()


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description=(
        "Backend API for bulk certificate generation. Submit a list of "
        "recipients, track generation progress, and download generated PDFs."
    ),
    lifespan=lifespan,
)

app.add_middleware(RequestContextMiddleware)

app.include_router(jobs.router)
app.include_router(certificates.router)
app.include_router(verify.router)


@app.get("/health", tags=["meta"], summary="Liveness probe")
def health() -> dict[str, str]:
    return {"status": "ok", "version": settings.app_version}


@app.get("/ready", tags=["meta"], summary="Readiness probe (checks DB + storage)")
def ready() -> JSONResponse:
    checks: dict[str, str] = {}

    db = SessionLocal()
    try:
        db.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as exc:  # noqa: BLE001
        checks["database"] = f"error: {exc}"
    finally:
        db.close()

    try:
        settings.storage_path.mkdir(parents=True, exist_ok=True)
        probe = settings.storage_path / ".write_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        checks["storage"] = "ok"
    except Exception as exc:  # noqa: BLE001
        checks["storage"] = f"error: {exc}"

    healthy = all(value == "ok" for value in checks.values())
    return JSONResponse(
        status_code=200 if healthy else 503,
        content={"status": "ready" if healthy else "not-ready", "checks": checks},
    )


@app.get("/", tags=["meta"], include_in_schema=False)
def root() -> dict[str, str]:
    return {
        "service": settings.app_name,
        "version": settings.app_version,
        "docs": "/docs",
        "openapi": "/openapi.json",
    }
