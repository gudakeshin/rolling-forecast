"""Tests for Phase 4 skills: ensemble, commentary, branching, accuracy, anomalies."""

import pytest
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.domain.base_skill import SkillContext
from app.models.forecast import ForecastVersion, ForecastLineResult
from app.models.actuals import ActualsDataset, ActualsRecord
from app.models.line_item import LineItem


@pytest.fixture
def seed_forecast_data(db_session):
    """Seed a version with forecast data and actuals for testing."""
    # Create line items
    li_revenue = LineItem(
        account_code="REV-001",
        name="Product Revenue",
        category="Revenue",
    )
    li_cogs = LineItem(
        account_code="COGS-001",
        name="Cost of Goods",
        category="COGS",
        allow_negative=True,
    )
    db_session.add_all([li_revenue, li_cogs])
    db_session.flush()

    # Create actuals dataset
    dataset = ActualsDataset(
        source_type="csv",
        source_name="test_actuals.csv",
        file_hash="testhash123",
        row_count=24,
        period_start="2024-01",
        period_end="2025-12",
        periods_count=24,
    )
    db_session.add(dataset)
    db_session.flush()

    # Create actuals records (24 months)
    import random
    random.seed(42)
    for i in range(24):
        month = f"2024-{(i % 12) + 1:02d}" if i < 12 else f"2025-{(i % 12) + 1:02d}"
        db_session.add(ActualsRecord(
            dataset_id=dataset.id,
            line_item_id=li_revenue.id,
            period=month,
            value=100000 + random.uniform(-10000, 10000),
        ))
        db_session.add(ActualsRecord(
            dataset_id=dataset.id,
            line_item_id=li_cogs.id,
            period=month,
            value=-50000 + random.uniform(-5000, 5000),
        ))

    # Create forecast version
    version = ForecastVersion(
        name="FC-test-v1",
        status="draft",
        version_type="scheduled",
        actuals_dataset_id=dataset.id,
        actuals_hash="testhash123",
        horizon_months=6,
        base_period="2025-12",
        total_line_items=2,
    )
    db_session.add(version)
    db_session.flush()

    # Create forecast results
    for i in range(6):
        period = f"2026-{i + 1:02d}"
        db_session.add(ForecastLineResult(
            version_id=version.id,
            line_item_id=li_revenue.id,
            period=period,
            p10=90000, p50=100000, p90=110000,
            confidence_score=55,
            confidence_level="medium",
            model_type="linear",
            model_mape=8.5,
        ))
        db_session.add(ForecastLineResult(
            version_id=version.id,
            line_item_id=li_cogs.id,
            period=period,
            p10=-60000, p50=-50000, p90=-40000,
            confidence_score=45,
            confidence_level="low",
            model_type="linear",
            model_mape=12.0,
        ))

    db_session.commit()

    return {
        "version": version,
        "dataset": dataset,
        "revenue": li_revenue,
        "cogs": li_cogs,
    }


class TestRunEnsembleSkill:
    """Tests for RunEnsembleSkill."""

    @pytest.mark.asyncio
    async def test_ensemble_with_seed_data(
        self, db_session, context_manager, seed_forecast_data
    ):
        from app.domain.skills.run_ensemble import RunEnsembleSkill

        skill = RunEnsembleSkill()
        context = SkillContext(
            db=db_session,
            context_manager=context_manager,
            user_id="test-user",
            user_role="admin",
            conversation_id="test-conv",
        )

        version = seed_forecast_data["version"]
        context.context_manager.set_active_version_id(version.id)

        result = await skill.execute(
            {"weighting_method": "equal", "top_k": 2},
            context,
        )

        assert result.success
        assert "ensemble" in result.message.lower() or "Ensemble" in result.message
        assert result.data.get("weighting_method") == "equal"

    @pytest.mark.asyncio
    async def test_ensemble_specific_line_item(
        self, db_session, context_manager, seed_forecast_data
    ):
        from app.domain.skills.run_ensemble import RunEnsembleSkill

        skill = RunEnsembleSkill()
        context = SkillContext(
            db=db_session,
            context_manager=context_manager,
            user_id="test-user",
            user_role="admin",
            conversation_id="test-conv",
        )

        version = seed_forecast_data["version"]
        context.context_manager.set_active_version_id(version.id)

        result = await skill.execute(
            {"line_item_name": "Product Revenue", "top_k": 2},
            context,
        )

        assert result.success


