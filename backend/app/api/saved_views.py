"""Saved view APIs -- personal shortcuts to reopen a panel with specific filters."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.saved_view import SavedView
from app.models.user import User
from app.services.permissions import require_permission
from app.services.saved_views import create_view, delete_view, list_views

router = APIRouter(prefix="/saved-views", tags=["saved-views"])


class SavedViewCreate(BaseModel):
    name: str
    panel_type: str
    panel_params: dict = {}


def _serialize(view: SavedView) -> dict:
    return {
        "id": view.id,
        "name": view.name,
        "panel_type": view.panel_type,
        "panel_params": view.panel_params,
        "business_unit_id": view.business_unit_id,
        "created_at": view.created_at.isoformat() if view.created_at else None,
    }


@router.post("", status_code=201)
async def create_saved_view(
    body: SavedViewCreate,
    current_user: User = Depends(require_permission("input")),
    db: Session = Depends(get_db),
):
    try:
        view = create_view(
            db,
            name=body.name,
            panel_type=body.panel_type,
            panel_params=body.panel_params,
            actor=current_user,
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return _serialize(view)


@router.get("")
async def list_saved_views(
    current_user: User = Depends(require_permission("input")),
    db: Session = Depends(get_db),
):
    rows = list_views(db, actor=current_user)
    return {"views": [_serialize(r) for r in rows], "count": len(rows)}


@router.delete("/{view_id}")
async def delete_saved_view(
    view_id: str,
    current_user: User = Depends(require_permission("input")),
    db: Session = Depends(get_db),
):
    try:
        delete_view(db, view_id, actor=current_user)
    except ValueError as e:
        raise HTTPException(404, str(e)) from e
    return {"id": view_id, "deleted": True}
