"""In-memory OIDC state / PKCE / one-time login code store (Redis in Phase 4)."""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass
from threading import Lock


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
    with _lock:
        _purge(_oidc, time.time())
        _oidc[pending.state] = pending
    return pending


def pop_oidc_pending(state: str | None) -> OIDCPending | None:
    if not state:
        return None
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
    with _lock:
        _purge(_codes, time.time())
        _codes[code] = OneTimeCode(user_id=user_id, created_at=time.time())
    return code


def redeem_one_time_code(code: str) -> str | None:
    with _lock:
        _purge(_codes, time.time())
        entry = _codes.pop(code, None)
    return entry.user_id if entry else None
