"""Tests for domain skills (integration tests)."""

import pytest
from app.domain.skills.ingest_actuals import IngestActualsSkill
from app.domain.skills.generate_baseline import GenerateBaselineSkill
from app.domain.skills.score_confidence import ScoreConfidenceSkill
from app.domain.skills.manage_versions import ManageVersionsSkill
from app.domain.skills.apply_override import ApplyOverrideSkill
from app.domain.skills.compare_forecasts import CompareForecastsSkill
from app.domain.skills.review_forecast import ReviewForecastSkill
from app.domain.skills.export_audit import ExportAuditSkill
from app.domain.base_skill import SkillContext


class TestSkillMetadata:
    """Test that all skills have proper metadata."""

    SKILLS = [
        IngestActualsSkill,
        GenerateBaselineSkill,
        ScoreConfidenceSkill,
        ManageVersionsSkill,
        ApplyOverrideSkill,
        CompareForecastsSkill,
        ReviewForecastSkill,
        ExportAuditSkill,
    ]

    @pytest.mark.parametrize("skill_cls", SKILLS)
    def test_skill_has_name(self, skill_cls):
        skill = skill_cls()
        assert skill.name is not None
        assert len(skill.name) > 0

    @pytest.mark.parametrize("skill_cls", SKILLS)
    def test_skill_has_description(self, skill_cls):
        skill = skill_cls()
        assert skill.description is not None
        assert len(skill.description) > 20

    @pytest.mark.parametrize("skill_cls", SKILLS)
    def test_skill_has_parameters_schema(self, skill_cls):
        skill = skill_cls()
        schema = skill.parameters_schema
        assert isinstance(schema, dict)
        assert "type" in schema
        assert schema["type"] == "object"
        assert "properties" in schema


class TestManageVersionsSkill:
    """Test the version management skill."""

    @pytest.mark.asyncio
    async def test_list_empty_versions(self, skill_context):
        """Listing versions with no data should succeed gracefully."""
        skill = ManageVersionsSkill()
        result = await skill.execute({"action": "list"}, skill_context)
        assert result.success is True
        assert "No forecast versions" in result.message or "versions" in result.message.lower()

    @pytest.mark.asyncio
    async def test_get_nonexistent_version(self, skill_context):
        """Getting a non-existent version should fail gracefully."""
        skill = ManageVersionsSkill()
        result = await skill.execute(
            {"action": "get", "version_id": "nonexistent-id"},
            skill_context,
        )
        assert result.success is False
        assert "not found" in result.message.lower()


class TestGenerateBaselineSkill:
    """Test baseline forecast generation."""

    @pytest.mark.asyncio
    async def test_no_data_returns_error(self, skill_context):
        """Should fail gracefully when no actuals exist."""
        skill = GenerateBaselineSkill()
        result = await skill.execute({}, skill_context)
        assert result.success is False
        assert "no actuals" in result.message.lower() or "no line items" in result.message.lower()

    @pytest.mark.asyncio
    async def test_generate_with_seed_data(self, skill_context, seed_actuals):
        """Should successfully generate forecast with seeded actuals."""
        skill_context.context_manager.set_memory("last_dataset_id", seed_actuals.id)

        skill = GenerateBaselineSkill()
        result = await skill.execute(
            {"horizon_months": 6, "model_type": "linear", "random_seed": 42},
            skill_context,
        )
        assert result.success is True
        assert result.data.get("lines_forecasted", 0) > 0
        assert result.data.get("version_id") is not None


class TestScoreConfidenceSkill:
    """Test confidence scoring."""

    @pytest.mark.asyncio
    async def test_no_version_returns_error(self, skill_context):
        """Should fail gracefully when no version is active."""
        skill = ScoreConfidenceSkill()
        result = await skill.execute({}, skill_context)
        assert result.success is False

    @pytest.mark.asyncio
    async def test_score_after_generation(self, skill_context, seed_actuals):
        """Should score confidence after forecast generation."""
        # Generate first
        skill_context.context_manager.set_memory("last_dataset_id", seed_actuals.id)
        gen_skill = GenerateBaselineSkill()
        gen_result = await gen_skill.execute(
            {"horizon_months": 6, "model_type": "linear", "random_seed": 42},
            skill_context,
        )
        assert gen_result.success is True

        # Then score
        score_skill = ScoreConfidenceSkill()
        result = await score_skill.execute({}, skill_context)
        assert result.success is True
        assert result.data.get("high_count", 0) + result.data.get("medium_count", 0) + result.data.get("low_count", 0) > 0


