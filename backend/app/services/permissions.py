"""RBAC helpers for API endpoints and skills."""

from __future__ import annotations

from fastapi import Depends, HTTPException, status
from sqlalchemy.orm import Query, Session

from app.models.user import User


PERMISSION_ATTR = {
    "input": "can_input",
    "generate": "can_generate",
    "override": "can_override",
    "review": "can_review",
    "publish": "can_publish",
    "admin": "can_admin",
    "manage_drivers": "can_manage_drivers",
}

# Roles allowed to approve/reject at version level (aligned with seeded roles)
APPROVER_ROLES = frozenset({"admin", "reviewer", "publisher"})
PUBLISHER_ROLES = frozenset({"admin", "publisher"})


def user_has_permission(user: User, permission: str) -> bool:
    attr = PERMISSION_ATTR.get(permission)
    if not attr or not user.role:
        return False
    return bool(getattr(user.role, attr, False))


def can_view_all_bus(user: User) -> bool:
    """True if user may read line items across all business units."""
    if not user.role:
        return False
    if getattr(user.role, "can_view_all_bus", False) or user.role.can_admin:
        return True
    return user.role.name == "admin"


def line_item_scope_filter(query: Query, user: User, line_item_model) -> Query:
    """Restrict a LineItem query to the caller's BU unless they can view all.

    Line items with NULL business_unit are treated as shared (visible to all).
    """
    if can_view_all_bus(user):
        return query
    bu = user.business_unit
    if not bu:
        # No BU assigned and no cross-BU privilege → only shared (null BU) rows
        return query.filter(line_item_model.business_unit.is_(None))
    return query.filter(
        (line_item_model.business_unit == bu) | (line_item_model.business_unit.is_(None))
    )


def resolve_skill_user(context) -> User | None:
    """Resolve the acting User from a SkillContext (prefers attached user object)."""
    user = getattr(context, "user", None)
    if user is not None:
        return user
    cm = getattr(context, "context_manager", None)
    if cm is not None and getattr(cm, "user", None) is not None:
        return cm.user
    user_id = getattr(context, "user_id", None)
    db: Session | None = getattr(context, "db", None)
    if user_id and db is not None:
        return db.query(User).filter(User.id == user_id).first()
    return None


def scoped_line_items(db: Session, user: User | None, *filters) -> Query:
    """LineItem query restricted to the caller's BU scope when user is known."""
    from app.models.line_item import LineItem

    q = db.query(LineItem)
    for f in filters:
        q = q.filter(f)
    if user is None:
        return q
    return line_item_scope_filter(q, user, LineItem)


def allowed_line_item_ids(db: Session, user: User | None) -> set[int] | None:
    """Return allowed LineItem IDs, or None when the user may view all BUs.

    Use with ``.filter(col.in_(ids))`` when ``ids is not None``.
    """
    if user is None or can_view_all_bus(user):
        return None
    return {li.id for li in scoped_line_items(db, user).all()}


def user_can_view_line_item(user: User | None, line_item) -> bool:
    """True if the user may read this line item under BU scope."""
    if user is None or line_item is None:
        return True
    if can_view_all_bus(user):
        return True
    li_bu = getattr(line_item, "business_unit", None)
    if li_bu is None:
        return True  # shared
    return li_bu == user.business_unit


def driver_scope_filter(query: Query, user: User, driver_model) -> Query:
    """Restrict a Driver query to the caller's BU unless they can view all.

    Drivers with NULL business_unit are treated as shared (visible to all).
    """
    if can_view_all_bus(user):
        return query
    bu = user.business_unit
    if not bu:
        return query.filter(driver_model.business_unit.is_(None))
    return query.filter(
        (driver_model.business_unit == bu) | (driver_model.business_unit.is_(None))
    )


def scoped_drivers(db: Session, user: User | None, *filters) -> Query:
    """Driver query restricted to the caller's BU scope when user is known."""
    from app.models.driver import Driver

    q = db.query(Driver)
    for f in filters:
        q = q.filter(f)
    if user is None:
        return q
    return driver_scope_filter(q, user, Driver)


def user_can_view_driver(user: User | None, driver) -> bool:
    """True if the user may read this driver under BU scope."""
    if user is None or driver is None:
        return True
    if can_view_all_bus(user):
        return True
    d_bu = getattr(driver, "business_unit", None)
    if d_bu is None:
        return True
    return d_bu == user.business_unit


def require_permission(permission: str):
    """FastAPI dependency factory that enforces a Role.can_* flag."""
    # Lazy import to avoid circular dependency with app.api.auth
    from app.api.auth import get_current_user

    def _dependency(current_user: User = Depends(get_current_user)) -> User:
        if not user_has_permission(current_user, permission):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Permission '{permission}' required",
            )
        return current_user

    return _dependency


def require_any_role(*role_names: str):
    """FastAPI dependency factory that requires one of the given role names."""
    from app.api.auth import get_current_user

    allowed = frozenset(role_names)

    def _dependency(current_user: User = Depends(get_current_user)) -> User:
        if not current_user.role or current_user.role.name not in allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"One of roles {sorted(allowed)} required",
            )
        return current_user

    return _dependency


def can_approve_forecast(user: User) -> bool:
    return bool(user.role) and (
        user.role.name in APPROVER_ROLES or user.role.can_review
    )


def can_publish_forecast(user: User) -> bool:
    return bool(user.role) and (
        user.role.name in PUBLISHER_ROLES or user.role.can_publish
    )
