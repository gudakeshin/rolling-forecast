"""Phase 0 — deployment blockers & correctness regressions."""

from __future__ import annotations

import os
import tempfile
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from app.domain.base_skill import SkillContext
from app.domain.skills.detect_anomalies import DetectAnomaliesSkill
from app.models.approval import ApprovalWorkflow, ApprovalStep
from app.models.forecast import ForecastVersion
from app.models.user import Role, User
from app.services.ingestion.csv_adapter import CSVActualsProvider
from passlib.context import CryptContext


@pytest.fixture
def client():
    """HTTP client; lifespan seeds demo users when SEED_DEMO_USERS=true."""
    import os
    os.environ["SEED_DEMO_USERS"] = "true"
    os.environ["APP_ENV"] = "test"
    from app.main import app
    from app.rate_limit import limiter

    try:
        limiter.reset()
    except Exception:
        storage = getattr(limiter, "_storage", None)
        if storage is not None and hasattr(storage, "reset"):
            storage.reset()

    with TestClient(app) as c:
        yield c


def _auth_header(client: TestClient, username: str, password: str) -> dict[str, str]:
    r = client.post(
        "/api/auth/login",
        json={"username": username, "password": password},
    )
    assert r.status_code == 200, r.text
    token = r.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_detect_anomalies_uses_fail_not_error(db_session, seed_users):
    """SkillResult.error does not exist — missing version must return .fail cleanly."""
    cm = MagicMock()
    cm.get_active_version_id.return_value = None
    ctx = SkillContext(
        db=db_session,
        context_manager=cm,
        user_id=seed_users["analyst"].id,
        user_role="analyst",
        conversation_id="test-conv",
    )
    skill = DetectAnomaliesSkill()
    result = await skill.execute({}, ctx)
    assert result.success is False
    assert "forecast version" in (result.error or result.message).lower()


@pytest.mark.asyncio
async def test_csv_missing_required_columns_hard_reject():
    """Missing required columns must fail ingestion, not warn-and-continue."""
    provider = CSVActualsProvider()
    with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
        f.write("foo,bar\n1,2\n")
        path = f.name
    try:
        result = await provider.pull_actuals({"file_path": path})
    finally:
        os.unlink(path)

    assert result.success is False
    assert result.error is not None
    assert "Missing required columns" in result.error


def test_sod_creator_cannot_self_approve(client):
    """Generator who created the forecast must get 403 when approving (SoD)."""
    from app.database import SessionLocal
    from app.models.approval import ApprovalWorkflow, ApprovalStep
    from app.models.forecast import ForecastVersion
    from app.models.user import Role, User

    pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")
    db = SessionLocal()
    try:
        admin_role = db.query(Role).filter(Role.name == "admin").first()
        assert admin_role is not None

        creator = db.query(User).filter(User.username == "creator").first()
        if not creator:
            creator = User(
                email="creator@test.local",
                username="creator",
        hashed_password=pwd.hash("creator"),
                full_name="Forecast Creator",
                role_id=admin_role.id,
            )
            db.add(creator)
            db.flush()

        version = ForecastVersion(
            name="FC-SoD-Test",
            status="in_review",
            version_type="scheduled",
            horizon_months=12,
            created_by=creator.id,
            created_at=datetime.now(timezone.utc),
        )
        db.add(version)
        db.flush()

        wf = ApprovalWorkflow(
            name="SoD Test Workflow",
            description="test",
            is_active=True,
            require_sod=True,
            levels=[{"level": 1, "role": "admin", "label": "Admin"}],
            created_at=datetime.now(timezone.utc),
        )
        db.add(wf)
        db.flush()

        step = ApprovalStep(
            version_id=version.id,
            workflow_id=wf.id,
            level=1,
            required_role="admin",
            status="pending",
            created_at=datetime.now(timezone.utc),
        )
        db.add(step)
        db.commit()
        step_id = step.id
    finally:
        db.close()

    headers = _auth_header(client, "creator", "creator")
    r = client.post(
        "/api/approvals/decide",
        json={"step_id": step_id, "action": "approve", "comments": "self"},
        headers=headers,
    )
    assert r.status_code == 403
    assert "segregation of duties" in r.json()["detail"].lower()


@pytest.mark.asyncio
async def test_generate_baseline_sets_created_by(db_session, seed_users, seed_actuals, skill_context):
    """AI-generated forecasts must stamp created_by for SoD enforcement."""
    from app.domain.skills.generate_baseline import GenerateBaselineSkill

    skill_context.context_manager.set_memory("last_dataset_id", seed_actuals.id)
    skill = GenerateBaselineSkill()
    result = await skill.execute(
        {
            "horizon_months": 3,
            "model_type": "linear",
            "random_seed": 42,
            "async_job": False,
            "skip_plan_check": True,
        },
        skill_context,
    )
    assert result.success is True, result.error or result.message
    version_id = result.data.get("version_id")
    assert version_id

    loaded = db_session.query(ForecastVersion).filter(ForecastVersion.id == version_id).one()
    assert loaded.created_by == seed_users["analyst"].id
    assert loaded.created_by == skill_context.user_id