class TestApplyOverrideSkill:
    """Test override application."""

    @pytest.mark.asyncio
    async def test_no_version_returns_error(self, skill_context):
        """Should fail when no version is active."""
        skill = ApplyOverrideSkill()
        result = await skill.execute(
            {"action": "apply", "line_item_name": "Revenue", "period": "2026-01",
             "new_value": 600000, "reason": "Test override for revenue adjustment"},
            skill_context,
        )
        assert result.success is False

    @pytest.mark.asyncio
    async def test_list_overrides_empty(self, skill_context, seed_actuals):
        """Listing overrides on fresh version should show none."""
        skill_context.context_manager.set_memory("last_dataset_id", seed_actuals.id)
        gen_skill = GenerateBaselineSkill()
        await gen_skill.execute(
            {"horizon_months": 3, "model_type": "linear", "random_seed": 42},
            skill_context,
        )

        skill = ApplyOverrideSkill()
        result = await skill.execute({"action": "list"}, skill_context)
        assert result.success is True
        assert "no active overrides" in result.message.lower() or result.data.get("override_count", 0) == 0

    @pytest.mark.asyncio
    async def test_override_requires_reason(self, skill_context, seed_actuals):
        """EC6: Override without sufficient reason should fail."""
        skill_context.context_manager.set_memory("last_dataset_id", seed_actuals.id)
        gen_skill = GenerateBaselineSkill()
        await gen_skill.execute(
            {"horizon_months": 3, "model_type": "linear", "random_seed": 42},
            skill_context,
        )

        skill = ApplyOverrideSkill()
        result = await skill.execute(
            {"action": "apply", "line_item_name": "Product Revenue",
             "period": "2026-03", "new_value": 600000, "reason": "short"},
            skill_context,
        )
        assert result.success is False
        assert "minimum 10 characters" in result.message.lower()


class TestReviewForecastSkill:
    """Test review workflow."""

    @pytest.mark.asyncio
    async def test_submit_for_review(self, skill_context, seed_actuals):
        """Should submit a draft forecast for review."""
        skill_context.context_manager.set_memory("last_dataset_id", seed_actuals.id)
        gen_skill = GenerateBaselineSkill()
        await gen_skill.execute(
            {"horizon_months": 3, "model_type": "linear", "random_seed": 42},
            skill_context,
        )

        skill = ReviewForecastSkill()
        result = await skill.execute({"action": "submit_for_review"}, skill_context)
        assert result.success is True
        assert "in_review" in result.data.get("status", "") or "submitted" in result.message.lower()

    @pytest.mark.asyncio
    async def test_analyst_cannot_approve(self, skill_context, seed_actuals):
        """RBAC: Analyst should not be able to approve."""
        skill_context.context_manager.set_memory("last_dataset_id", seed_actuals.id)
        gen_skill = GenerateBaselineSkill()
        await gen_skill.execute(
            {"horizon_months": 3, "model_type": "linear", "random_seed": 42},
            skill_context,
        )

        review_skill = ReviewForecastSkill()
        await review_skill.execute({"action": "submit_for_review"}, skill_context)

        # Try to approve as analyst
        result = await review_skill.execute({"action": "approve"}, skill_context)
        assert result.success is False
        assert "manager" in result.message.lower() or "admin" in result.message.lower() or "reviewer" in result.message.lower()


class TestExportAuditSkill:
    """Test audit trail export."""

    @pytest.mark.asyncio
    async def test_export_summary(self, skill_context, seed_actuals):
        """Should generate an audit summary."""
        skill_context.context_manager.set_memory("last_dataset_id", seed_actuals.id)
        gen_skill = GenerateBaselineSkill()
        await gen_skill.execute(
            {"horizon_months": 3, "model_type": "linear", "random_seed": 42},
            skill_context,
        )

        skill = ExportAuditSkill()
        result = await skill.execute({"format": "summary"}, skill_context)
        assert result.success is True
        assert "audit" in result.message.lower() or "trail" in result.message.lower()
        assert len(result.content_blocks) > 0
