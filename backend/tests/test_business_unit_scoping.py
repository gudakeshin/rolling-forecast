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


def _make_admin_user(db_session, seed_roles, username: str, business_unit: BusinessUnit | None = None) -> User:
    """A cross-BU (can_view_all_bus) user -- optionally still assigned to one
    company (own_bu), for testing the "cross-BU actor with a home BU" case."""
    u = User(
        email=f"{username}@test.local",
        username=username,
        hashed_password=_pwd.hash(username),
        full_name=username,
        business_unit_id=business_unit.id if business_unit else None,
        role_id=seed_roles["admin"].id,
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


def _make_version(db_session, business_unit, *, status="draft", parent_version_id=None, name="V"):
    from app.models.forecast import ForecastVersion

    v = ForecastVersion(
        name=name,
        status=status,
        version_type="baseline",
        horizon_months=3,
        business_unit_id=business_unit.id,
        parent_version_id=parent_version_id,
    )
    db_session.add(v)
    db_session.commit()
    return v


@pytest.mark.asyncio
async def test_approvals_never_cross_companies(db_session, two_companies):
    """submit/status/decide on approvals.py must all be refused cross-company --
    previously each fetched ForecastVersion/ApprovalStep by raw id with no
    company check at all."""
    from fastapi import HTTPException

    from app.api import approvals
    from app.models.approval import ApprovalStep, ApprovalWorkflow

    version_b = _make_version(db_session, two_companies["company_b"], status="draft")

    wf = ApprovalWorkflow(
        name="Standard",
        levels=[{"level": 1, "role": "reviewer"}],
        require_sod=False,
    )
    db_session.add(wf)
    db_session.commit()

    # submit_for_approval: user_a must not be able to submit company B's version.
    with pytest.raises(HTTPException) as exc:
        await approvals.submit_for_approval(
            approvals.SubmitApprovalRequest(version_id=version_b.id),
            current_user=two_companies["user_a"],
            db=db_session,
        )
    assert exc.value.status_code == 404

    # approval_status: same refusal.
    with pytest.raises(HTTPException) as exc:
        await approvals.approval_status(
            version_id=version_b.id, current_user=two_companies["user_a"], db=db_session
        )
    assert exc.value.status_code == 404

    # decide_step: submit for real as company B, then try to decide as user_a.
    submit_result = await approvals.submit_for_approval(
        approvals.SubmitApprovalRequest(version_id=version_b.id, workflow_id=wf.id),
        current_user=two_companies["user_b"],
        db=db_session,
    )
    assert submit_result["success"]
    step = (
        db_session.query(ApprovalStep)
        .filter(ApprovalStep.version_id == version_b.id)
        .first()
    )
    assert step is not None
    with pytest.raises(HTTPException) as exc:
        await approvals.decide_step(
            approvals.DecideRequest(step_id=step.id, action="approve"),
            current_user=two_companies["user_a"],
            db=db_session,
        )
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_budget_bridge_prior_version_never_crosses_companies(db_session, two_companies):
    """budget_bridge's 'prior version' fallback must stay inside the caller's
    company even when another company has a newer published version --
    previously it searched published/approved versions globally."""
    from app.api import executive

    version_a = _make_version(db_session, two_companies["company_a"], status="draft", name="A current")
    # Newer, published, but belongs to company B -- must never be picked as
    # company A's "prior" comparison.
    _make_version(db_session, two_companies["company_b"], status="published", name="B published")

    result = await executive.budget_bridge(
        version_id=version_a.id,
        materiality_pct=5.0,
        page=1,
        page_size=50,
        attribute=False,
        convention="volume_first",
        current_user=two_companies["user_a"],
        db=db_session,
    )
    assert result["prior_version_id"] is None


@pytest.mark.asyncio
async def test_dashboard_and_panel_endpoints_refuse_cross_company_versions(db_session, two_companies):
    """Representative sample of the newly-guarded dashboard.py/panel.py
    endpoints -- proves get_accessible_forecast_version is actually wired in,
    not exhaustive per-endpoint coverage."""
    from fastapi import HTTPException

    from app.api import dashboard, panel

    version_b = _make_version(db_session, two_companies["company_b"], status="draft")

    with pytest.raises(HTTPException) as exc:
        await dashboard.get_review_dashboard(
            version_id=version_b.id, current_user=two_companies["user_a"], db=db_session
        )
    assert exc.value.status_code == 404

    with pytest.raises(HTTPException) as exc:
        await panel.get_forecast_table(
            version_id=version_b.id, current_user=two_companies["user_a"], db=db_session
        )
    assert exc.value.status_code == 404

    version_a = _make_version(db_session, two_companies["company_a"], status="draft")
    with pytest.raises(HTTPException) as exc:
        await panel.get_comparison_panel(
            version_id_a=version_a.id,
            version_id_b=version_b.id,
            current_user=two_companies["user_a"],
            db=db_session,
        )
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_review_forecast_skill_never_crosses_companies(db_session, two_companies):
    """submit_for_review (a write) on the chat review_forecast skill must be
    refused cross-company -- same unscoped-lookup pattern as the REST layer,
    just reached via chat instead of HTTP."""
    from app.domain.skills.review_forecast import ReviewForecastSkill

    version_b = _make_version(db_session, two_companies["company_b"], status="draft", name="B draft")

    result = await ReviewForecastSkill().execute(
        {"action": "submit_for_review", "version_id": version_b.id},
        two_companies["ctx_a"],
    )
    assert not result.success
    assert version_b.status == "draft"  # never mutated by the refused caller

    # Same action, same company, must actually work.
    result_b = await ReviewForecastSkill().execute(
        {"action": "submit_for_review", "version_id": version_b.id},
        two_companies["ctx_b"],
    )
    assert result_b.success
    assert version_b.status == "in_review"


@pytest.mark.asyncio
async def test_explain_variance_skill_never_crosses_companies(db_session, two_companies):
    from app.domain.skills.explain_variance import ExplainVarianceSkill

    version_b = _make_version(db_session, two_companies["company_b"], status="draft")

    result = await ExplainVarianceSkill().execute(
        {"version_id": version_b.id}, two_companies["ctx_a"]
    )
    assert not result.success


@pytest.mark.asyncio
async def test_branch_forecast_never_crosses_companies_and_inherits_business_unit(
    db_session, two_companies
):
    """create_branch must refuse a cross-company source, and -- separately --
    a branch created from an accessible source must inherit its
    business_unit_id (clone_version_for_edit/the branch-create path
    previously dropped it, silently orphaning the clone from its company)."""
    from app.domain.skills.branch_forecast import BranchForecastSkill
    from app.models.forecast import ForecastVersion

    version_b = _make_version(db_session, two_companies["company_b"], status="draft")

    refused = await BranchForecastSkill().execute(
        {"action": "create_branch", "source_version_id": version_b.id},
        two_companies["ctx_a"],
    )
    assert not refused.success

    version_a = _make_version(db_session, two_companies["company_a"], status="draft")
    ok = await BranchForecastSkill().execute(
        {"action": "create_branch", "source_version_id": version_a.id},
        two_companies["ctx_a"],
    )
    assert ok.success, ok.message
    branch = db_session.query(ForecastVersion).filter(
        ForecastVersion.id == ok.data["branch_version_id"]
    ).first()
    assert branch is not None
    assert branch.business_unit_id == two_companies["company_a"].id


@pytest.mark.asyncio
async def test_model_presets_scoped_global_vs_company(db_session, two_companies, seed_roles):
    """A company-scoped caller sees global + their own presets, never another
    company's; can't edit a global preset; a cross-BU caller can do both."""
    from app.services.model_presets import create_preset, get_preset, list_presets, update_preset

    admin = _make_admin_user(db_session, seed_roles, "cross_admin")

    global_preset = create_preset(
        db_session, name="Conservative", description=None, actor=admin
    )
    assert global_preset.business_unit_id is None

    preset_a = create_preset(
        db_session, name="Aggressive", description=None, actor=two_companies["user_a"]
    )
    assert preset_a.business_unit_id == two_companies["company_a"].id

    # Company B can reuse the same name company A used -- different scope.
    preset_b = create_preset(
        db_session, name="Aggressive", description=None, actor=two_companies["user_b"]
    )
    assert preset_b.business_unit_id == two_companies["company_b"].id

    names_for_a = {p.name for p in list_presets(db_session, actor=two_companies["user_a"])}
    assert names_for_a == {"Conservative", "Aggressive"}  # global + own, not B's

    # A can't even see B's preset by id.
    assert get_preset(db_session, preset_b.id, actor=two_companies["user_a"]) is None

    # A can't edit the global preset (shared with every company).
    with pytest.raises(ValueError):
        update_preset(
            db_session, global_preset.id, actor=two_companies["user_a"], description="hijacked"
        )

    # The cross-BU admin sees all three rows (global + both companies' same-named ones).
    ids_for_admin = {p.id for p in list_presets(db_session, actor=admin)}
    assert ids_for_admin == {global_preset.id, preset_a.id, preset_b.id}
    updated = update_preset(db_session, global_preset.id, actor=admin, description="curated")
    assert updated.description == "curated"


@pytest.mark.asyncio
async def test_model_preset_create_forces_own_company_scope(db_session, two_companies):
    """A company-scoped caller can't request a global preset or another
    company's scope by passing business_unit_id explicitly -- it's ignored."""
    from app.services.model_presets import create_preset

    preset = create_preset(
        db_session,
        name="Sneaky Global",
        description=None,
        actor=two_companies["user_a"],
        business_unit_id=None,  # requesting global
    )
    assert preset.business_unit_id == two_companies["company_a"].id  # forced to own company

    preset2 = create_preset(
        db_session,
        name="Sneaky Cross",
        description=None,
        actor=two_companies["user_a"],
        business_unit_id=two_companies["company_b"].id,  # requesting B's scope
    )
    assert preset2.business_unit_id == two_companies["company_a"].id  # still forced to own
