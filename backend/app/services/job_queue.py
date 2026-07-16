"""Optional arq-backed job queue with Redis-persisted status."""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from app.config import settings

logger = logging.getLogger(__name__)

# In-memory fallback when Redis is unavailable (dev / tests / single process)
_LOCAL_JOBS: dict[str, dict[str, Any]] = {}
_JOB_TTL_SECONDS = 60 * 60 * 24  # 24h
_REDIS_PREFIX = "rf:job:"


def redis_configured() -> bool:
    return bool(settings.redis_url)


def _redis():
    if not settings.redis_url:
        return None
    try:
        import redis

        client = redis.Redis.from_url(settings.redis_url, decode_responses=True)
        client.ping()
        return client
    except Exception as e:
        logger.warning("Redis unavailable for job queue (%s)", e)
        return None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _save(record: dict[str, Any]) -> None:
    job_id = record["job_id"]
    _LOCAL_JOBS[job_id] = record
    r = _redis()
    if r is not None:
        r.setex(_REDIS_PREFIX + job_id, _JOB_TTL_SECONDS, json.dumps(record))


async def enqueue_generate_baseline(
    *,
    version_name: str,
    params: dict[str, Any],
    user_id: str | None,
    conversation_id: str | None = None,
) -> dict[str, Any]:
    """Enqueue baseline generation. Persists status in Redis when available."""
    job_id = str(uuid.uuid4())
    record = {
        "job_id": job_id,
        "kind": "generate_baseline",
        "status": "queued",
        "version_name": version_name,
        "params": params,
        "user_id": user_id,
        "conversation_id": conversation_id,
        "created_at": _now(),
        "result": None,
        "error": None,
    }

    if redis_configured():
        try:
            from arq import create_pool
            from arq.connections import RedisSettings

            redis_pool = await create_pool(RedisSettings.from_dsn(settings.redis_url))
            await redis_pool.enqueue_job(
                "run_generate_baseline",
                job_id,
                params,
                user_id,
                _job_id=job_id,
            )
            await redis_pool.aclose()
            record["backend"] = "arq"
            _save(record)
            return record
        except Exception:
            logger.exception("arq enqueue failed; falling back to local queue marker")

    record["status"] = "sync_required"
    record["backend"] = "local"
    if settings.require_async_jobs:
        record["message"] = (
            "Async job queue unavailable — Redis/arq is required "
            "(REQUIRE_ASYNC_JOBS=true). Start Redis and the arq worker."
        )
    else:
        record["message"] = (
            "Redis/arq unavailable — run generate_baseline synchronously "
            "(omit async_job=true) or start the arq worker with REDIS_URL set."
        )
    _save(record)
    return record


def get_job(job_id: str) -> dict[str, Any] | None:
    r = _redis()
    if r is not None:
        raw = r.get(_REDIS_PREFIX + job_id)
        if raw:
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                pass
    return _LOCAL_JOBS.get(job_id)


def update_job(job_id: str, **fields: Any) -> None:
    record = get_job(job_id) or {"job_id": job_id}
    record.update(fields)
    record["updated_at"] = _now()
    _save(record)
