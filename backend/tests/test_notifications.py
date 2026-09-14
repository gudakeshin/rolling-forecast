"""Phase 3.3 — notification creation, dedup, and trigger wiring."""

from __future__ import annotations

import asyncio

import pandas as pd
import pytest

from app.models.approval import ApprovalStep, ApprovalWorkflow
from app.models.forecast import ForecastLineResult, ForecastVersion
from app.models.notification import Notification
from app.models.user import Role, User
from app.services.notifications import (
    create_notification,
    users_eligible_for_approval_step,
)


def test_create_notification_dedup(db_session, seed_users):
    user = seed_users["analyst"]
    first = create_notification(
        db_session,
        user_id=user.id,
        kind="job_finished",
        title="First",
        dedup_key="job_finished:job-1",
    )
    db_session.commit()
    assert first is not None

    second = create_notification(
        db_session,
        user_id=user.id,
        kind="job_finished",
        title="Duplicate attempt",
        dedup_key="job_finished:job-1",
    )
    assert second is None
    assert (
        db_session.query(Notification)
        .filter(Notification.dedup_key == "job_finished:job-1")
        .count()
        == 1
    )


def test_create_notification_without_dedup_key_never_collapses(db_session, seed_users):
    user = seed_users["analyst"]
    create_notification(db_session, user_id=user.id, kind="anomaly_detected", title="A")
    create_notification(db_session, user_id=user.id, kind="anomaly_detected", title="B")
    db_session.commit()
    assert (
        db_session.query(Notification).filter(Notification.user_id == user.id).count() == 2
    )


@pytest.fixture
def seed_reviewer(db_session, seed_roles):
    role = db_session.query(Role).filter(Role.name == "reviewer").first()
    if not role:
        role = Role(
            name="reviewer", description="Reviewer", can_input=True, can_generate=True,
            can_override=True, can_review=True, can_publish=False, can_admin=False,
            can_view_all_bus=True, can_manage_drivers=True,
        )
        db_session.add(role)
        db_session.flush()
    user = db_session.query(User).filter(User.username == "reviewer1").first()
    if not user:
        from passlib.context import CryptContext

        pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")
        user = User(
            email="reviewer1@test.local", username="reviewer1",
            hashed_password=pwd_ctx.hash("reviewer1"), full_name="Test Reviewer",
            role_id=role.id,
        )
        db_session.add(user)
    db_session.commit()
    return user


def test_users_eligible_for_approval_step_matches_role_and_admin(
    db_session, seed_users, seed_reviewer
):
    eligible = users_eligible_for_approval_step(db_session, "reviewer")
    ids = {u.id for u in eligible}
    assert seed_reviewer.id in ids
    assert seed_users["admin"].id in ids  # admins can act on any step
    assert seed_users["analyst"].id not in ids  # analyst role can't review


def test_submit_for_approval_notifies_first_level_reviewers(
    db_session, seed_users, seed_reviewer
):
    from app.api.approvals import SubmitApprovalRequest, submit_for_approval

    wf = ApprovalWorkflow(
        name="Standard", is_active=True,
        levels=[{"level": 1, "role": "reviewer"}, {"level": 2, "role": "admin"}],
    )
    db_session.add(wf)
    version = ForecastVersion(
        id="approval-notif-v1", name="Approval Notif Test", status="draft",
        version_type="baseline", horizon_months=1, scenario="base",
        created_by=seed_users["analyst"].id,
    )
    db_session.add(version)
    db_session.commit()

    result = asyncio.run(
        submit_for_approval(
            body=SubmitApprovalRequest(version_id=version.id, workflow_id=wf.id),
            current_user=seed_users["analyst"],
            db=db_session,
        )
    )
    assert result["status"] == "in_review"

    notifs = (
        db_session.query(Notification)
        .filter(Notification.kind == "approval_pending", Notification.entity_id == version.id)
        .all()
    )
    recipient_ids = {n.user_id for n in notifs}
    assert seed_reviewer.id in recipient_ids
    assert seed_users["admin"].id in recipient_ids
    # Level 2 (admin) isn't actionable yet — only level 1 reviewers were notified now.
    assert all(n.body and "Level 1" in n.body for n in notifs)

    # Re-submitting the same version+level must not spam a second notification.
    asyncio.run(
        submit_for_approval(
            body=SubmitApprovalRequest(version_id=version.id, workflow_id=wf.id),
            current_user=seed_users["analyst"],
            db=db_session,
        )
    )
    notifs_after = (
        db_session.query(Notification)
        .filter(Notification.kind == "approval_pending", Notification.entity_id == version.id)
        .count()
    )
    assert notifs_after == len(notifs)


