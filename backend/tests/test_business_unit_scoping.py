"""Regression: two companies' data must never mix.

Before this, `business_unit` was a plain nullable string with no real
enforcement: line_items.account_code was globally unique (so two companies
both uploading "REV-001" collided onto the same row), ActualsDataset/
ForecastVersion had no company scope at all, and manage_actuals_dataset's
`unpin` had no ownership check. This file exercises the fix end-to-end:
BusinessUnit as a real FK-scoped entity, per-company dataset resolution, and
cross-company access being refused rather than silently allowed.
"""

from __future__ import annotations

from typing import Any

import pandas as pd
import pytest
from passlib.context import CryptContext

from app.models.business_unit import BusinessUnit
from app.models.line_item import LineItem
from app.models.user import User
from app.services.actuals_resolution import resolve_current_dataset

_pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")


def _make_user(db_session, seed_roles, business_unit: BusinessUnit, username: str) -> User:
    u = User(
        email=f"{username}@test.local",
        username=username,
        hashed_password=_pwd.hash(username),
        full_name=username,
        business_unit_id=business_unit.id,
        role_id=seed_roles["analyst"].id,
    )
    db_session.add(u)
    db_session.commit()
    return u


def _skill_context(db_session, user: User) -> Any:
    from app.domain.base_skill import SkillContext
    from tests.conftest import MockContextManager

    cm = MockContextManager(permissions={"input", "generate", "override"})
    cm.user = user
    return SkillContext(
        db=db_session,
        context_manager=cm,
        user_id=user.id,
        user_role="analyst",
        conversation_id=f"test-{user.username}",
        user=user,
    )


def _write_csv(path, base: float) -> None:
    rows = []
    for month in range(24):
        year = 2024 + month // 12
        m = (month % 12) + 1
        rows.append({
            "account_code": "REV-001",
            "account_name": "Product Revenue",
            "category": "Revenue",
            "period": f"{year}-{m:02d}",
            "value": base + month * 100,
            "currency": "USD",
        })
    pd.DataFrame(rows).to_csv(path, index=False)


@pytest.fixture
def two_companies(db_session, seed_roles):
    company_a = BusinessUnit(name="Company A")
    company_b = BusinessUnit(name="Company B")
    db_session.add_all([company_a, company_b])
    db_session.commit()
    user_a = _make_user(db_session, seed_roles, company_a, "user_a")
    user_b = _make_user(db_session, seed_roles, company_b, "user_b")
    return {
        "company_a": company_a,
        "company_b": company_b,
        "user_a": user_a,
        "user_b": user_b,
        "ctx_a": _skill_context(db_session, user_a),
        "ctx_b": _skill_context(db_session, user_b),
    }


@pytest.mark.asyncio
async def test_same_account_code_does_not_collide_across_companies(
    db_session, two_companies, tmp_path
):
    from app.domain.skills.ingest_actuals import IngestActualsSkill

    csv_a = tmp_path / "a.csv"
    csv_b = tmp_path / "b.csv"
    _write_csv(csv_a, base=100000.0)
    _write_csv(csv_b, base=999000.0)  # different content -> different file_hash too

    result_a = await IngestActualsSkill().execute({"file_path": str(csv_a)}, two_companies["ctx_a"])
    result_b = await IngestActualsSkill().execute({"file_path": str(csv_b)}, two_companies["ctx_b"])
    assert result_a.success, result_a.message
    assert result_b.success, result_b.message

    rows = db_session.query(LineItem).filter(LineItem.account_code == "REV-001").all()
    assert len(rows) == 2, "two companies' REV-001 must be two distinct LineItem rows"
    bu_ids = {li.business_unit_id for li in rows}
    assert bu_ids == {two_companies["company_a"].id, two_companies["company_b"].id}


@pytest.mark.asyncio
async def test_generate_baseline_never_crosses_companies(db_session, two_companies, tmp_path):
    from app.domain.skills.generate_baseline import GenerateBaselineSkill
    from app.domain.skills.ingest_actuals import IngestActualsSkill

    csv_a = tmp_path / "a.csv"
    csv_b = tmp_path / "b.csv"
    _write_csv(csv_a, base=100000.0)
    _write_csv(csv_b, base=999000.0)

    result_a = await IngestActualsSkill().execute({"file_path": str(csv_a)}, two_companies["ctx_a"])
    assert result_a.success, result_a.message
    # B ingests *after* A and is pinned too (every manual upload is) -- if
    # resolution weren't company-scoped, B (being more recent) would win
    # globally and A's forecast would silently use B's data.
    result_b = await IngestActualsSkill().execute({"file_path": str(csv_b)}, two_companies["ctx_b"])
    assert result_b.success, result_b.message

    current_for_a = resolve_current_dataset(db_session, business_unit_id=two_companies["company_a"].id)
    assert current_for_a.id == result_a.data["dataset_id"]

    gen = GenerateBaselineSkill()
    outcome_a = await gen.execute(
        {"horizon_months": 3, "model_type": "linear", "random_seed": 42, "skip_plan_check": True},
        two_companies["ctx_a"],
    )
    assert outcome_a.success, outcome_a.message

    from app.models.forecast import ForecastVersion

    version_a = db_session.query(ForecastVersion).filter(
        ForecastVersion.id == outcome_a.data["version_id"]
    ).first()
    assert version_a.actuals_dataset_id == result_a.data["dataset_id"]
    assert version_a.business_unit_id == two_companies["company_a"].id


