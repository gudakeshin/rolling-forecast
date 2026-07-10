"""Shared SlowAPI rate limiter (in-memory; Redis-backed in Phase 4)."""

from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address, default_limits=[])
