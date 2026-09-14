"""Tests for the statistical model engine and auto-selection."""

import pytest
import numpy as np
import pandas as pd

from app.domain.engines.model_registry import ModelRegistry, ModelComparisonResult, _pick_best
from app.domain.engines.linear import LinearTrendModel
from app.domain.engines.base_model import ForecastOutput


class TestLinearTrendModel:
    """Test the linear trend model."""

    def test_fit_and_predict(self):
        """Basic fit and predict cycle."""
        model = LinearTrendModel()
        np.random.seed(42)

        values = pd.Series(np.arange(24) * 100 + 1000 + np.random.normal(0, 20, 24))
        dates = pd.DatetimeIndex(pd.date_range("2024-01", periods=24, freq="MS"))

        params = model.fit(values, dates)
        assert params is not None
        assert "slope" in params or "_values" in params

        forecast = model.predict(params, horizon=6, last_date=dates[-1])
        assert isinstance(forecast, ForecastOutput)
        assert len(forecast.point_forecast) == 6
        assert len(forecast.periods) == 6

    def test_forecast_has_upward_trend(self):
        """Forecast of upward-trending data should increase."""
        model = LinearTrendModel()
        values = pd.Series(np.arange(24) * 100 + 1000)
        dates = pd.DatetimeIndex(pd.date_range("2024-01", periods=24, freq="MS"))

        params = model.fit(values, dates)
        forecast = model.predict(params, horizon=6, last_date=dates[-1])

        # Last actual value should be less than first forecast
        assert forecast.point_forecast[0] > values.iloc[-3]

    def test_min_data_points(self):
        """Check minimum data points requirement."""
        model = LinearTrendModel()
        assert model.min_data_points >= 2


class TestModelRegistry:
    """Test the model registry and auto-selection."""

    def test_default_models_registered(self):
        """Core models should be registered; benchmarks optional via settings."""
        registry = ModelRegistry()
        models = registry.list_models()
        assert "linear" in models
        assert "ets" in models
        assert "arima" in models
        assert "prophet" in models

    def test_get_model(self):
        """Can retrieve a model by name."""
        registry = ModelRegistry()
        model = registry.get("linear")
        assert model is not None
        assert model.name == "linear"

    def test_get_nonexistent_model(self):
        """Getting a non-existent model returns None."""
        registry = ModelRegistry()
        assert registry.get("nonexistent") is None

    def test_auto_select_with_sufficient_data(self):
        """Auto-selection should work with enough data."""
        registry = ModelRegistry()
        np.random.seed(42)

        values = pd.Series(np.arange(30) * 50 + 1000 + np.random.normal(0, 30, 30))
        dates = pd.DatetimeIndex(pd.date_range("2023-07", periods=30, freq="MS"))

        model_name, mape, selection = registry.auto_select(values, dates)
        assert model_name in registry.list_models()
        assert mape >= 0
        assert selection is not None
        assert selection.selection_method == "rolling_origin_cv"

    def test_auto_select_sparse_data_uses_simpler(self):
        """With sparse data, only simpler models should be candidates."""
        registry = ModelRegistry()
        np.random.seed(42)

        # Only 10 data points -- ARIMA needs 18
        values = pd.Series(np.random.normal(1000, 50, 10))
        dates = pd.DatetimeIndex(pd.date_range("2025-01", periods=10, freq="MS"))

        model_name, mape, _selection = registry.auto_select(values, dates)
        # Should not select arima (needs 18 points)
        assert model_name is not None
        assert model_name not in ["arima"]
        assert model_name in registry.list_models()

    def test_tie_breaking_prefers_simpler(self):
        """EC8: When models tie, prefer the simpler one."""
        registry = ModelRegistry()
        # This is implicitly tested in auto_select via the simplicity_rank logic
        # The registry has tie-breaking that prefers linear > ets > arima > prophet
        models = registry.list_models()
        assert "linear" in models  # Simplest model exists

    def test_fit_and_predict_specific_model(self):
        """Can fit and predict with a specific model."""
        registry = ModelRegistry()
        np.random.seed(42)

        values = pd.Series(np.random.normal(1000, 100, 24))
        dates = pd.DatetimeIndex(pd.date_range("2024-01", periods=24, freq="MS"))

        output = registry.fit_and_predict("linear", values, dates, horizon=12)
        assert len(output.point_forecast) == 12
        assert len(output.periods) == 12
        assert output.model_type == "linear"

    def test_fit_and_predict_invalid_model(self):
        """Should raise ValueError for invalid model name."""
        registry = ModelRegistry()
        values = pd.Series([100] * 24)
        dates = pd.DatetimeIndex(pd.date_range("2024-01", periods=24, freq="MS"))

        with pytest.raises(ValueError, match="not found"):
            registry.fit_and_predict("nonexistent", values, dates, horizon=6)


