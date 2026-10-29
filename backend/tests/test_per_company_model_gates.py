"""Per-company benchmark / global-GBM gates (AnalysisProfile + per-gate registries)."""

from __future__ import annotations

import pytest

from app.config import settings
from app.domain.engines.model_registry import (
    get_full_model_registry,
    get_model_registry,
    registry_for_analysis,
)
from app.domain.skills.generate_baseline import GenerateBaselineSkill
from app.services.analysis_profile import resolve_analysis_settings, update_analysis_profile
from app.services.model_presets import create_preset, resolve_preset


def test_registry_membership_follows_the_gates():
    off = get_model_registry(enable_benchmark_models=False, enable_global_gbm_model=False)
    on = get_model_registry(enable_benchmark_models=True, enable_global_gbm_model=True)
    assert not {"naive", "seasonal_naive", "global_gbm"} & set(off.list_models())
    assert {"naive", "seasonal_naive", "global_gbm"} <= set(on.list_models())
    assert get_full_model_registry() is on


def test_registries_are_cached_per_gate_combination():
    a = get_model_registry(enable_benchmark_models=False, enable_global_gbm_model=True)
    b = get_model_registry(enable_benchmark_models=False, enable_global_gbm_model=True)
    assert a is b


def test_unset_gates_fall_back_to_global_settings(monkeypatch):
    monkeypatch.setattr(settings, "enable_benchmark_models", False)
    monkeypatch.setattr(settings, "enable_global_gbm_model", True)
    reg = get_model_registry()
    assert "naive" not in reg.list_models()
    assert "global_gbm" in reg.list_models()


def test_company_override_picks_its_own_registry(db_session, seed_users, seed_business_unit, monkeypatch):
    monkeypatch.setattr(settings, "enable_global_gbm_model", False)
    monkeypatch.setattr(settings, "enable_benchmark_models", True)
    bu_id = seed_business_unit.id

    assert "global_gbm" not in registry_for_analysis(
        resolve_analysis_settings(db_session, business_unit_id=bu_id)
    ).list_models()

    update_analysis_profile(
        db_session,
        business_unit_id=bu_id,
        patch={"enable_global_gbm_model": True, "enable_benchmark_models": False},
        actor=seed_users["admin"],
    )
    analysis = resolve_analysis_settings(db_session, business_unit_id=bu_id)
    assert analysis.enable_global_gbm_model is True
    models = set(registry_for_analysis(analysis).list_models())
    assert "global_gbm" in models
    assert "naive" not in models

    # Another company (none here) still gets the global defaults.
    global_models = set(
        registry_for_analysis(resolve_analysis_settings(db_session, business_unit_id=None)).list_models()
    )
    assert "global_gbm" not in global_models
    assert "naive" in global_models


def test_resolve_preset_validates_against_the_given_registry(db_session, seed_users):
    preset = create_preset(
        db_session, name="PinGBM", description=None, model_type="global_gbm",
        actor=seed_users["admin"],
    )
    resolved = resolve_preset(db_session, preset.id, registry=get_full_model_registry())
    assert resolved["model_type"] == "global_gbm"
    with pytest.raises(ValueError, match="not currently registered"):
        resolve_preset(
            db_session, preset.id,
            registry=get_model_registry(enable_benchmark_models=True, enable_global_gbm_model=False),
        )


@pytest.mark.asyncio
async def test_preset_candidates_disabled_for_company_are_skipped_with_warning(
    skill_context, seed_users, seed_actuals, monkeypatch
):
    monkeypatch.setattr(settings, "enable_global_gbm_model", False)
    preset = create_preset(
        skill_context.db, name="LinearOrGBM", description=None, model_type="auto",
        candidate_models=["linear", "global_gbm"], actor=seed_users["admin"],
    )
    result = await GenerateBaselineSkill().execute(
        {"model_preset": preset.id, "async_job": False, "skip_plan_check": True},
        skill_context,
    )
    assert result.success is True, result.message
    # Surfaced to the user in the run's warnings block, not only the log.
    assert "disabled for this company" in str(result.content_blocks)


@pytest.mark.asyncio
async def test_preset_with_only_disabled_candidates_fails(
    skill_context, seed_users, seed_actuals, monkeypatch
):
    monkeypatch.setattr(settings, "enable_global_gbm_model", False)
    preset = create_preset(
        skill_context.db, name="OnlyGBM", description=None, model_type="auto",
        candidate_models=["global_gbm"], actor=seed_users["admin"],
    )
    result = await GenerateBaselineSkill().execute(
        {"model_preset": preset.id, "async_job": False}, skill_context,
    )
    assert result.success is False
    assert "enabled for this company" in result.message


@pytest.fixture
def client():
    import os

    from fastapi.testclient import TestClient

    os.environ["SEED_DEMO_USERS"] = "true"
    os.environ["APP_ENV"] = "test"
    from app.main import app
    from app.rate_limit import limiter

    try:
        limiter.reset()
    except Exception:
        pass
    with TestClient(app) as c:
        yield c


def _analyst_headers(client) -> dict[str, str]:
    r = client.post("/api/auth/login", json={"username": "analyst", "password": "analyst"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def test_available_models_endpoint_uses_callers_company(client):
    r = client.get("/api/model-presets/available-models", headers=_analyst_headers(client))
    assert r.status_code == 200, r.text
    names = {m["name"] for m in r.json()["models"]}
    assert "linear" in names


def test_available_models_endpoint_hides_other_companies(client):
    import uuid

    from app.database import SessionLocal
    from app.models.business_unit import BusinessUnit
    from app.models.company import Company
    from app.services.tenant_scope import UNRESTRICTED, push_tenant_scope, reset_tenant_scope

    token = push_tenant_scope(UNRESTRICTED)
    db = SessionLocal()
    try:
        company = Company(name=f"GatesOtherCo-{uuid.uuid4().hex[:8]}")
        db.add(company)
        db.flush()
        other_bu = BusinessUnit(name=f"GatesOtherBU-{uuid.uuid4().hex[:8]}", company_id=company.id)
        db.add(other_bu)
        db.commit()
        other_bu_id = other_bu.id
    finally:
        db.close()
        reset_tenant_scope(token)

    r = client.get(
        f"/api/model-presets/available-models?business_unit_id={other_bu_id}",
        headers=_analyst_headers(client),
    )
    assert r.status_code == 404
