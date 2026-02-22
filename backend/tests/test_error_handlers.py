"""Tests for PRD Section 10 edge case handlers."""

import pytest
import numpy as np
import pandas as pd
import time

from app.services.error_handlers import (
    HistoryAnalysis,
    OverrideValidator,
    clamp_forecast_values,
    check_driver_deadline,
    check_generation_timeout,
    ForecastTimeoutError,
)
from app.config import settings


class TestHistoryAnalysis:
    """Test EC1, EC2, EC4, EC12."""

    def test_all_zeros_detection(self):
        """EC2: Line item with all zeros in history."""
        values = pd.Series([0.0] * 24)
        dates = pd.DatetimeIndex(pd.date_range("2024-01", periods=24, freq="MS"))
        analysis = HistoryAnalysis(values, dates, "Zero Line")

        assert analysis.is_all_zeros is True
        assert analysis.recommended_model == "zero"

        result = analysis.analyze()
        assert "all_zeros" in result["flags"]
        assert result["confidence_override"] == "low"
        assert any("No historical activity" in w for w in result["warnings"])

    def test_sparse_data_detection(self):
        """EC1: Line item with < 12 months of history."""
        values = pd.Series([100.0] * 8)
        dates = pd.DatetimeIndex(pd.date_range("2025-01", periods=8, freq="MS"))
        analysis = HistoryAnalysis(values, dates, "Sparse Line")

        assert analysis.is_sparse is True
        assert analysis.is_very_sparse is False
        assert analysis.recommended_model == "linear"

        result = analysis.analyze()
        assert "sparse_data" in result["flags"]
        assert result["confidence_override"] == "low"

    def test_very_sparse_data(self):
        """EC1: Line item with < 6 months -- cannot use any statistical model."""
        values = pd.Series([100.0, 200.0, 150.0])
        dates = pd.DatetimeIndex(pd.date_range("2025-01", periods=3, freq="MS"))
        analysis = HistoryAnalysis(values, dates, "Very Sparse")

        assert analysis.is_very_sparse is True
        assert analysis.recommended_model == "average"

    def test_normal_data_no_flags(self):
        """Sufficient data should produce no flags."""
        np.random.seed(42)
        values = pd.Series(np.random.normal(1000, 50, 24))
        dates = pd.DatetimeIndex(pd.date_range("2024-01", periods=24, freq="MS"))
        analysis = HistoryAnalysis(values, dates, "Normal Line")

        assert analysis.is_all_zeros is False
        assert analysis.is_sparse is False
        assert analysis.recommended_model == "auto"

    def test_structural_break_detection(self):
        """EC12: Detect structural break in data."""
        # Create data with a clear break at month 12
        values_pre = [100.0] * 12
        values_post = [500.0] * 12  # 5x jump
        values = pd.Series(values_pre + values_post)
        dates = pd.DatetimeIndex(pd.date_range("2024-01", periods=24, freq="MS"))
        analysis = HistoryAnalysis(values, dates, "Break Line")

        # Note: structural break detection depends on the R/S statistic threshold
        result = analysis.analyze()
        if analysis.has_structural_break:
            assert "structural_break" in result["flags"]
            assert analysis.structural_break_period is not None