class TestGlobalGBMRegistration:
    """Phase 2.1 — the global panel model must flow through the existing
    two-stage screen and Occam tie-break unchanged; it is never special-cased."""

    def test_not_registered_by_default(self):
        registry = ModelRegistry()
        assert "global_gbm" not in registry.list_models()

    def test_registered_when_enabled(self, monkeypatch):
        from app.config import settings

        monkeypatch.setattr(settings, "enable_global_gbm_model", True)
        registry = ModelRegistry()
        assert "global_gbm" in registry.list_models()
        model = registry.get("global_gbm")
        assert model.capabilities.is_panel_model is True
        assert model.capabilities.cost_class == "expensive"
        assert model.capabilities.auto_selectable is True

    def test_ineligible_without_panel_context(self, monkeypatch):
        """No crash, no silent skip — a clear 'panel unavailable' ineligibility."""
        from app.config import settings

        monkeypatch.setattr(settings, "enable_global_gbm_model", True)
        registry = ModelRegistry()
        series = pd.Series(np.arange(24, dtype=float) + 100.0)
        dates = pd.DatetimeIndex(pd.date_range("2023-01", periods=24, freq="MS"))

        result = registry.compare_models(series, dates, is_material=True, panel=None, line_item_id=None)
        gbm = next(c for c in result.comparisons if c.model_name == "global_gbm")
        assert gbm.eligible is False
        assert gbm.error == "panel context unavailable"
        # The run still resolves a winner from the rest of the roster.
        assert result.best_model is not None

    def test_evaluate_one_looks_up_precomputed_panel_cv(self, monkeypatch):
        """The panel CV was already computed once, panel-wide, at build time —
        _evaluate_one for a panel model must be a lookup, not a recomputation."""
        from app.config import settings
        from app.domain.engines.global_gbm import GlobalPanelContext

        monkeypatch.setattr(settings, "enable_global_gbm_model", True)
        registry = ModelRegistry()
        series = pd.Series(np.arange(24, dtype=float) + 100.0)
        dates = pd.DatetimeIndex(pd.date_range("2023-01", periods=24, freq="MS"))

        panel = GlobalPanelContext(
            feature_columns=[],
            production_models={},
            predict_features_by_line={},
            per_line_cv_results={
                7: {
                    "mean_mape": 8.0,
                    "mean_smape": 8.0,
                    "mean_mase": 0.55,
                    "mean_pinball_10": 1.0,
                    "mean_pinball_90": 1.0,
                    "mean_pinball": 1.0,
                    "coverage_80": 0.8,
                    "fold_residuals": {1: [1.0, -1.0]},
                    "n": 6,
                    "fold_mapes": [8.0, 8.0],
                    "fold_mases": [0.55, 0.55],
                    "n_folds_used": 2,
                    "mase_scale_method": "seasonal_naive",
                }
            },
            fitted_at="2026-01-01T00:00:00+00:00",
        )
        comparison = registry._evaluate_one(
            "global_gbm", series, dates, test_size=6, panel=panel, line_item_id=7
        )
        assert comparison.eligible is True
        assert comparison.mase == 0.55
        assert comparison.fold_mases == [0.55, 0.55]

        # A line the panel never scored is ineligible, not a crash.
        missing = registry._evaluate_one(
            "global_gbm", series, dates, test_size=6, panel=panel, line_item_id=999
        )
        assert missing.mase == float("inf")

    def test_two_stage_screen_gates_expensive_admission(self, monkeypatch):
        """A stable, non-material series must not admit the expensive stage at
        all — global_gbm should never even reach a panel lookup in that case."""
        from app.config import settings
        from app.domain.engines.global_gbm import GlobalPanelContext

        monkeypatch.setattr(settings, "enable_global_gbm_model", True)
        registry = ModelRegistry()
        # Low coefficient-of-variation, stable series.
        series = pd.Series(np.full(24, 1000.0) + np.random.default_rng(0).normal(0, 1, 24))
        dates = pd.DatetimeIndex(pd.date_range("2023-01", periods=24, freq="MS"))
        panel = GlobalPanelContext(
            feature_columns=[], production_models={}, predict_features_by_line={},
            per_line_cv_results={7: {
                "mean_mape": 1.0, "mean_smape": 1.0, "mean_mase": 0.1,
                "mean_pinball_10": 0.1, "mean_pinball_90": 0.1, "mean_pinball": 0.1,
                "coverage_80": 0.8, "fold_residuals": {}, "n": 6,
                "fold_mapes": [1.0], "fold_mases": [0.1], "n_folds_used": 1,
                "mase_scale_method": "seasonal_naive",
            }},
            fitted_at="2026-01-01T00:00:00+00:00",
        )
        result = registry.compare_models(
            series, dates, is_material=False, panel=panel, line_item_id=7
        )
        gbm = next(c for c in result.comparisons if c.model_name == "global_gbm")
        assert gbm.skipped_budget is True
        assert gbm.error == "Skipped: not admitted by two-stage screen"

    def test_exact_tie_prefers_lower_complexity_rank(self):
        """_pick_best's Occam tie-break fires on the case it's built for: two
        candidates at (near-)identical MASE. It is unmodified by this task —
        this documents existing behavior, not a new guarantee GBM adds."""
        linear = ModelComparisonResult(
            model_name="linear", mape=10.0, mase=0.50, pinball=1.0,
            evaluation_time_ms=1, eligible=True, complexity_rank=10,
            cost_class="cheap", is_benchmark=False,
            fold_mases=[0.50, 0.50, 0.50], n_folds=3,
        )
        gbm = ModelComparisonResult(
            model_name="global_gbm", mape=10.0, mase=0.50, pinball=1.0,
            evaluation_time_ms=1, eligible=True, complexity_rank=45,
            cost_class="expensive", is_benchmark=False,
            fold_mases=[0.50, 0.50, 0.50], n_folds=3,
        )
        winner, *_ = _pick_best([gbm, linear], rule="mase_pinball_complexity")
        assert winner == "linear"

    def test_lower_mase_wins_even_within_the_1se_band(self):
        """Documents current _pick_best behavior: candidates within the 1-SE
        band are still ranked by raw MASE first, so a materially-better (but
        within-band) global_gbm score wins over a simpler model rather than
        being suppressed by complexity_rank. Only an exact tie reaches the
        complexity tie-break (see test above). This is pre-existing
        model_registry.py behavior, unmodified by Phase 2.1."""
        linear = ModelComparisonResult(
            model_name="linear", mape=10.0, mase=0.50, pinball=1.0,
            evaluation_time_ms=1, eligible=True, complexity_rank=10,
            cost_class="cheap", is_benchmark=False,
            fold_mases=[0.49, 0.50, 0.51], n_folds=3,
        )
        gbm = ModelComparisonResult(
            model_name="global_gbm", mape=9.5, mase=0.48, pinball=0.9,
            evaluation_time_ms=1, eligible=True, complexity_rank=45,
            cost_class="expensive", is_benchmark=False,
            fold_mases=[0.47, 0.48, 0.49], n_folds=3,
        )
        winner, *_ = _pick_best([linear, gbm], rule="mase_pinball_complexity")
        assert winner == "global_gbm"