@pytest.mark.asyncio
async def test_cross_company_dataset_access_refused(db_session, two_companies, tmp_path):
    from app.domain.skills.ingest_actuals import IngestActualsSkill
    from app.domain.skills.manage_actuals_dataset import ManageActualsDatasetSkill

    csv_b = tmp_path / "b.csv"
    _write_csv(csv_b, base=999000.0)
    result_b = await IngestActualsSkill().execute({"file_path": str(csv_b)}, two_companies["ctx_b"])
    assert result_b.success
    dataset_b_id = result_b.data["dataset_id"]

    # A tries to unpin/delete B's dataset -- must be refused, not silently allowed.
    manage = ManageActualsDatasetSkill()
    unpin_attempt = await manage.execute(
        {"action": "unpin", "dataset_id": dataset_b_id}, two_companies["ctx_a"]
    )
    assert not unpin_attempt.success

    delete_attempt = await manage.execute(
        {"action": "delete", "dataset_id": dataset_b_id}, two_companies["ctx_a"]
    )
    assert not delete_attempt.success

    from app.models.actuals import ActualsDataset

    still_there = db_session.get(ActualsDataset, dataset_b_id)
    assert still_there is not None and still_there.is_pinned is True


@pytest.mark.asyncio
async def test_manage_versions_list_never_crosses_companies(db_session, two_companies, tmp_path):
    """The chat `manage_versions` "list" action must respect the same company
    boundary as line_item_scope_filter/driver_scope_filter -- previously it
    queried ForecastVersion with no business_unit_id filter at all."""
    from app.domain.skills.generate_baseline import GenerateBaselineSkill
    from app.domain.skills.ingest_actuals import IngestActualsSkill
    from app.domain.skills.manage_versions import ManageVersionsSkill

    csv_a = tmp_path / "a.csv"
    csv_b = tmp_path / "b.csv"
    _write_csv(csv_a, base=100000.0)
    _write_csv(csv_b, base=999000.0)

    result_a = await IngestActualsSkill().execute({"file_path": str(csv_a)}, two_companies["ctx_a"])
    result_b = await IngestActualsSkill().execute({"file_path": str(csv_b)}, two_companies["ctx_b"])
    assert result_a.success and result_b.success

    gen = GenerateBaselineSkill()
    outcome_a = await gen.execute(
        {"horizon_months": 3, "model_type": "linear", "random_seed": 42, "skip_plan_check": True},
        two_companies["ctx_a"],
    )
    outcome_b = await gen.execute(
        {"horizon_months": 3, "model_type": "linear", "random_seed": 42, "skip_plan_check": True},
        two_companies["ctx_b"],
    )
    assert outcome_a.success and outcome_b.success
    version_a_id = outcome_a.data["version_id"]
    version_b_id = outcome_b.data["version_id"]

    manage = ManageVersionsSkill()
    list_for_a = await manage.execute({"action": "list"}, two_companies["ctx_a"])
    ids_seen_by_a = {v["id"] for v in list_for_a.data["versions"]}
    assert version_a_id in ids_seen_by_a
    assert version_b_id not in ids_seen_by_a

    list_for_b = await manage.execute({"action": "list"}, two_companies["ctx_b"])
    ids_seen_by_b = {v["id"] for v in list_for_b.data["versions"]}
    assert version_b_id in ids_seen_by_b
    assert version_a_id not in ids_seen_by_b

    # "get"/"set_active" must also refuse another company's version id outright.
    get_attempt = await manage.execute(
        {"action": "get", "version_id": version_b_id}, two_companies["ctx_a"]
    )
    assert not get_attempt.success
    set_active_attempt = await manage.execute(
        {"action": "set_active", "version_id": version_b_id}, two_companies["ctx_a"]
    )
    assert not set_active_attempt.success


@pytest.mark.asyncio
async def test_explicit_dataset_id_cannot_bypass_company_scope(db_session, two_companies, tmp_path):
    """generate_baseline's dataset_id param must still be ownership-checked --
    otherwise scoping could be defeated just by guessing/knowing another
    company's dataset id."""
    from app.domain.skills.generate_baseline import GenerateBaselineSkill
    from app.domain.skills.ingest_actuals import IngestActualsSkill

    csv_b = tmp_path / "b.csv"
    _write_csv(csv_b, base=999000.0)
    result_b = await IngestActualsSkill().execute({"file_path": str(csv_b)}, two_companies["ctx_b"])
    assert result_b.success

    gen = GenerateBaselineSkill()
    outcome = await gen.execute(
        {
            "dataset_id": result_b.data["dataset_id"],
            "horizon_months": 3,
            "model_type": "linear",
            "random_seed": 42,
            "skip_plan_check": True,
        },
        two_companies["ctx_a"],
    )
    assert not outcome.success