def test_approving_level_one_notifies_level_two(db_session, seed_users, seed_reviewer):
    from app.api.approvals import DecideRequest, SubmitApprovalRequest, decide_step, submit_for_approval

    wf = ApprovalWorkflow(
        name="Two Level", is_active=True,
        levels=[{"level": 1, "role": "reviewer"}, {"level": 2, "role": "admin"}],
    )
    db_session.add(wf)
    version = ForecastVersion(
        id="approval-notif-v2", name="Two Level Test", status="draft",
        version_type="baseline", horizon_months=1, scenario="base",
        created_by=seed_users["analyst"].id,
    )
    db_session.add(version)
    db_session.commit()

    asyncio.run(
        submit_for_approval(
            body=SubmitApprovalRequest(version_id=version.id, workflow_id=wf.id),
            current_user=seed_users["analyst"],
            db=db_session,
        )
    )
    level1_step = (
        db_session.query(ApprovalStep)
        .filter(ApprovalStep.version_id == version.id, ApprovalStep.level == 1)
        .first()
    )
    asyncio.run(
        decide_step(
            body=DecideRequest(step_id=level1_step.id, action="approve"),
            current_user=seed_reviewer,
            db=db_session,
        )
    )
    level2_notifs = (
        db_session.query(Notification)
        .filter(Notification.kind == "approval_pending", Notification.entity_id == version.id)
        .filter(Notification.body.like("%Level 2%"))
        .all()
    )
    assert any(n.user_id == seed_users["admin"].id for n in level2_notifs)


def test_actuals_ingest_notifies_can_generate_users_with_top_mover(
    db_session, seed_users, seed_line_items
):
    from app.services.actuals_ingest import persist_pulled_actuals
    from app.services.ingestion.base import IngestionResult

    li = seed_line_items["REV-001"]
    version = ForecastVersion(
        id="actuals-notif-v1", name="Actuals Notif Test", status="published",
        version_type="baseline", horizon_months=1, scenario="base",
        base_period="2025-12",
    )
    db_session.add(version)
    db_session.add(ForecastLineResult(
        id="flr-actuals-notif-1", version_id=version.id, line_item_id=li.id,
        period="2026-01", p10=80_000, p50=100_000, p90=120_000, model_type="linear",
    ))
    db_session.commit()

    df = pd.DataFrame([
        {"account_code": li.account_code, "account_name": li.name, "category": li.category,
         "period": "2026-01", "value": 150_000.0, "currency": "USD"},
    ])
    result = IngestionResult(success=True, dataframe=df, row_count=1, file_hash="h1")

    outcome = asyncio.run(
        persist_pulled_actuals(
            db_session, result, "warehouse", "test-source",
            actor_id=seed_users["admin"].id, actor_username="admin",
        )
    )
    assert outcome["success"] is True

    notifs = (
        db_session.query(Notification)
        .filter(Notification.kind == "actuals_ingested")
        .all()
    )
    assert notifs, "expected at least one actuals_ingested notification"
    recipient_ids = {n.user_id for n in notifs}
    # can_generate=True for both admin and analyst in the seeded roles.
    assert seed_users["admin"].id in recipient_ids
    assert seed_users["analyst"].id in recipient_ids
    # A ForecastLineResult existed for the same line/period, so the accuracy
    # snapshot produced a real pct_error and the notification names the mover.
    assert any("Product Revenue" in (n.body or "") and "%" in (n.body or "") for n in notifs)
