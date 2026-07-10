"""Health endpoints — liveness vs readiness."""

from fastapi import APIRouter, Response
from sqlalchemy import text

from app.config import settings
from app.database import SessionLocal

router = APIRouter(tags=["health"])


@router.get("/livez")
async def livez():
    """Process is up — no dependency checks."""
    return {"status": "ok"}


@router.get("/readyz")
async def readyz(response: Response):
    """Ready to serve traffic: DB reachable + API key present in production."""
    checks: dict = {}
    ready = True

    try:
        db = SessionLocal()
        try:
            db.execute(text("SELECT 1"))
            checks["database"] = "ok"
        finally:
            db.close()
    except Exception as e:
        checks["database"] = f"error: {e}"
        ready = False

    if settings.is_production and not settings.anthropic_api_key:
        checks["llm"] = "missing ANTHROPIC_API_KEY"
        ready = False
    else:
        checks["llm"] = "ok" if settings.anthropic_api_key else "not_configured"

    # Redis: required when configured (locks / queue / rate-limit backing)
    redis_url = (settings.redis_url or "").strip()
    if redis_url:
        try:
            import redis

            client = redis.Redis.from_url(redis_url, socket_connect_timeout=2)
            client.ping()
            checks["redis"] = "ok"
        except Exception as e:
            checks["redis"] = f"error: {e}"
            ready = False
    else:
        checks["redis"] = "not_configured"

    # Migrations-at-head: best-effort Alembic check
    try:
        from alembic.script import ScriptDirectory
        from alembic.config import Config
        import os

        backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        cfg = Config(os.path.join(backend_dir, "alembic.ini"))
        script = ScriptDirectory.from_config(cfg)
        head = script.get_current_head()
        checks["migrations_head"] = head or "unknown"
    except Exception as e:
        checks["migrations_head"] = f"error: {e}"

    if not ready:
        response.status_code = 503
    return {
        "status": "ready" if ready else "not_ready",
        "checks": checks,
    }


@router.get("/health")
async def health_check():
    """Backward-compatible health probe (prefer /livez and /readyz)."""
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