class TestGenerateCommentarySkill:
    """Tests for GenerateCommentarySkill."""

    @pytest.mark.asyncio
    async def test_executive_commentary(
        self, db_session, context_manager, seed_forecast_data
    ):
        from app.domain.skills.generate_commentary import GenerateCommentarySkill

        skill = GenerateCommentarySkill()
        context = SkillContext(
            db=db_session,
            context_manager=context_manager,
            user_id="test-user",
            user_role="admin",
            conversation_id="test-conv",
        )

        version = seed_forecast_data["version"]
        context.context_manager.set_active_version_id(version.id)

        result = await skill.execute(
            {"scope": "executive", "tone": "formal"},
            context,
        )

        assert result.success
        assert "executive" in result.data.get("scope", "")
        assert len(result.content_blocks) > 0

    @pytest.mark.asyncio
    async def test_concise_commentary(
        self, db_session, context_manager, seed_forecast_data
    ):
        from app.domain.skills.generate_commentary import GenerateCommentarySkill

        skill = GenerateCommentarySkill()
        context = SkillContext(
            db=db_session,
            context_manager=context_manager,
            user_id="test-user",
            user_role="admin",
            conversation_id="test-conv",
        )

        version = seed_forecast_data["version"]
        context.context_manager.set_active_version_id(version.id)

        result = await skill.execute(
            {"scope": "executive", "tone": "concise"},
            context,
        )

        assert result.success
        assert result.data.get("tone") == "concise"


class TestBranchForecastSkill:
    """Tests for BranchForecastSkill."""

    @pytest.mark.asyncio
    async def test_create_branch(
        self, db_session, context_manager, seed_forecast_data
    ):
        from app.domain.skills.branch_forecast import BranchForecastSkill

        skill = BranchForecastSkill()
        context = SkillContext(
            db=db_session,
            context_manager=context_manager,
            user_id="test-user",
            user_role="admin",
            conversation_id="test-conv",
        )

        version = seed_forecast_data["version"]
        context.context_manager.set_active_version_id(version.id)

        result = await skill.execute(
            {
                "action": "create_branch",
                "branch_name": "Optimistic Scenario",
                "adjustments": {"Revenue": 10, "COGS": -5},
                "description": "Test optimistic scenario",
            },
            context,
        )

        assert result.success
        assert result.data.get("branch_name") == "Optimistic Scenario"
        assert result.data.get("lines_copied") > 0
        assert result.data.get("lines_adjusted") > 0

    @pytest.mark.asyncio
    async def test_list_branches(
        self, db_session, context_manager, seed_forecast_data
    ):
        from app.domain.skills.branch_forecast import BranchForecastSkill

        skill = BranchForecastSkill()
        context = SkillContext(
            db=db_session,
            context_manager=context_manager,
            user_id="test-user",
            user_role="admin",
            conversation_id="test-conv",
        )

        version = seed_forecast_data["version"]
        context.context_manager.set_active_version_id(version.id)

        # Create a branch first
        await skill.execute(
            {"action": "create_branch", "branch_name": "Test Branch"},
            context,
        )

        # List branches
        result = await skill.execute(
            {"action": "list_branches"},
            context,
        )

        assert result.success

    @pytest.mark.asyncio
    async def test_create_and_compare_branch(
        self, db_session, context_manager, seed_forecast_data
    ):
        from app.domain.skills.branch_forecast import BranchForecastSkill

        skill = BranchForecastSkill()
        context = SkillContext(
            db=db_session,
            context_manager=context_manager,
            user_id="test-user",
            user_role="admin",
            conversation_id="test-conv",
        )

        version = seed_forecast_data["version"]
        context.context_manager.set_active_version_id(version.id)

        # Create branch
        create_result = await skill.execute(
            {"action": "create_branch", "branch_name": "Compare Test", "adjustments": {"Revenue": 20}},
            context,
        )
        branch_id = create_result.data["branch_version_id"]

        # Compare
        compare_result = await skill.execute(
            {"action": "compare_to_base", "branch_version_id": branch_id},
            context,
        )

        assert compare_result.success
        assert compare_result.data.get("variance_count", 0) > 0


