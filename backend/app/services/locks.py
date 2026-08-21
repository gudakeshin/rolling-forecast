"""Optimistic locking helpers for concurrent forecast edits.

Uses Redis when REDIS_URL is configured (multi-worker safe); otherwise an
in-process dict (single-worker / tests).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.models.forecast import ForecastLineResult

logger = logging.getLogger(__name__)


class LockConflictError(Exception):
    def __init__(self, message: str, holder: str | None = None):
        super().__init__(message)
        self.holder = holder


_LOCKS: dict[str, dict[str, Any]] = {}
DEFAULT_TTL_SECONDS = 120
_redis = None
_redis_checked = False


def _redis_client():
    global _redis, _redis_checked
    if _redis_checked:
        return _redis
    _redis_checked = True
    try:
        from app.config import settings
        url = getattr(settings, "redis_url", "") or ""
        if not url:
            return None
        import redis
        client = redis.Redis.from_url(url, decode_responses=True)
        client.ping()
        _redis = client
        logger.info("LockService using Redis at %s", url.split("@")[-1])
        return _redis
    except Exception as e:
        logger.warning("Redis unavailable for locks (%s); using in-process store", e)
        _redis = None
        return None


def _key(version_id: str, line_item_id: int, period: str) -> str:
    return f"lock:{version_id}:{line_item_id}:{period}"


def acquire_lock(
    version_id: str,
    line_item_id: int,
    period: str,
    user_id: str,
    username: str | None = None,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
) -> dict[str, Any]:
    """Acquire an edit lock. Raises LockConflictError if held by another user."""
    k = _key(version_id, line_item_id, period)
    now = datetime.now(timezone.utc)
    entry = {
        "user_id": user_id,
        "username": username,
        "expires_at": (now + timedelta(seconds=ttl_seconds)).isoformat(),
        "version_id": version_id,
        "line_item_id": line_item_id,
        "period": period,
    }

    r = _redis_client()
    if r is not None:
        payload = json.dumps(entry)
        # SET NX with TTL — atomic
        ok = r.set(k, payload, nx=True, ex=ttl_seconds)
        if not ok:
            existing_raw = r.get(k)
            existing = json.loads(existing_raw) if existing_raw else {}
            if existing.get("user_id") != user_id:
                raise LockConflictError(
                    f"Line item locked by {existing.get('username') or existing.get('user_id')}",
                    holder=existing.get("user_id"),
                )
            r.set(k, payload, ex=ttl_seconds)  # refresh own lock
        return entry

    # In-process fallback
    existing = _LOCKS.get(k)
    if existing and existing["expires_at"] > now and existing["user_id"] != user_id:
        raise LockConflictError(
            f"Line item locked by {existing.get('username') or existing['user_id']} until {existing['expires_at'].isoformat()}",
            holder=existing["user_id"],
        )
    mem_entry = {
        **entry,
        "expires_at": now + timedelta(seconds=ttl_seconds),
    }
    _LOCKS[k] = mem_entry
    return mem_entry


def release_lock(
    version_id: str,
    line_item_id: int,
    period: str,
    user_id: str,
) -> bool:
    k = _key(version_id, line_item_id, period)
    r = _redis_client()
    if r is not None:
        existing_raw = r.get(k)
        if not existing_raw:
            return True
        existing = json.loads(existing_raw)
        if existing.get("user_id") != user_id:
            return False
        r.delete(k)
        return True

    existing = _LOCKS.get(k)
    if not existing:
        return True
    if existing["user_id"] != user_id:
        return False
    del _LOCKS[k]
    return True


def check_row_version(
    db: Session,
    result_id: str,
    expected_updated_at: str | None,
) -> ForecastLineResult:
    """Soft optimistic concurrency via reviewed_at proxy."""
    result = db.query(ForecastLineResult).filter(ForecastLineResult.id == result_id).first()
    if not result:
        raise ValueError("Forecast line result not found")
    if expected_updated_at and result.reviewed_at:
        client_ts = expected_updated_at.replace("Z", "+00:00")
        server = result.reviewed_at
        if server.tzinfo is None:
            server = server.replace(tzinfo=timezone.utc)
        try:
            client = datetime.fromisoformat(client_ts)
            if client.tzinfo is None:
                client = client.replace(tzinfo=timezone.utc)
            if abs((server - client).total_seconds()) > 1 and server > client:
                raise LockConflictError(
                    "Row was modified by another user. Refresh and retry.",
                )
        except ValueError:
            pass
    return result


def purge_expired_locks() -> int:
    now = datetime.now(timezone.utc)
    expired = [k for k, v in _LOCKS.items() if v["expires_at"] <= now]
    for k in expired:
        del _LOCKS[k]
    return len(expired)
