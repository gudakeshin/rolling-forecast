"""Scheduled notification sweeps (plan 3.3): driver overdue and critical anomalies."""

from __future__ import annotations

import os
import uuid

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    os.environ["SEED_DEMO_USERS"] = "true"
    os.environ["APP_ENV"] = "test"
    from app.main import app

    with TestClient(app) as c:
        yield c


@pytest.fixture
def db():
    from app.database import SessionLocal
    from app.services.tenant_scope import UNRESTRICTED, push_tenant_scope, reset_tenant_scope

    token = push_tenant_scope(UNRESTRICTED)
    session = SessionLocal()
    yield session
    session.close()
    reset_tenant_scope(token)


def _company(db, tag, side):
    from app.models.business_unit import BusinessUnit
    from app.models.company import Company
    from app.models.forecast import ForecastVersion
    from app.models.user import Role, User

    co = Company(name=f"SweepCo-{side}-{tag}")
    db.add(co)
    db.flush()
    bu = BusinessUnit(name=f"SweepBU-{side}-{tag}", company_id=co.id)
    db.add(bu)
    db.flush()
    analyst = db.query(Role).filter(Role.name == "analyst").one()  # can_input
    user = User(
        email=f"sweep_{side}_{tag}@test.local", username=f"sweep_{side}_{tag}",
        hashed_password="x", business_unit_id=bu.id, role_id=analyst.id,
    )
    version = ForecastVersion(name=f"Sweep {side} {tag}", status="draft", business_unit_id=bu.id)
    db.add_all([user, version])
    db.flush()
    return bu, user, version


@pytest.mark.asyncio
async def test_driver_overdue_notifies_only_the_forms_own_company(client, db):
    from app.models.driver_input import DriverFormConfig
    from app.models.notification import Notification
    from app.workers.arq_worker import scheduled_driver_overdue_sweep

    tag = uuid.uuid4().hex[:8]
    bu_a, user_a, version_a = _company(db, tag, "A")
    bu_b, user_b, version_b = _company(db, tag, "B")
    form_a = DriverFormConfig(
        business_unit=bu_a.name, business_unit_id=bu_a.id, name=f"Volumes {tag}",
        fields_schema={"fields": []}, soft_deadline_days=-1, hard_deadline_days=-1, is_active=True,
    )
    db.add(form_a)
    db.commit()

    await scheduled_driver_overdue_sweep({})

    db.expire_all()
    a_notes = db.query(Notification).filter(Notification.user_id == user_a.id).all()
    b_notes = db.query(Notification).filter(Notification.user_id == user_b.id).all()
    assert [n.kind for n in a_notes] == ["driver_overdue"]
    assert a_notes[0].link_params == {"version_id": version_a.id}
    assert b_notes == []  # company B never hears about company A's form


@pytest.mark.asyncio
async def test_anomaly_sweep_notifies_reviewers_and_survives_unreadable_versions(client, db, monkeypatch):
    from app.api import dashboard
    from app.models.notification import Notification
    from app.models.user import Role, User
    from app.workers.arq_worker import scheduled_anomaly_sweep

    tag = uuid.uuid4().hex[:8]
    _, _, version_a = _company(db, tag, "A")
    _, _, version_b = _company(db, tag, "B")
    reviewer_role = db.query(Role).filter(Role.name == "reviewer").one()
    reviewer = User(
        email=f"sweep_rev_{tag}@test.local", username=f"sweep_rev_{tag}",
        hashed_password="x", role_id=reviewer_role.id,
    )
    db.add(reviewer)
    db.commit()
    seen_as: set[str] = set()

    class _Result:
        def __init__(self, anomalies):
            self.data = {"anomalies": anomalies}

    async def fake_dashboard(version_id, current_user, db):
        seen_as.add(current_user.username)
        if version_id == version_a.id:
            raise RuntimeError("cannot read this version")
        if version_id == version_b.id:
            return _Result([{
                "id": f"flr-{tag}", "worst_severity": "critical", "is_dismissed": False,
                "line_item_name": "Freight", "findings": [{"headline": "Spike vs history"}],
            }])
        return _Result([])

    monkeypatch.setattr(dashboard, "get_anomaly_dashboard", fake_dashboard)
    summary = await scheduled_anomaly_sweep({})

    assert summary["notifications_created"] >= 1  # A's failure did not stop B
    assert seen_as == {"admin"}  # the platform admin, who can read every company
    db.expire_all()
    note = db.query(Notification).filter(
        Notification.user_id == reviewer.id, Notification.entity_id == f"flr-{tag}"
    ).one()
    assert note.kind == "anomaly_detected"
    assert note.link_params == {"version_id": version_b.id}