class TestAutoAccuracyReportSkill:
    """Tests for AutoAccuracyReportSkill."""

    @pytest.mark.asyncio
    async def test_accuracy_summary(
        self, db_session, context_manager, seed_forecast_data
    ):
        from app.domain.skills.auto_accuracy_report import AutoAccuracyReportSkill

        skill = AutoAccuracyReportSkill()
        context = SkillContext(
            db=db_session,
            context_manager=context_manager,
            user_id="test-user",
            user_role="admin",
            conversation_id="test-conv",
        )

        version = seed_forecast_data["version"]
        context.context_manager.set_active_version_id(version.id)

        result = await skill.execute(
            {"report_type": "accuracy_summary"},
            context,
        )

        assert result.success

    @pytest.mark.asyncio
    async def test_bias_analysis(
        self, db_session, context_manager, seed_forecast_data
    ):
        from app.domain.skills.auto_accuracy_report import AutoAccuracyReportSkill

        skill = AutoAccuracyReportSkill()
        context = SkillContext(
            db=db_session,
            context_manager=context_manager,
            user_id="test-user",
            user_role="admin",
            conversation_id="test-conv",
        )

        version = seed_forecast_data["version"]
        context.context_manager.set_active_version_id(version.id)

        result = await skill.execute(
            {"report_type": "bias_analysis"},
            context,
        )

        assert result.success


class TestDetectAnomaliesSkill:
    """Tests for DetectAnomaliesSkill."""

    @pytest.mark.asyncio
    async def test_detect_actuals_anomalies(
        self, db_session, context_manager, seed_forecast_data
    ):
        from app.domain.skills.detect_anomalies import DetectAnomaliesSkill

        skill = DetectAnomaliesSkill()
        context = SkillContext(
            db=db_session,
            context_manager=context_manager,
            user_id="test-user",
            user_role="admin",
            conversation_id="test-conv",
        )

        version = seed_forecast_data["version"]
        context.context_manager.set_active_version_id(version.id)

        result = await skill.execute(
            {"target": "actuals", "method": "zscore", "sensitivity": "high"},
            context,
        )

        assert result.success

    @pytest.mark.asyncio
    async def test_detect_forecast_anomalies(
        self, db_session, context_manager, seed_forecast_data
    ):
        from app.domain.skills.detect_anomalies import DetectAnomaliesSkill

        skill = DetectAnomaliesSkill()
        context = SkillContext(
            db=db_session,
            context_manager=context_manager,
            user_id="test-user",
            user_role="admin",
            conversation_id="test-conv",
        )

        version = seed_forecast_data["version"]
        context.context_manager.set_active_version_id(version.id)

        result = await skill.execute(
            {"target": "both", "method": "all", "sensitivity": "medium"},
            context,
        )

        assert result.success

    @pytest.mark.asyncio
    async def test_detect_for_specific_category(
        self, db_session, context_manager, seed_forecast_data
    ):
        from app.domain.skills.detect_anomalies import DetectAnomaliesSkill

        skill = DetectAnomaliesSkill()
        context = SkillContext(
            db=db_session,
            context_manager=context_manager,
            user_id="test-user",
            user_role="admin",
            conversation_id="test-conv",
        )

        version = seed_forecast_data["version"]
        context.context_manager.set_active_version_id(version.id)

        result = await skill.execute(
            {"target": "actuals", "category": "Revenue", "sensitivity": "medium"},
            context,
        )

        assert result.success
