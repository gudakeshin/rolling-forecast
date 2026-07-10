"""RBAC helpers for API endpoints and skills."""

from __future__ import annotations

from fastapi import Depends, HTTPException, status
from sqlalchemy.orm import Query

from app.models.user import User


PERMISSION_ATTR = {
    "input": "can_input",
    "generate": "can_generate",
    "override": "can_override",
    "review": "can_review",
    "publish": "can_publish",
    "admin": "can_admin",
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
