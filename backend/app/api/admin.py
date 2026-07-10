"""Admin API — users, roles, CoA, audit log."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, EmailStr
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User, Role
from app.models.line_item import LineItem, LineItemDependency
from app.models.audit import AuditEvent
from app.schemas.auth import UserResponse
from app.services.permissions import require_permission
from app.services.coa_dependencies import ensure_standard_dependencies
from app.services.audit import record_audit
from passlib.context import CryptContext

router = APIRouter(prefix="/admin", tags=["admin"])
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


class RoleUpdate(BaseModel):
    can_input: bool | None = None
    can_generate: bool | None = None
    can_override: bool | None = None
    can_review: bool | None = None
    can_publish: bool | None = None
    can_admin: bool | None = None


class UserUpdate(BaseModel):
    full_name: str | None = None
    business_unit: str | None = None
    role_name: str | None = None
    is_active: bool | None = None
    password: str | None = None


class LineItemCreate(BaseModel):
    account_code: str
    name: str
    category: str
    is_calculated: bool = False
    formula: str | None = None
    business_unit: str | None = None
    display_order: int = 0


class DependencyCreate(BaseModel):
    dependent_item_id: int
    source_item_id: int
    relationship_type: str = "sum"
    weight: float = 1.0


@router.get("/users", response_model=list[UserResponse])
async def list_users(
    current_user: User = Depends(require_permission("admin")),
    db: Session = Depends(get_db),
):
    users = db.query(User).order_by(User.username).all()
    return [
        UserResponse(
            id=u.id,
            email=u.email,
            username=u.username,
            full_name=u.full_name,
            business_unit=u.business_unit,
            role_name=u.role.name if u.role else "",
            is_active=u.is_active,
        )
        for u in users
    ]


@router.patch("/users/{user_id}", response_model=UserResponse)
async def update_user(
    user_id: str,
    body: UserUpdate,
    current_user: User = Depends(require_permission("admin")),
    db: Session = Depends(get_db),
):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(404, "User not found")
    if body.full_name is not None:
        user.full_name = body.full_name
    if body.business_unit is not None:
        user.business_unit = body.business_unit
    if body.is_active is not None:
        user.is_active = body.is_active
    if body.password:
        user.hashed_password = pwd_context.hash(body.password)
    if body.role_name:
        role = db.query(Role).filter(Role.name == body.role_name).first()
        if not role:
            raise HTTPException(400, f"Role '{body.role_name}' not found")
        user.role_id = role.id
    record_audit(
        db,
        action="admin.update_user",
        entity_type="user",
        entity_id=user.id,
        actor_id=current_user.id,
        actor_username=current_user.username,
    )
    db.commit()
    db.refresh(user)
    return UserResponse(
        id=user.id,
        email=user.email,
        username=user.username,
        full_name=user.full_name,
        business_unit=user.business_unit,
        role_name=user.role.name,
        is_active=user.is_active,
    )


@router.get("/roles")
async def list_roles(
    current_user: User = Depends(require_permission("admin")),
    db: Session = Depends(get_db),
):
    roles = db.query(Role).all()
    return [
        {
            "id": r.id,
            "name": r.name,
            "description": r.description,
            "can_input": r.can_input,
            "can_generate": r.can_generate,
            "can_override": r.can_override,
            "can_review": r.can_review,
            "can_publish": r.can_publish,
            "can_admin": r.can_admin,
        }
        for r in roles
    ]


@router.patch("/roles/{role_name}")
async def update_role(
    role_name: str,
    body: RoleUpdate,
    current_user: User = Depends(require_permission("admin")),
    db: Session = Depends(get_db),
):
    role = db.query(Role).filter(Role.name == role_name).first()
    if not role:
        raise HTTPException(404, "Role not found")
    for field, value in body.model_dump(exclude_none=True).items():
        setattr(role, field, value)
    db.commit()
    return {"success": True, "name": role.name}


@router.get("/coa")
async def list_coa(
    current_user: User = Depends(require_permission("admin")),
    db: Session = Depends(get_db),
):
    items = db.query(LineItem).order_by(LineItem.display_order, LineItem.account_code).all()
    deps = db.query(LineItemDependency).all()
    return {
        "line_items": [
            {
                "id": li.id,
                "account_code": li.account_code,
                "name": li.name,
                "category": li.category,
                "is_calculated": li.is_calculated,
                "formula": li.formula,
                "business_unit": li.business_unit,
            }
            for li in items
        ],
        "dependencies": [
            {
                "id": d.id,
                "dependent_item_id": d.dependent_item_id,
                "source_item_id": d.source_item_id,
                "relationship_type": d.relationship_type,
                "weight": d.weight,
            }
            for d in deps
        ],
    }


@router.post("/coa")
async def create_line_item(
    body: LineItemCreate,
    current_user: User = Depends(require_permission("admin")),
    db: Session = Depends(get_db),
):
    if db.query(LineItem).filter(LineItem.account_code == body.account_code).first():
        raise HTTPException(409, "Account code already exists")
    li = LineItem(**body.model_dump())
    db.add(li)
    db.commit()
    db.refresh(li)
    return {"id": li.id, "account_code": li.account_code}


@router.post("/coa/dependencies")
async def create_dependency(
    body: DependencyCreate,
    current_user: User = Depends(require_permission("admin")),
    db: Session = Depends(get_db),
):
    from app.services.dependency_graph import DependencyGraphManager

    dag = DependencyGraphManager(db)
    ok, msg = dag.add_dependency(
        body.dependent_item_id, body.source_item_id, body.relationship_type, body.weight
    )
    if not ok:
        raise HTTPException(400, msg)
    db.commit()
    return {"success": True, "message": msg}


@router.post("/coa/wire-standard")
async def wire_standard_coa(
    current_user: User = Depends(require_permission("admin")),
    db: Session = Depends(get_db),
):
    created = ensure_standard_dependencies(db)
    db.commit()
    return {"success": True, "dependencies_created": created}


@router.get("/audit")
async def list_audit(
    current_user: User = Depends(require_permission("admin")),
    db: Session = Depends(get_db),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    action: str | None = None,
):
    q = db.query(AuditEvent).order_by(AuditEvent.timestamp.desc())
    if action:
        q = q.filter(AuditEvent.action == action)
    total = q.count()
    rows = q.offset(offset).limit(limit).all()
    return {
        "total": total,
        "offset": offset,
        "limit": limit,
        "events": [
            {
                "id": e.id,
                "timestamp": e.timestamp.isoformat() if e.timestamp else None,
                "actor_username": e.actor_username,
                "action": e.action,
                "entity_type": e.entity_type,
                "entity_id": e.entity_id,
                "details": e.details,
            }
            for e in rows
        ],
    }
