"""Optimistic locking helpers for concurrent forecast edits."""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.models.forecast import ForecastLineResult


class LockConflictError(Exception):
    def __init__(self, message: str, holder: str | None = None):
        super().__init__(message)
        self.holder = holder


# In-process lock registry: key -> {user_id, expires_at, username}
# For multi-worker prod, replace with Redis; this satisfies single-process + documents the contract.
_LOCKS: dict[str, dict[str, Any]] = {}
DEFAULT_TTL_SECONDS = 120


def _key(version_id: str, line_item_id: int, period: str) -> str:
    return f"{version_id}:{line_item_id}:{period}"


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
    existing = _LOCKS.get(k)
    if existing and existing["expires_at"] > now and existing["user_id"] != user_id:
        raise LockConflictError(
            f"Line item locked by {existing.get('username') or existing['user_id']} until {existing['expires_at'].isoformat()}",
            holder=existing["user_id"],
        )
    entry = {
        "user_id": user_id,
        "username": username,
        "expires_at": now + timedelta(seconds=ttl_seconds),
        "version_id": version_id,
        "line_item_id": line_item_id,
        "period": period,
    }
    _LOCKS[k] = entry
    return entry


def release_lock(
    version_id: str,
    line_item_id: int,
    period: str,
    user_id: str,
) -> bool:
    k = _key(version_id, line_item_id, period)
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
    """
    Soft optimistic concurrency: if client sends expected_updated_at and the row
    has reviewed_at/updated differently, raise LockConflictError.
    Uses reviewed_at as a proxy when no dedicated row_version column exists.
    """
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
