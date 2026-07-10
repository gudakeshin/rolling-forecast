"""Optional arq-backed job queue with in-process fallback."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from app.config import settings

logger = logging.getLogger(__name__)

# In-memory job store used when Redis/arq is unavailable (dev / tests)
_LOCAL_JOBS: dict[str, dict[str, Any]] = {}


def redis_configured() -> bool:
    return bool(settings.redis_url)


async def enqueue_generate_baseline(
    *,
    version_name: str,
    params: dict[str, Any],
    user_id: str | None,
    conversation_id: str | None = None,
) -> dict[str, Any]:
    """Enqueue baseline generation. Falls back to a local pending job record.

    When Redis is configured, pushes to arq. The worker runs the skill.
    Callers that need immediate results should run the skill synchronously
    instead of using this helper.
    """
    job_id = str(uuid.uuid4())
    record = {
        "job_id": job_id,
        "kind": "generate_baseline",
        "status": "queued",
        "version_name": version_name,
        "params": params,
        "user_id": user_id,
        "conversation_id": conversation_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "result": None,
        "error": None,
    }

    if redis_configured():
        try:
            from arq import create_pool
            from arq.connections import RedisSettings

            redis = await create_pool(RedisSettings.from_dsn(settings.redis_url))
            await redis.enqueue_job(
                "run_generate_baseline",
                job_id,
                params,
                user_id,
                _job_id=job_id,
            )
            await redis.aclose()
            _LOCAL_JOBS[job_id] = record
            return {**record, "backend": "arq"}
        except Exception:
            logger.exception("arq enqueue failed; falling back to local queue marker")

    record["status"] = "sync_required"
    record["message"] = (
        "Redis/arq unavailable — run generate_baseline synchronously "
        "(omit async_job=true) or start the arq worker with REDIS_URL set."
    )
    _LOCAL_JOBS[job_id] = record
    return {**record, "backend": "local"}


def get_job(job_id: str) -> dict[str, Any] | None:
    return _LOCAL_JOBS.get(job_id)


def update_job(job_id: str, **fields: Any) -> None:
    if job_id in _LOCAL_JOBS:
        _LOCAL_JOBS[job_id].update(fields)
        _LOCAL_JOBS[job_id]["updated_at"] = datetime.now(timezone.utc).isoformat()
