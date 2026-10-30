"""Admin CRUD for integration connections (secrets write-only)."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.integration import IntegrationConnection
from app.models.user import User
from app.services.audit import record_audit
from app.services.integration_safety import assert_safe_integration_url
from app.services.permissions import require_permission
from app.services.secret_box import encrypt_secret

router = APIRouter(prefix="/admin/integrations", tags=["admin-integrations"])


class ConnectionCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    kind: str = Field(..., pattern="^(warehouse|erp)$")
    url: str = Field(..., min_length=1, description="SQLAlchemy URL or ERP base URL")
    token: str | None = None
    description: str | None = None
    enabled: bool = True
    # Opt-in unattended nightly pull (see WorkerSettings.cron_jobs). A warehouse
    # connection needs default_query; an erp connection needs default_relative_path.
    auto_pull_enabled: bool = False
    default_query: str | None = None
    default_relative_path: str | None = None
    default_source_name: str | None = None


class ConnectionUpdate(BaseModel):
    name: str | None = None
    url: str | None = None
    token: str | None = None
    description: str | None = None
    enabled: bool | None = None
    auto_pull_enabled: bool | None = None
    default_query: str | None = None
    default_relative_path: str | None = None
    default_source_name: str | None = None


def _require_auto_pull_defaults(
    kind: str, default_query: str | None, default_relative_path: str | None
) -> None:
    if kind == "warehouse" and not default_query:
        raise HTTPException(400, "auto_pull_enabled on a warehouse connection requires default_query")
    if kind == "erp" and not default_relative_path:
        raise HTTPException(400, "auto_pull_enabled on an erp connection requires default_relative_path")


def _public(conn: IntegrationConnection) -> dict:
    return {
        "id": conn.id,
        "name": conn.name,
        "kind": conn.kind,
        "enabled": conn.enabled,
        "description": conn.description,
        "has_token": bool(conn.encrypted_token),
        "auto_pull_enabled": conn.auto_pull_enabled,
        "default_query": conn.default_query,
        "default_relative_path": conn.default_relative_path,
        "default_source_name": conn.default_source_name,
        "created_at": conn.created_at.isoformat() if conn.created_at else None,
        "updated_at": conn.updated_at.isoformat() if conn.updated_at else None,
    }


@router.get("")
async def list_connections(
    current_user: User = Depends(require_permission("admin")),
    db: Session = Depends(get_db),
):
    rows = db.query(IntegrationConnection).order_by(IntegrationConnection.name).all()
    return [_public(r) for r in rows]


@router.post("")
async def create_connection(
    body: ConnectionCreate,
    current_user: User = Depends(require_permission("admin")),
    db: Session = Depends(get_db),
):
    if db.query(IntegrationConnection).filter(IntegrationConnection.name == body.name).first():
        raise HTTPException(409, f"Connection '{body.name}' already exists")
    try:
        assert_safe_integration_url(body.url, body.kind)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    if body.auto_pull_enabled:
        _require_auto_pull_defaults(body.kind, body.default_query, body.default_relative_path)
    conn = IntegrationConnection(
        name=body.name,
        kind=body.kind,
        encrypted_url=encrypt_secret(body.url),
        encrypted_token=encrypt_secret(body.token) if body.token else None,
        description=body.description,
        enabled=body.enabled,
        auto_pull_enabled=body.auto_pull_enabled,
        default_query=body.default_query,
        default_relative_path=body.default_relative_path,
        default_source_name=body.default_source_name,
        created_by=current_user.id,
    )
    db.add(conn)
    record_audit(
        db,
        action="admin.integration_create",
        entity_type="integration_connection",
        entity_id=None,
        actor_id=current_user.id,
        actor_username=current_user.username,
        details={"name": body.name, "kind": body.kind},
    )
    db.commit()
    db.refresh(conn)
    return _public(conn)


@router.patch("/{connection_id}")
async def update_connection(
    connection_id: str,
    body: ConnectionUpdate,
    current_user: User = Depends(require_permission("admin")),
    db: Session = Depends(get_db),
):
    conn = db.query(IntegrationConnection).filter(IntegrationConnection.id == connection_id).first()
    if not conn:
        raise HTTPException(404, "Connection not found")
    if body.name is not None:
        conn.name = body.name
    if body.url is not None:
        try:
            assert_safe_integration_url(body.url, conn.kind)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        conn.encrypted_url = encrypt_secret(body.url)
    if body.token is not None:
        conn.encrypted_token = encrypt_secret(body.token) if body.token else None
    if body.description is not None:
        conn.description = body.description
    if body.enabled is not None:
        conn.enabled = body.enabled
    if body.default_query is not None:
        conn.default_query = body.default_query
    if body.default_relative_path is not None:
        conn.default_relative_path = body.default_relative_path
    if body.default_source_name is not None:
        conn.default_source_name = body.default_source_name
    if body.auto_pull_enabled is not None:
        conn.auto_pull_enabled = body.auto_pull_enabled
    if conn.auto_pull_enabled:
        _require_auto_pull_defaults(conn.kind, conn.default_query, conn.default_relative_path)
    conn.updated_at = datetime.now(timezone.utc)
    record_audit(
        db,
        action="admin.integration_update",
        entity_type="integration_connection",
        entity_id=conn.id,
        actor_id=current_user.id,
        actor_username=current_user.username,
        details={"fields": body.model_dump(exclude_none=True).keys()},
    )
    db.commit()
    return _public(conn)


@router.delete("/{connection_id}")
async def delete_connection(
    connection_id: str,
    current_user: User = Depends(require_permission("admin")),
    db: Session = Depends(get_db),
):
    conn = db.query(IntegrationConnection).filter(IntegrationConnection.id == connection_id).first()
    if not conn:
        raise HTTPException(404, "Connection not found")
    record_audit(
        db,
        action="admin.integration_delete",
        entity_type="integration_connection",
        entity_id=conn.id,
        actor_id=current_user.id,
        actor_username=current_user.username,
        details={"name": conn.name},
    )
    db.delete(conn)
    db.commit()
    return {"success": True}
