"""CompanyMembership: the grant list behind real multi-company users.

See app/models/company_membership.py for the shape and rationale. Short
version: User.business_unit_id/role_id are "the active company + role,"
unchanged; switching is a plain DB write of those two columns because every
permission check reads them live off the User row (get_current_user re-fetches
from the DB on every request -- see app/api/auth.py). This module is the only
place that write happens, so the "active company always has a membership"
invariant lives in one place.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.business_unit import BusinessUnit
from app.models.company import Company
from app.models.company_membership import CompanyMembership
from app.models.user import Role, User
from app.services.audit import record_audit


def list_memberships(db: Session, user: User) -> list[dict]:
    """This user's companies, joined with company/role names, for /auth/me."""
    rows = (
        db.query(CompanyMembership, Company, Role)
        .join(Company, Company.id == CompanyMembership.company_id)
        .join(Role, Role.id == CompanyMembership.role_id)
        .filter(CompanyMembership.user_id == user.id)
        .order_by(Company.name)
        .all()
    )
    out = []
    for membership, company, role in rows:
        bu = db.query(BusinessUnit).filter(BusinessUnit.company_id == company.id).first()
        out.append({
            "company_id": company.id,
            "business_unit_id": bu.id if bu else None,
            "name": company.name,
            "role_name": role.name,
        })
    return out


def ensure_active_membership(db: Session, user: User) -> None:
    """Upsert a membership matching the user's CURRENT business_unit_id/role_id.

    Called from every place those two columns get set (register,
    admin-create, admin update, OIDC auto-provision) so a user's active
    company is never missing from their own grant list. No-op if the user
    has no business_unit_id yet.
    """
    if not user.business_unit_id or not user.role_id:
        return
    bu = db.query(BusinessUnit).filter(BusinessUnit.id == user.business_unit_id).first()
    if bu is None or bu.company_id is None:
        return
    existing = (
        db.query(CompanyMembership)
        .filter(
            CompanyMembership.user_id == user.id,
            CompanyMembership.company_id == bu.company_id,
        )
        .first()
    )
    if existing:
        if existing.role_id != user.role_id:
            existing.role_id = user.role_id
        return
    db.add(
        CompanyMembership(user_id=user.id, company_id=bu.company_id, role_id=user.role_id)
    )
    db.flush()


def switch_active_company(db: Session, user: User, company_id: str) -> User:
    """Switch `user`'s active company/role to one they hold a membership for.

    Raises ValueError (callers map to 404 -- don't reveal whether a company
    exists differently from "you can't access it") if no membership row
    exists for (user, company_id).
    """
    membership = (
        db.query(CompanyMembership)
        .filter(
            CompanyMembership.user_id == user.id,
            CompanyMembership.company_id == company_id,
        )
        .first()
    )
    if membership is None:
        raise ValueError("Company not found")
    bu = db.query(BusinessUnit).filter(BusinessUnit.company_id == company_id).first()
    if bu is None:
        raise ValueError("Company not found")

    user.business_unit_id = bu.id
    user.business_unit = bu.name  # keep the legacy free-text field in sync too
    user.role_id = membership.role_id
    db.commit()
    db.refresh(user)
    return user


def grant_membership(
    db: Session, *, user_id: str, company_id: str, role_id: int, actor: User
) -> CompanyMembership:
    """Admin action: grant `user_id` a role in `company_id`. Audited."""
    target = db.query(User).filter(User.id == user_id).first()
    if target is None:
        raise ValueError(f"User '{user_id}' not found")
    company = db.query(Company).filter(Company.id == company_id).first()
    if company is None:
        raise ValueError(f"Company '{company_id}' not found")
    role = db.query(Role).filter(Role.id == role_id).first()
    if role is None:
        raise ValueError(f"Role id {role_id} not found")

    existing = (
        db.query(CompanyMembership)
        .filter(CompanyMembership.user_id == user_id, CompanyMembership.company_id == company_id)
        .first()
    )
    if existing:
        existing.role_id = role_id
        membership = existing
    else:
        membership = CompanyMembership(user_id=user_id, company_id=company_id, role_id=role_id)
        db.add(membership)
    db.flush()

    record_audit(
        db,
        action="admin.grant_company_membership",
        entity_type="company_membership",
        entity_id=membership.id,
        actor_id=actor.id,
        actor_username=actor.username,
        details={"user_id": user_id, "company_id": company_id, "role": role.name},
    )
    db.commit()
    db.refresh(membership)
    return membership
