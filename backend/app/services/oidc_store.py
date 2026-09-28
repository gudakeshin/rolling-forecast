"""OIDC state / PKCE / one-time login code store — Redis when available."""

from __future__ import annotations

import json
import logging
import secrets
import time
from dataclasses import asdict, dataclass
from threading import Lock

from app.config import settings

logger = logging.getLogger(__name__)


@dataclass
class OIDCPending:
    state: str
    nonce: str
    code_verifier: str
    created_at: float


@dataclass
class OneTimeCode:
    user_id: str
    created_at: float


_lock = Lock()
_oidc: dict[str, OIDCPending] = {}
_codes: dict[str, OneTimeCode] = {}
_TTL_SECONDS = 600
_REDIS_OIDC = "rf:oidc:"
_REDIS_CODE = "rf:sso_code:"


def _redis():
    url = (settings.redis_url or "").strip()
    if not url:
        return None
    try:
        import redis

        client = redis.Redis.from_url(url, decode_responses=True)
        client.ping()
        return client
    except Exception as e:
        logger.warning("Redis unavailable for OIDC store (%s); using memory", e)
        return None


def _purge(store: dict, now: float) -> None:
    expired = [k for k, v in store.items() if now - v.created_at > _TTL_SECONDS]
    for k in expired:
        del store[k]


def create_oidc_pending() -> OIDCPending:
    pending = OIDCPending(
        state=secrets.token_urlsafe(32),
        nonce=secrets.token_urlsafe(32),
        code_verifier=secrets.token_urlsafe(64),
        created_at=time.time(),
    )
    r = _redis()
    if r is not None:
        r.setex(_REDIS_OIDC + pending.state, _TTL_SECONDS, json.dumps(asdict(pending)))
        return pending
    with _lock:
        _purge(_oidc, time.time())
        _oidc[pending.state] = pending
    return pending


def pop_oidc_pending(state: str | None) -> OIDCPending | None:
    if not state:
        return None
    r = _redis()
    if r is not None:
        raw = r.getdel(_REDIS_OIDC + state) if hasattr(r, "getdel") else None
        if raw is None:
            # redis-py <4.2 fallback
            pipe = r.pipeline()
            pipe.get(_REDIS_OIDC + state)
            pipe.delete(_REDIS_OIDC + state)
            raw, _ = pipe.execute()
        if not raw:
            return None
        data = json.loads(raw)
        return OIDCPending(**data)
    with _lock:
        _purge(_oidc, time.time())
        return _oidc.pop(state, None)


def pkce_challenge(verifier: str) -> str:
    import base64
    import hashlib

    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def issue_one_time_code(user_id: str) -> str:
    code = secrets.token_urlsafe(32)
    entry = OneTimeCode(user_id=user_id, created_at=time.time())
    r = _redis()
    if r is not None:
        r.setex(_REDIS_CODE + code, _TTL_SECONDS, json.dumps(asdict(entry)))
        return code
    with _lock:
        _purge(_codes, time.time())
        _codes[code] = entry
    return code


def redeem_one_time_code(code: str) -> str | None:
    r = _redis()
    if r is not None:
        pipe = r.pipeline()
        pipe.get(_REDIS_CODE + code)
        pipe.delete(_REDIS_CODE + code)
        raw, _ = pipe.execute()
        if not raw:
            return None
        return json.loads(raw).get("user_id")
    with _lock:
        _purge(_codes, time.time())
        entry = _codes.pop(code, None)
    return entry.user_id if entry else None
