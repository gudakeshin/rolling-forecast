"""Audit event writer — append-only."""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

from app.models.audit import AuditEvent

logger = logging.getLogger(__name__)


def record_audit(
    db: Session,
    *,
    action: str,
    entity_type: str,
    entity_id: str | None = None,
    actor_id: str | None = None,
    actor_username: str | None = None,
    details: dict[str, Any] | None = None,
    ip_address: str | None = None,
    request_id: str | None = None,
    notes: str | None = None,
    commit: bool = False,
) -> AuditEvent:
    """Append an immutable audit event. Callers should not update/delete AuditEvent rows."""
    if request_id is None:
        try:
            from app.middleware import get_request_id

            request_id = get_request_id()
        except Exception:
            request_id = None
    event = AuditEvent(
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        actor_id=actor_id,
        actor_username=actor_username,
        details=details or {},
        ip_address=ip_address,
        request_id=request_id,
        notes=notes,
    )
    db.add(event)
    if commit:
        db.commit()
        db.refresh(event)
    else:
        db.flush()
    logger.info(
        "audit action=%s entity=%s/%s actor=%s",
        action,
        entity_type,
        entity_id,
        actor_username or actor_id,
    )
    return event
