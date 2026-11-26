"""Agent writes held for the user's approval, and which conversations carry
untrusted text. See ``app.domain.skill_safety`` for why.

Records live in Redis when configured (so any replica can confirm them),
otherwise in process memory (dev/test only; ``get_redis`` refuses the
fallback outside development).
"""

from __future__ import annotations

import json
import time
import uuid
from typing import Any

from app.services.redis_client import get_redis

PENDING_TTL_SECONDS = 15 * 60
TAINT_TTL_SECONDS = 7 * 24 * 60 * 60

_PENDING_PREFIX = "rf:pending-action:"
_TAINT_PREFIX = "rf:untrusted-conv:"

_local_pending: dict[str, tuple[float, dict[str, Any]]] = {}
_local_taint: dict[str, float] = {}


def _now() -> float:
    return time.time()


def mark_untrusted(conversation_id: str | None) -> None:
    """Record that text from outside the user's messages entered this conversation."""
    if not conversation_id:
        return
    r = get_redis("pending agent actions")
    if r is not None:
        r.setex(_TAINT_PREFIX + conversation_id, TAINT_TTL_SECONDS, "1")
        return
    _local_taint[conversation_id] = _now() + TAINT_TTL_SECONDS


def is_untrusted(conversation_id: str | None) -> bool:
    if not conversation_id:
        return False
    r = get_redis("pending agent actions")
    if r is not None:
        return bool(r.exists(_TAINT_PREFIX + conversation_id))
    expires = _local_taint.get(conversation_id)
    return expires is not None and expires > _now()


def create_pending(
    *, user_id: str, conversation_id: str | None, skill_name: str, params: dict[str, Any]
) -> str:
    action_id = uuid.uuid4().hex
    record = {
        "id": action_id,
        "user_id": user_id,
        "conversation_id": conversation_id,
        "skill_name": skill_name,
        "params": params,
        "created_at": _now(),
    }
    r = get_redis("pending agent actions")
    if r is not None:
        r.setex(_PENDING_PREFIX + action_id, PENDING_TTL_SECONDS, json.dumps(record, default=str))
    else:
        _local_pending[action_id] = (_now() + PENDING_TTL_SECONDS, record)
    return action_id


def take_pending(action_id: str, *, user_id: str) -> dict[str, Any] | None:
    """Remove and return the action if it belongs to ``user_id`` and has not
    expired. One use only: a second confirm finds nothing."""
    r = get_redis("pending agent actions")
    record: dict[str, Any] | None
    if r is not None:
        raw = r.getdel(_PENDING_PREFIX + action_id)
        record = json.loads(raw) if raw else None
    else:
        entry = _local_pending.pop(action_id, None)
        record = entry[1] if entry and entry[0] > _now() else None
    if record is None or record.get("user_id") != user_id:
        if record is not None:
            # Someone else's id: put it back for its owner rather than burning it.
            _restore(record)
        return None
    return record


def discard_pending(action_id: str, *, user_id: str) -> bool:
    return take_pending(action_id, user_id=user_id) is not None


def _restore(record: dict[str, Any]) -> None:
    remaining = PENDING_TTL_SECONDS - (_now() - float(record.get("created_at", _now())))
    if remaining <= 0:
        return
    r = get_redis("pending agent actions")
    if r is not None:
        r.setex(_PENDING_PREFIX + record["id"], int(remaining) or 1, json.dumps(record, default=str))
    else:
        _local_pending[record["id"]] = (_now() + remaining, record)


def reset_local_state() -> None:
    """Tests only."""
    _local_pending.clear()
    _local_taint.clear()
