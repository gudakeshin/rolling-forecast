"""Agent-authored forecast-settings changes: read, propose, confirm-gated apply."""

from __future__ import annotations

import pytest

from app.domain.base_skill import SkillContext
from app.domain.skills.forecast_settings import (
    GetForecastSettingsSkill,
    UpdateForecastSettingsSkill,
)
from app.models.audit import AuditEvent
from app.models.business_unit import BusinessUnit
from app.services.analysis_profile import resolve_analysis_settings


def _ctx(db, user, context_manager, permissions):
    context_manager.user = user
    context_manager.permissions = permissions
    return SkillContext(
        db=db, context_manager=context_manager, user_id=user.id,
        user_role=user.role.name if user.role else "analyst",
        conversation_id="test-conv", user=user,
    )


@pytest.fixture
def admin_ctx(db_session, seed_users, seed_business_unit, context_manager):
    admin = seed_users["admin"]
    admin.business_unit_id = seed_business_unit.id
    admin.is_platform_admin = False  # a company admin, not cross-company
    db_session.commit()
    return _ctx(db_session, admin, context_manager, {"input", "generate", "admin"})


@pytest.mark.asyncio
async def test_get_shows_effective_settings_and_sources(admin_ctx, seed_business_unit):
    result = await GetForecastSettingsSkill().execute({}, admin_ctx)
    assert result.success, result.message
    assert result.data["business_unit_id"] == seed_business_unit.id
    assert "materiality_share" in result.data["settings"]


@pytest.mark.asyncio
async def test_update_without_confirm_only_proposes(db_session, admin_ctx, seed_business_unit):
    before = resolve_analysis_settings(db_session, business_unit_id=seed_business_unit.id)
    target = round(before.materiality_share + 0.01, 4)

    result = await UpdateForecastSettingsSkill().execute(
        {"changes": {"materiality_share": target}}, admin_ctx
    )
    assert result.success, result.message
    assert result.data["applied"] is False
    assert result.data["diff"][0]["proposed"] == str(target)
    after = resolve_analysis_settings(db_session, business_unit_id=seed_business_unit.id)
    assert after.materiality_share == before.materiality_share


@pytest.mark.asyncio
async def test_update_with_confirm_applies_and_audits(db_session, admin_ctx, seed_business_unit):
    current = resolve_analysis_settings(db_session, business_unit_id=seed_business_unit.id)
    result = await UpdateForecastSettingsSkill().execute(
        {
            "changes": {"enable_benchmark_models": not current.enable_benchmark_models},
            "confirm": True,
        },
        admin_ctx,
    )
    assert result.success, result.message
    assert result.data["applied"] is True
    after = resolve_analysis_settings(db_session, business_unit_id=seed_business_unit.id)
    assert after.enable_benchmark_models is (not current.enable_benchmark_models)

    event = (
        db_session.query(AuditEvent)
        .filter(AuditEvent.action == "analysis_profile.update")
        .order_by(AuditEvent.id.desc())
        .first()
    )
    assert event is not None
    assert event.details["source"] == "agent"
    assert event.details["field"] == "enable_benchmark_models"


@pytest.mark.asyncio
async def test_no_op_change_is_reported_not_written(admin_ctx, db_session, seed_business_unit):
    current = resolve_analysis_settings(db_session, business_unit_id=seed_business_unit.id)
    result = await UpdateForecastSettingsSkill().execute(
        {"changes": {"selection_metric": current.selection_metric}, "confirm": True}, admin_ctx
    )
    assert result.success
    assert result.data["applied"] is False


@pytest.mark.parametrize(
    "changes, message",
    [
        ({"no_such_setting": 1}, "Unknown"),
        ({"enable_benchmark_models": "yes"}, "boolean"),
        ({"materiality_share": "0.05"}, "number"),
        ({}, "non-empty"),
    ],
)
@pytest.mark.asyncio
async def test_invalid_changes_fail_before_anything_is_written(admin_ctx, changes, message):
    result = await UpdateForecastSettingsSkill().execute(
        {"changes": changes, "confirm": True}, admin_ctx
    )
    assert not result.success
    assert message in result.message


@pytest.mark.asyncio
async def test_cannot_touch_another_company(db_session, admin_ctx):
    other = BusinessUnit(name="Forecast Settings Other Co")
    db_session.add(other)
    db_session.commit()
    result = await UpdateForecastSettingsSkill().execute(
        {"changes": {"materiality_share": 0.5}, "confirm": True, "business_unit": other.id},
        admin_ctx,
    )
    assert not result.success
    assert "not found" in result.message
    result = await GetForecastSettingsSkill().execute({"business_unit": other.name}, admin_ctx)
    assert not result.success


def test_write_skill_requires_admin_and_read_skill_does_not(
    db_session, seed_users, context_manager
):
    analyst_ctx = _ctx(db_session, seed_users["analyst"], context_manager, {"input", "generate", "override"})
    assert GetForecastSettingsSkill().validate_permissions(analyst_ctx)
    assert not UpdateForecastSettingsSkill().validate_permissions(analyst_ctx)


def test_skills_are_registered_with_definitions():
    from app.domain.registry import register_all_skills

    registry = register_all_skills()
    for name in ("get_forecast_settings", "update_forecast_settings"):
        assert registry.get(name) is not None
        assert registry.get_definition(name) is not None
    assert registry.get_definition("update_forecast_settings").required_role == "admin"
