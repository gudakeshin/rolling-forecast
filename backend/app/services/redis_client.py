"""One shared Redis client (and connection pool) for the whole process.

Earlier, each caller built a new client, and pinged it, on every call. The
token denylist, the job queue and pending agent actions now share this one.

Outside development and test (``settings.async_jobs_required``), the
in-memory fallbacks are not safe: a token revoked in one process would stay
valid in another, and a pending action approved on one replica would not be
found on the next. There, an unreachable Redis raises instead of falling back.
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from app.config import settings

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_client: Any = None
_client_url: str | None = None


class RedisUnavailable(RuntimeError):
    """Redis is required here but cannot be reached."""


def get_redis(purpose: str = "redis") -> Any | None:
    """The shared client, or None when Redis is not configured in dev/test.

    Raises ``RedisUnavailable`` when Redis is required (production, or
    ``REQUIRE_ASYNC_JOBS``) but missing or unreachable.
    """
    global _client, _client_url
    url = settings.redis_url
    if not url:
        if settings.async_jobs_required:
            raise RedisUnavailable(f"REDIS_URL is required for {purpose} outside development")
        return None
    with _lock:
        if _client is None or _client_url != url:
            import redis

            _client = redis.Redis.from_url(
                url, decode_responses=True, socket_connect_timeout=2, socket_timeout=2
            )
            _client_url = url
        client = _client
    try:
        client.ping()
    except Exception as e:
        if settings.async_jobs_required:
            raise RedisUnavailable(f"Redis unreachable for {purpose}: {e}") from e
        logger.warning("Redis unavailable for %s (%s); using the in-memory fallback", purpose, e)
        return None
    return client


def reset_redis_client() -> None:
    """Drop the cached client (tests, or after REDIS_URL changes)."""
    global _client, _client_url
    with _lock:
        _client = None
        _client_url = None
