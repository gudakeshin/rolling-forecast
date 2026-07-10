"""Health check endpoint with dependency probes."""

from fastapi import APIRouter
from sqlalchemy import text

from app.config import settings
from app.database import SessionLocal

router = APIRouter(tags=["health"])


@router.get("/health")
async def health_check():
    checks = {
        "database": "unknown",
        "llm_configured": bool(settings.anthropic_api_key),
        "chroma_dir": settings.chroma_persist_dir,
    }
    status = "healthy"
    try:
        db = SessionLocal()
        try:
            db.execute(text("SELECT 1"))
            checks["database"] = "ok"
        finally:
            db.close()
    except Exception as e:
        checks["database"] = f"error: {e}"
        status = "degraded"

    try:
        from pathlib import Path
        Path(settings.chroma_persist_dir).mkdir(parents=True, exist_ok=True)
        checks["chroma"] = "ok"
    except Exception as e:
        checks["chroma"] = f"error: {e}"
        status = "degraded"

    return {
        "status": status,
        "service": "rolling-forecast-api",
        "env": settings.app_env,
        "checks": checks,
    }