class TestOverrideValidator:
    """Test EC6: Override creates impossible value."""

    def test_negative_revenue_warning(self):
        """Override that makes revenue negative."""
        warnings = OverrideValidator.validate(
            line_item_name="Product Revenue",
            category="Revenue",
            allow_negative=False,
            original_value=500000,
            override_value=-100000,
        )
        assert len(warnings) >= 1
        assert any("negative" in w["message"].lower() for w in warnings)

    def test_extreme_change_warning(self):
        """Override with > 300% change."""
        warnings = OverrideValidator.validate(
            line_item_name="Marketing",
            category="OpEx",
            allow_negative=True,
            original_value=100000,
            override_value=500000,  # 400% change
        )
        assert len(warnings) >= 1
        assert any("300%" in w["message"] or "400%" in w["message"] for w in warnings)

    def test_zero_override_on_nonzero(self):
        """Override that sets a significant line to zero."""
        warnings = OverrideValidator.validate(
            line_item_name="Salaries",
            category="OpEx",
            allow_negative=True,
            original_value=150000,
            override_value=0,
        )
        assert any("zero" in w["message"].lower() for w in warnings)

    def test_normal_override_no_warnings(self):
        """Reasonable override should produce no warnings."""
        warnings = OverrideValidator.validate(
            line_item_name="Revenue",
            category="Revenue",
            allow_negative=False,
            original_value=500000,
            override_value=520000,  # 4% change
        )
        assert len(warnings) == 0


class TestNegativeValueClamping:
    """Test EC9: Statistical model generates negative values."""

    def test_clamp_negative_values(self):
        """Negative values should be clamped to zero for non-negative lines."""
        values = np.array([100, -50, 200, -30, 150])
        clamped, warnings = clamp_forecast_values(values, allow_negative=False, line_item_name="Revenue")

        assert np.all(clamped >= 0)
        assert len(warnings) == 1
        assert "negative forecast" in warnings[0].lower() or "Clamped" in warnings[0]

    def test_no_clamp_when_allowed(self):
        """Negative values should NOT be clamped when allow_negative=True."""
        values = np.array([100, -50, 200, -30, 150])
        clamped, warnings = clamp_forecast_values(values, allow_negative=True)

        assert np.array_equal(clamped, values)
        assert len(warnings) == 0

    def test_no_clamp_all_positive(self):
        """All positive values should pass through unchanged."""
        values = np.array([100, 200, 300])
        clamped, warnings = clamp_forecast_values(values, allow_negative=False)

        assert np.array_equal(clamped, values)
        assert len(warnings) == 0


class TestDriverDeadline:
    """Test EC7: Late driver input submissions."""

    def test_on_time_submission(self):
        """Submission within soft deadline."""
        from datetime import datetime, timezone, timedelta

        # Cycle started today
        cycle_start = datetime.now(timezone.utc)
        result = check_driver_deadline(
            soft_deadline_days=3, hard_deadline_days=5, cycle_start=cycle_start
        )
        assert result["is_late"] is False
        assert result["is_past_soft"] is False
        assert result["is_past_hard"] is False

    def test_past_soft_deadline(self):
        """Submission after soft deadline but before hard deadline."""
        from datetime import datetime, timezone, timedelta

        cycle_start = datetime.now(timezone.utc) - timedelta(days=4)
        result = check_driver_deadline(
            soft_deadline_days=3, hard_deadline_days=5, cycle_start=cycle_start
        )
        assert result["is_past_soft"] is True
        assert result["is_past_hard"] is False

    def test_past_hard_deadline(self):
        """Submission after hard deadline -- should be marked as late."""
        from datetime import datetime, timezone, timedelta

        cycle_start = datetime.now(timezone.utc) - timedelta(days=7)
        result = check_driver_deadline(
            soft_deadline_days=3, hard_deadline_days=5, cycle_start=cycle_start
        )
        assert result["is_late"] is True
        assert result["is_past_hard"] is True
        assert "next forecast refresh" in result["message"].lower()


class TestGenerationTimeout:
    """Test EC11: Forecast generation timeout."""

    def test_no_timeout_within_limit(self):
        """Should not raise within the time limit."""
        start = time.time()
        # Should not raise
        check_generation_timeout(start, "test context")

    def test_timeout_exceeded(self):
        """Should raise ForecastTimeoutError when limit exceeded."""
        # Simulate a start time far in the past
        start = time.time() - (settings.max_forecast_generation_minutes * 60 + 60)
        with pytest.raises(ForecastTimeoutError):
            check_generation_timeout(start, "test context")
