"""Saved views -- named panel + filter combinations, stored server-side so they
follow the user across browsers and can be shared with their company."""

from __future__ import annotations

import json
from datetime import timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.api.auth import get_current_user
from app.database import get_db
from app.models.saved_view import SavedView
from app.models.user import User

router = APIRouter(prefix="/saved-views", tags=["saved-views"])

# Panel params are filters and ids, never data -- cap them so the table can't
# be used as a blob store.
_MAX_PARAMS_BYTES = 8 * 1024


class SavedViewCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    panel_type: str = Field(min_length=1, max_length=64)
    panel_params: dict = Field(default_factory=dict)
    is_shared: bool = False

    @field_validator("name")
    @classmethod
    def _strip_name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("name must not be blank")
        return v

    @field_validator("panel_params")
    @classmethod
    def _cap_params(cls, v: dict) -> dict:
        if len(json.dumps(v, default=str)) > _MAX_PARAMS_BYTES:
            raise ValueError(f"panel_params exceeds {_MAX_PARAMS_BYTES} bytes")
        return v


class SavedViewUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    is_shared: bool | None = None


def _dict(view: SavedView, current_user: User) -> dict:
    created = view.created_at
    if created is not None and created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)  # SQLite drops tzinfo
    return {
        "id": view.id,
        "name": view.name,
        "panel_type": view.panel_type,
        "panel_params": view.panel_params or {},
        "is_shared": view.is_shared,
        "is_owner": view.user_id == current_user.id,
        "created_at": created.isoformat() if created else None,
    }


def _in_active_company(query, user: User):
    """Views saved in the caller's active company -- explicit, not left to the
    tenant net, so a cross-company (unrestricted) caller still sees only the
    company they're working in."""
    if user.business_unit_id is None:
        return query.filter(SavedView.business_unit_id.is_(None))
    return query.filter(SavedView.business_unit_id == user.business_unit_id)


def _visible(db: Session, user: User):
    return _in_active_company(db.query(SavedView), user).filter(
        or_(SavedView.user_id == user.id, SavedView.is_shared.is_(True))
    )


def _owned_or_error(db: Session, view_id: str, user: User) -> SavedView:
    view = _visible(db, user).filter(SavedView.id == view_id).first()
    if view is None:
        raise HTTPException(404, "Saved view not found")
    if view.user_id != user.id:
        raise HTTPException(403, "Only the owner can change a saved view")
    return view


@router.get("")
async def list_saved_views(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    rows = _visible(db, current_user).order_by(SavedView.created_at).all()
    return {"views": [_dict(v, current_user) for v in rows]}


@router.post("", status_code=201)
async def create_saved_view(
    body: SavedViewCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    view = SavedView(
        user_id=current_user.id,
        business_unit_id=current_user.business_unit_id,
        name=body.name,
        panel_type=body.panel_type,
        panel_params=body.panel_params,
        is_shared=body.is_shared,
    )
    db.add(view)
    db.commit()
    db.refresh(view)
    return _dict(view, current_user)


@router.patch("/{view_id}")
async def update_saved_view(
    view_id: str,
    body: SavedViewUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    view = _owned_or_error(db, view_id, current_user)
    if body.name is not None:
        name = body.name.strip()
        if not name:
            raise HTTPException(422, "name must not be blank")
        view.name = name
    if body.is_shared is not None:
        view.is_shared = body.is_shared
    db.commit()
    db.refresh(view)
    return _dict(view, current_user)


@router.delete("/{view_id}", status_code=204)
async def delete_saved_view(
    view_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    view = _owned_or_error(db, view_id, current_user)
    db.delete(view)
    db.commit()
