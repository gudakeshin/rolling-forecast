"""Shared SlowAPI rate limiter — Redis-backed when REDIS_URL is set."""

from __future__ import annotations

from slowapi import Limiter
from slowapi.util import get_remote_address

from app.config import settings


def _storage_uri() -> str:
    url = (settings.redis_url or "").strip()
    if url:
        return url
    return "memory://"


limiter = Limiter(
    key_func=get_remote_address,
    default_limits=[],
    storage_uri=_storage_uri(),
)
