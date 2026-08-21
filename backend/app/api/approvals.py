"""Multi-level approval workflow with segregation of duties."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.api.auth import get_current_user
from app.models.user import User
from app.models.forecast import ForecastVersion
from app.models.approval import ApprovalWorkflow, ApprovalStep
from app.services.permissions import require_permission
from app.services.audit import record_audit

router = APIRouter(prefix="/approvals", tags=["approvals"])


class SubmitApprovalRequest(BaseModel):
    version_id: str
    workflow_id: str | None = None


class DecideRequest(BaseModel):
    step_id: str
    action: str  # approve | reject
    comments: str | None = None


def _can_decide(
    db: Session,
    step: ApprovalStep,
    version: ForecastVersion | None,
    wf: ApprovalWorkflow | None,
    user: User,
) -> tuple[bool, str | None]:
    """Role-gate + SoD + level-ordering eligibility for a pending step."""
    if step.status != "pending":
        return False, f"Step already {step.status}"

    if wf and wf.require_sod and version and version.created_by == user.id:
        return False, "Segregation of duties: the forecast creator cannot approve their own submission"

    role_name = user.role.name if user.role else ""
    if role_name not in (step.required_role, "admin") and not (
        step.required_role == "reviewer" and user.role and user.role.can_review
    ):
        if not (user.role and user.role.can_admin):
            return False, f"Step requires role '{step.required_role}', you are '{role_name}'"

    prior_pending = (
        db.query(ApprovalStep)
        .filter(
            ApprovalStep.version_id == step.version_id,
            ApprovalStep.level < step.level,
            ApprovalStep.status != "approved",
        )
        .count()
    )
    if prior_pending:
        return False, "Previous approval levels must be completed first"

    return True, None


def _actor_username(db: Session, actor_id: str | None) -> str | None:
    if not actor_id:
        return None
    actor = db.query(User).filter(User.id == actor_id).first()
    return actor.username if actor else None


@router.get("/workflows")
async def list_workflows(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    wfs = db.query(ApprovalWorkflow).filter(ApprovalWorkflow.is_active == True).all()  # noqa: E712
    return [
        {
            "id": w.id,
            "name": w.name,
            "description": w.description,
            "levels": w.levels,
            "require_sod": w.require_sod,
        }
        for w in wfs
    ]


@router.post("/submit")
async def submit_for_approval(
    body: SubmitApprovalRequest,
    current_user: User = Depends(require_permission("generate")),
    db: Session = Depends(get_db),
):
    version = db.query(ForecastVersion).filter(ForecastVersion.id == body.version_id).first()
    if not version:
        raise HTTPException(404, "Version not found")
    if version.status not in ("draft", "in_review"):
        raise HTTPException(400, f"Cannot submit a '{version.status}' version")

    wf = None
    if body.workflow_id:
        wf = db.query(ApprovalWorkflow).filter(ApprovalWorkflow.id == body.workflow_id).first()
    if not wf:
        wf = (
            db.query(ApprovalWorkflow)
            .filter(ApprovalWorkflow.is_active == True)  # noqa: E712
            .order_by(ApprovalWorkflow.created_at.asc())
            .first()
        )
    if not wf:
        raise HTTPException(400, "No active approval workflow configured")

    # Clear prior pending steps
    db.query(ApprovalStep).filter(
        ApprovalStep.version_id == version.id,
        ApprovalStep.status == "pending",
    ).delete()

    for level in sorted(wf.levels, key=lambda x: x.get("level", 0)):
        db.add(ApprovalStep(
            version_id=version.id,
            workflow_id=wf.id,
            level=int(level["level"]),
            required_role=level["role"],
            status="pending",
        ))

    version.status = "in_review"
    record_audit(
        db,
        action="approval.submit",
        entity_type="forecast_version",
        entity_id=version.id,
        actor_id=current_user.id,
        actor_username=current_user.username,
        details={"workflow_id": wf.id},
    )
    db.commit()
    return {"success": True, "version_id": version.id, "workflow_id": wf.id, "status": "in_review"}


@router.get("/status/{version_id}")
async def approval_status(
    version_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    version = db.query(ForecastVersion).filter(ForecastVersion.id == version_id).first()
    if not version:
        raise HTTPException(404, "Version not found")

    steps = (
        db.query(ApprovalStep)
        .filter(ApprovalStep.version_id == version_id)
        .order_by(ApprovalStep.level)
        .all()
    )

    wf_ids = {s.workflow_id for s in steps if s.workflow_id}
    workflows = {
        w.id: w
        for w in db.query(ApprovalWorkflow).filter(ApprovalWorkflow.id.in_(wf_ids)).all()
    } if wf_ids else {}

    step_payloads = []
    for s in steps:
        wf = workflows.get(s.workflow_id)
        can_decide, blocked_reason = _can_decide(db, s, version, wf, current_user)
        step_payloads.append({
            "id": s.id,
            "level": s.level,
            "required_role": s.required_role,
            "status": s.status,
            "actor_id": s.actor_id,
            "actor_username": _actor_username(db, s.actor_id),
            "comments": s.comments,
            "decided_at": s.decided_at.isoformat() if s.decided_at else None,
            "can_decide": can_decide,
            "blocked_reason": blocked_reason,
        })

    is_creator = version.created_by == current_user.id
    can_submit = (
        version.status == "draft"
        and bool(current_user.role and current_user.role.can_generate)
    )

    return {
        "version_id": version_id,
        "version": {
            "id": version.id,
            "name": version.name,
            "status": version.status,
            "created_by": version.created_by,
        },
        "steps": step_payloads,
        "can_submit": can_submit,
        "is_creator": is_creator,
    }


@router.post("/decide")
async def decide_step(
    body: DecideRequest,
    current_user: User = Depends(require_permission("review")),
    db: Session = Depends(get_db),
):
    step = db.query(ApprovalStep).filter(ApprovalStep.id == body.step_id).first()
    if not step:
        raise HTTPException(404, "Approval step not found")
    if step.status != "pending":
        raise HTTPException(400, f"Step already {step.status}")

    version = db.query(ForecastVersion).filter(ForecastVersion.id == step.version_id).first()
    wf = db.query(ApprovalWorkflow).filter(ApprovalWorkflow.id == step.workflow_id).first()

    can_decide, reason = _can_decide(db, step, version, wf, current_user)
    if not can_decide:
        status = 403 if reason and (
            "segregation" in reason.lower() or "requires role" in reason.lower()
        ) else 400
        raise HTTPException(status, reason or "Cannot decide this step")

    if body.action not in ("approve", "reject"):
        raise HTTPException(400, "action must be approve or reject")

    step.status = "approved" if body.action == "approve" else "rejected"
    step.actor_id = current_user.id
    step.comments = body.comments
    step.decided_at = datetime.now(timezone.utc)

    if body.action == "reject":
        if not body.comments:
            raise HTTPException(400, "Comments required when rejecting")
        if version:
            version.status = "draft"
        # Cancel remaining pending
        db.query(ApprovalStep).filter(
            ApprovalStep.version_id == step.version_id,
            ApprovalStep.status == "pending",
        ).update({"status": "skipped"})
    else:
        remaining = (
            db.query(ApprovalStep)
            .filter(
                ApprovalStep.version_id == step.version_id,
                ApprovalStep.status == "pending",
            )
            .count()
        )
        if remaining == 0 and version:
            version.status = "approved"
            version.approved_at = datetime.now(timezone.utc)
            version.approved_by = current_user.id

    record_audit(
        db,
        action=f"approval.{body.action}",
        entity_type="approval_step",
        entity_id=step.id,
        actor_id=current_user.id,
        actor_username=current_user.username,
        details={"version_id": step.version_id, "level": step.level},
    )
    db.commit()
    return {
        "success": True,
        "step_status": step.status,
        "version_status": version.status if version else None,
    }
