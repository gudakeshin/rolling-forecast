"""Notification creation and role-based fan-out helpers."""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models.notification import Notification
from app.models.user import Role, User

logger = logging.getLogger(__name__)


def create_notification(
    db: Session,
    *,
    user_id: str,
    kind: str,
    title: str,
    body: str | None = None,
    entity_type: str | None = None,
    entity_id: str | None = None,
    link_panel: str | None = None,
    link_params: dict[str, Any] | None = None,
    dedup_key: str | None = None,
) -> Notification | None:
    """Create a notification, or silently no-op if `dedup_key` already fired for this user."""
    if dedup_key:
        existing = (
            db.query(Notification)
            .filter(Notification.user_id == user_id, Notification.dedup_key == dedup_key)
            .first()
        )
        if existing:
            return None
    n = Notification(
        user_id=user_id,
        kind=kind,
        title=title,
        body=body,
        entity_type=entity_type,
        entity_id=entity_id,
        link_panel=link_panel,
        link_params=link_params,
        dedup_key=dedup_key,
    )
    db.add(n)
    db.flush()
    return n


def notify_users(
    db: Session,
    users: list[User],
    *,
    kind: str,
    title: str,
    body: str | None = None,
    entity_type: str | None = None,
    entity_id: str | None = None,
    link_panel: str | None = None,
    link_params: dict[str, Any] | None = None,
    dedup_key_prefix: str | None = None,
) -> list[Notification]:
    """Notify each user, deduping per-user via `f"{dedup_key_prefix}:{user.id}"`."""
    created = []
    for u in users:
        dedup_key = f"{dedup_key_prefix}:{u.id}" if dedup_key_prefix else None
        n = create_notification(
            db,
            user_id=u.id,
            kind=kind,
            title=title,
            body=body,
            entity_type=entity_type,
            entity_id=entity_id,
            link_panel=link_panel,
            link_params=link_params,
            dedup_key=dedup_key,
        )
        if n:
            created.append(n)
    return created


def users_eligible_for_approval_step(db: Session, required_role: str) -> list[User]:
    """Mirrors the role-gate in app/api/approvals.py::_can_decide — anyone who
    could act on a step with this required_role."""
    conditions = [Role.name == required_role, Role.can_admin.is_(True)]
    if required_role == "reviewer":
        conditions.append(Role.can_review.is_(True))
    return (
        db.query(User)
        .join(Role)
        .filter(User.is_active.is_(True), or_(*conditions))
        .all()
    )


def users_with_permission(db: Session, permission: str) -> list[User]:
    """Active users whose role has the given boolean permission flag set."""
    return (
        db.query(User)
        .join(Role)
        .filter(User.is_active.is_(True), getattr(Role, permission).is_(True))
        .all()
    )


def users_in_business_unit_with_permission(
    db: Session, business_unit: str, permission: str
) -> list[User]:
    return (
        db.query(User)
        .join(Role)
        .filter(
            User.is_active.is_(True),
            User.business_unit == business_unit,
            getattr(Role, permission).is_(True),
        )
        .all()
    )
