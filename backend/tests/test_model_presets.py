"""Model presets — service layer, skill layer, and generate_baseline wiring."""

from __future__ import annotations

import pytest

from app.config import settings
from app.domain.skills.generate_baseline import GenerateBaselineSkill
from app.domain.skills.manage_model_presets import (
    ListModelPresetsSkill,
    ManageModelPresetsSkill,
)
from app.services.model_presets import (
    create_preset,
    deactivate_preset,
    find_preset,
    list_presets,
    resolve_preset,
    update_preset,
)


class TestServiceLayer:
    def test_create_and_get(self, db_session, seed_users):
        preset = create_preset(
            db_session,
            name="Conservative",
            description="ETS only",
            model_type="ets",
            candidate_models=None,
            default_horizon_months=6,
            actor=seed_users["admin"],
        )
        assert preset.id
        assert preset.is_active is True
        fetched = find_preset(db_session, preset.id)
        assert fetched.name == "Conservative"
        by_name = find_preset(db_session, "conservative")  # case-insensitive
        assert by_name.id == preset.id

    def test_duplicate_name_rejected(self, db_session, seed_users):
        create_preset(db_session, name="Dup", description=None, actor=seed_users["admin"])
        with pytest.raises(ValueError, match="already exists"):
            create_preset(db_session, name="dup", description=None, actor=seed_users["admin"])

    def test_candidate_models_requires_auto(self, db_session, seed_users):
        with pytest.raises(ValueError, match="model_type is 'auto'"):
            create_preset(
                db_session,
                name="BadCombo",
                description=None,
                model_type="ets",
                candidate_models=["arima", "ets"],
                actor=seed_users["admin"],
            )

    def test_list_excludes_inactive_by_default(self, db_session, seed_users):
        p = create_preset(db_session, name="ToRetire", description=None, actor=seed_users["admin"])
        deactivate_preset(db_session, p.id, actor=seed_users["admin"])
        active = list_presets(db_session)
        assert p.id not in {r.id for r in active}
        all_rows = list_presets(db_session, include_inactive=True)
        assert p.id in {r.id for r in all_rows}

    def test_deactivate_idempotent(self, db_session, seed_users):
        p = create_preset(db_session, name="Retire2", description=None, actor=seed_users["admin"])
        first = deactivate_preset(db_session, p.id, actor=seed_users["admin"])
        second = deactivate_preset(db_session, p.id, actor=seed_users["admin"])
        assert first.is_active is False
        assert second.is_active is False

    def test_update_rename_conflict(self, db_session, seed_users):
        create_preset(db_session, name="Alpha", description=None, actor=seed_users["admin"])
        beta = create_preset(db_session, name="Beta", description=None, actor=seed_users["admin"])
        with pytest.raises(ValueError, match="already exists"):
            update_preset(db_session, beta.id, name="alpha", actor=seed_users["admin"])


class TestResolvePreset:
    def test_auto_with_candidate_models(self, db_session, seed_users):
        p = create_preset(
            db_session,
            name="LimitedAuto",
            description=None,
            model_type="auto",
            candidate_models=["ets", "linear"],
            actor=seed_users["admin"],
        )
        resolved = resolve_preset(db_session, p.id)
        assert resolved["model_type"] == "auto"
        assert set(resolved["models_to_test"]) == {"ets", "linear"}
        assert resolved["warnings"] == []

    def test_pinned_model_not_registered_fails_closed(self, db_session, seed_users, monkeypatch):
        monkeypatch.setattr(settings, "enable_global_gbm_model", False)
        p = create_preset(
            db_session,
            name="GBMPreset",
            description=None,
            model_type="global_gbm",
            actor=seed_users["admin"],
        )
        with pytest.raises(ValueError, match="not currently registered"):
            resolve_preset(db_session, p.id)

    def test_candidate_models_partial_unregistered_filtered_with_warning(
        self, db_session, seed_users
    ):
        p = create_preset(
            db_session,
            name="PartlyBad",
            description=None,
            model_type="auto",
            candidate_models=["ets", "totally_not_a_model"],
            actor=seed_users["admin"],
        )
        resolved = resolve_preset(db_session, p.id)
        assert resolved["models_to_test"] == ["ets"]
        assert resolved["warnings"]
        assert "totally_not_a_model" in resolved["warnings"][0]

    def test_resolve_missing_preset_raises(self, db_session):
        with pytest.raises(ValueError, match="not found"):
            resolve_preset(db_session, "does-not-exist")

    def test_resolve_deactivated_preset_raises(self, db_session, seed_users):
        p = create_preset(db_session, name="Gone", description=None, actor=seed_users["admin"])
        deactivate_preset(db_session, p.id, actor=seed_users["admin"])
        with pytest.raises(ValueError, match="deactivated"):
            resolve_preset(db_session, p.id)

    def test_override_horizon_wins_over_default(self, db_session, seed_users):
        p = create_preset(
            db_session,
            name="HorizonPreset",
            description=None,
            default_horizon_months=12,
            actor=seed_users["admin"],
        )
        resolved = resolve_preset(db_session, p.id, override_horizon=3)
        assert resolved["horizon_months"] == 3


class TestSkillLayer:
    @pytest.mark.asyncio
    async def test_list_model_presets_empty(self, skill_context):
        result = await ListModelPresetsSkill().execute({}, skill_context)
        assert result.success is True
        assert result.data["presets"] == []

    @pytest.mark.asyncio
    async def test_manage_create_then_list(self, skill_context, seed_users):
        create_result = await ManageModelPresetsSkill().execute(
            {"action": "create", "name": "ViaSkill", "model_type": "ets"},
            skill_context,
        )
        assert create_result.success is True
        preset_id = create_result.data["preset"]["id"]

        list_result = await ListModelPresetsSkill().execute({}, skill_context)
        names = [p["name"] for p in list_result.data["presets"]]
        assert "ViaSkill" in names

        get_result = await ListModelPresetsSkill().execute(
            {"preset": "ViaSkill"}, skill_context
        )
        assert get_result.data["preset"]["id"] == preset_id

    @pytest.mark.asyncio
    async def test_manage_unknown_action_fails(self, skill_context):
        result = await ManageModelPresetsSkill().execute({"action": "bogus"}, skill_context)
        assert result.success is False

    @pytest.mark.asyncio
    async def test_manage_deactivate_requires_preset_id(self, skill_context):
        result = await ManageModelPresetsSkill().execute(
            {"action": "deactivate"}, skill_context
        )
        assert result.success is False
        assert "preset_id" in result.message


class TestGenerateBaselinePresetWiring:
    @pytest.mark.asyncio
    async def test_unresolvable_preset_fails_fast(self, skill_context, seed_users, monkeypatch):
        """A preset pinned to a disabled/unregistered model must fail closed,
        never silently fall back to auto — and it should fail before any
        dataset/line-item lookups (no fixtures needed for those here)."""
        monkeypatch.setattr(settings, "enable_global_gbm_model", False)
        preset = create_preset(
            skill_context.db,
            name="DisabledGBM",
            description=None,
            model_type="global_gbm",
            actor=seed_users["admin"],
        )
        result = await GenerateBaselineSkill().execute(
            {"model_preset": preset.id, "async_job": False},
            skill_context,
        )
        assert result.success is False
        assert "not currently registered" in result.message

    @pytest.mark.asyncio
    async def test_unknown_preset_name_fails(self, skill_context):
        result = await GenerateBaselineSkill().execute(
            {"model_preset": "NoSuchPreset", "async_job": False},
            skill_context,
        )
        assert result.success is False
        assert "not found" in result.message
