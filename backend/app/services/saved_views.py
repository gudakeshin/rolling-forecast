"""Saved views -- personal shortcuts to reopen a panel with specific filters.

Unlike model_presets, there is no global/shared bucket: a saved view always
belongs to exactly one company (the creator's business_unit_id, resolved
server-side -- never trusted from the request) and is only visible to and
modifiable by the user who created it.
"""

from __future__ import annotations

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.saved_view import SavedView
from app.models.user import User
from app.services.permissions import can_access_business_unit


def _find_by_name(
    db: Session, name: str, *, business_unit_id: str, created_by: str
) -> SavedView | None:
    return (
        db.query(SavedView)
        .filter(
            func.lower(SavedView.name) == name.strip().lower(),
            SavedView.business_unit_id == business_unit_id,
            SavedView.created_by == created_by,
        )
        .first()
    )


def create_view(
    db: Session,
    *,
    name: str,
    panel_type: str,
    panel_params: dict,
    actor: User,
) -> SavedView:
    name = (name or "").strip()
    if not name:
        raise ValueError("View name is required")
    if not panel_type:
        raise ValueError("panel_type is required")
    if not actor.business_unit_id:
        # No shared bucket -- a user with no company assigned can't save a
        # view, mirroring line_item_scope_filter's "no BU -> sees/can't
        # create anything" rule.
        raise ValueError("You must belong to a company to save a view")

    if _find_by_name(
        db, name, business_unit_id=actor.business_unit_id, created_by=actor.id
    ) is not None:
        raise ValueError(f"A saved view named '{name}' already exists")

    view = SavedView(
        name=name,
        panel_type=panel_type,
        panel_params=panel_params or {},
        business_unit_id=actor.business_unit_id,
        created_by=actor.id,
    )
    db.add(view)
    db.commit()
    db.refresh(view)
    return view


def list_views(db: Session, *, actor: User) -> list[SavedView]:
    return (
        db.query(SavedView)
        .filter(SavedView.created_by == actor.id)
        .order_by(SavedView.created_at.desc())
        .all()
    )


def _get_owned(db: Session, view_id: str, *, actor: User) -> SavedView | None:
    view = db.query(SavedView).filter(SavedView.id == view_id).first()
    if view is None:
        return None
    if view.created_by != actor.id:
        return None
    if not can_access_business_unit(actor, view.business_unit_id):
        return None
    return view


def delete_view(db: Session, view_id: str, *, actor: User) -> None:
    view = _get_owned(db, view_id, actor=actor)
    if view is None:
        # Same shape whether the view doesn't exist, belongs to someone
        # else, or belongs to another company -- never reveal which.
        raise ValueError("Saved view not found")
    db.delete(view)
    db.commit()
