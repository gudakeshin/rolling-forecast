"""JWT denylist + refresh-token rotation (Redis with DB fallback)."""

from __future__ import annotations

import hashlib
import logging
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from jose import jwt
from sqlalchemy.orm import Session

from app.config import settings
from app.models.auth_tokens import RefreshToken, TokenDenylist

logger = logging.getLogger(__name__)

_REDIS_DENY_PREFIX = "rf:deny:jti:"


def _redis():
    if not settings.redis_url:
        return None
    try:
        import redis

        client = redis.Redis.from_url(settings.redis_url, decode_responses=True)
        client.ping()
        return client
    except Exception as e:
        logger.warning("Redis unavailable for token denylist (%s)", e)
        return None


def _hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def create_access_token(data: dict[str, Any], *, jti: str | None = None) -> tuple[str, str, datetime]:
    """Return (token, jti, expires_at). Includes jti for revocation."""
    to_encode = data.copy()
    token_jti = jti or str(uuid.uuid4())
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.jwt_expiry_minutes)
    to_encode.update({"exp": expire, "jti": token_jti, "type": "access"})
    token = jwt.encode(to_encode, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)
    return token, token_jti, expire


def revoke_jti(db: Session, jti: str, expires_at: datetime) -> None:
    """Denylist an access-token jti until it would have expired."""
    if not jti:
        return
    now = datetime.now(timezone.utc)
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    ttl = max(int((expires_at - now).total_seconds()), 1)

    r = _redis()
    if r is not None:
        try:
            r.setex(_REDIS_DENY_PREFIX + jti, ttl, "1")
        except Exception:
            logger.debug("Redis denylist write failed", exc_info=True)

    # Always persist to DB as durable fallback
    existing = db.query(TokenDenylist).filter(TokenDenylist.jti == jti).first()
    if existing:
        existing.expires_at = expires_at
    else:
        db.add(TokenDenylist(jti=jti, expires_at=expires_at))
    db.commit()


def is_jti_revoked(db: Session, jti: str | None) -> bool:
    if not jti:
        return False
    r = _redis()
    if r is not None:
        try:
            if r.get(_REDIS_DENY_PREFIX + jti):
                return True
        except Exception:
            pass

    now = datetime.now(timezone.utc)
    row = db.query(TokenDenylist).filter(TokenDenylist.jti == jti).first()
    if row is None:
        return False
    exp = row.expires_at
    if exp.tzinfo is None:
        exp = exp.replace(tzinfo=timezone.utc)
    if exp < now:
        db.delete(row)
        db.commit()
        return False
    return True


def issue_refresh_token(db: Session, user_id: str, *, family_id: str | None = None) -> str:
    """Create a new refresh token (opaque) and persist its hash. Returns raw token."""
    raw = secrets.token_urlsafe(48)
    fam = family_id or str(uuid.uuid4())
    expires = datetime.now(timezone.utc) + timedelta(days=settings.jwt_refresh_days)
    db.add(
        RefreshToken(
            user_id=user_id,
            token_hash=_hash_token(raw),
            family_id=fam,
            expires_at=expires,
            revoked=False,
        )
    )
    db.commit()
    return raw


def rotate_refresh_token(db: Session, raw_refresh: str) -> tuple[str, str]:
    """Validate + rotate refresh token.

    Returns (new_raw_refresh, user_id).
    Raises ValueError on invalid / reused / revoked tokens.
    """
    th = _hash_token(raw_refresh)
    row = db.query(RefreshToken).filter(RefreshToken.token_hash == th).first()
    if row is None:
        raise ValueError("Invalid refresh token")

    now = datetime.now(timezone.utc)
    exp = row.expires_at
    if exp.tzinfo is None:
        exp = exp.replace(tzinfo=timezone.utc)

    # Reuse of an already-rotated token → revoke entire family
    if row.revoked or row.replaced_by is not None:
        db.query(RefreshToken).filter(RefreshToken.family_id == row.family_id).update(
            {"revoked": True}
        )
        db.commit()
        raise ValueError("Refresh token reuse detected")

    if exp < now:
        row.revoked = True
        db.commit()
        raise ValueError("Refresh token expired")

    new_raw = secrets.token_urlsafe(48)
    new_row = RefreshToken(
        user_id=row.user_id,
        token_hash=_hash_token(new_raw),
        family_id=row.family_id,
        expires_at=datetime.now(timezone.utc) + timedelta(days=settings.jwt_refresh_days),
        revoked=False,
    )
    db.add(new_row)
    db.flush()
    row.revoked = True
    row.replaced_by = new_row.id
    db.commit()
    return new_raw, row.user_id


def revoke_refresh_token(db: Session, raw_refresh: str | None) -> None:
    if not raw_refresh:
        return
    th = _hash_token(raw_refresh)
    row = db.query(RefreshToken).filter(RefreshToken.token_hash == th).first()
    if row is None:
        return
    db.query(RefreshToken).filter(RefreshToken.family_id == row.family_id).update(
        {"revoked": True}
    )
    db.commit()


def revoke_access_from_bearer(db: Session, access_token: str | None) -> None:
    """Decode access JWT (even if expired) and denylist its jti."""
    if not access_token:
        return
    try:
        payload = jwt.decode(
            access_token,
            settings.jwt_secret_key,
            algorithms=[settings.jwt_algorithm],
            options={"verify_exp": False},
        )
    except Exception:
        return
    jti = payload.get("jti")
    exp = payload.get("exp")
    if not jti or not exp:
        return
    expires_at = datetime.fromtimestamp(int(exp), tz=timezone.utc)
    revoke_jti(db, jti, expires_at)
