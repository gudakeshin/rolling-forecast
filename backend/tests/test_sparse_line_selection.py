"""Short-history lines: linear vs the global panel model instead of forced linear."""

from __future__ import annotations

import numpy as np
import pandas as pd

from app.domain.engines.global_gbm import GlobalPanelContext
from app.domain.engines.model_registry import get_model_registry
from app.models.line_item import LineItem
from app.services.period_calendar import get_calendar_config
from app.services.forecast_pipeline import LineForecastContext, forecast_line_item

LINE_ID = 4242
HORIZON = 6
N_POINTS = 9  # >= 6 (not "very sparse") and < min_history_months (12): sparse


class _FakeQuantileModel:
    def __init__(self, values: np.ndarray):
        self._values = values

    def predict(self, X: pd.DataFrame) -> np.ndarray:
        return self._values[: len(X)]


def _cv(mase: float) -> dict:
    return {
        "mean_mape": 1.0, "mean_smape": 1.0, "mean_mase": mase,
        "mean_pinball_10": 0.1, "mean_pinball_90": 0.1, "mean_pinball": 0.1,
        "coverage_80": 0.8, "fold_residuals": {1: [0.5, -0.5]}, "n": 6,
        "fold_mapes": [1.0, 1.0], "fold_mases": [mase, mase], "n_folds_used": 2,
        "mase_scale_method": "seasonal_naive",
    }


def _panel(*, cv_mase: float | None = 0.01, line_id: int = LINE_ID) -> GlobalPanelContext:
    return GlobalPanelContext(
        feature_columns=["f"],
        production_models={
            0.10: _FakeQuantileModel(np.full(HORIZON, 90.0)),
            0.50: _FakeQuantileModel(np.full(HORIZON, 100.0)),
            0.90: _FakeQuantileModel(np.full(HORIZON, 110.0)),
        },
        predict_features_by_line={line_id: pd.DataFrame([{"f": 0.0}] * HORIZON)},
        per_line_cv_results={} if cv_mase is None else {line_id: _cv(cv_mase)},
        fitted_at="2026-01-01T00:00:00+00:00",
    )


def _run(panel, *, models_to_test=None, gbm_enabled=True):
    li = LineItem(account_code="SPARSE-1", name="Short history", category="Revenue")
    li.id = LINE_ID
    rng = np.random.default_rng(7)
    values = pd.Series(100.0 + rng.normal(0, 15, N_POINTS))
    periods = [f"2025-{i + 1:02d}" for i in range(N_POINTS)]
    dates = pd.DatetimeIndex([pd.Timestamp(f"{p}-01") for p in periods])
    ctx = LineForecastContext(
        version_id="sparse-v1",
        horizon=HORIZON,
        model_type="auto",
        models_to_test=models_to_test,
        selection_rule="mase_pinball_complexity",
        is_material=True,
        cal_cfg=get_calendar_config(),
        model_registry=get_model_registry(
            enable_benchmark_models=True, enable_global_gbm_model=gbm_enabled
        ),
        panel=panel,
    )
    return forecast_line_item(None, li, values, dates, periods, ctx)  # type: ignore[arg-type]


def _compared(out) -> set[str]:
    return {c["model_name"] for c in (out.comparison or {}).get("comparisons", [])}


def test_short_line_can_select_the_global_model():
    out = _run(_panel(cv_mase=0.01))
    assert not out.skipped
    assert _compared(out) == {"linear", "global_gbm"}
    assert out.selected_model == "global_gbm"


def test_short_line_without_a_panel_stays_linear():
    out = _run(None)
    assert out.selected_model == "linear"
    assert "global_gbm" not in _compared(out)


def test_short_line_stays_linear_when_gbm_is_disabled_for_the_company():
    out = _run(_panel(), gbm_enabled=False)
    assert out.selected_model == "linear"


def test_short_line_respects_models_to_test():
    out = _run(_panel(), models_to_test=["linear", "ets"])
    assert out.selected_model == "linear"
    assert "global_gbm" not in _compared(out)


def test_short_line_not_scored_by_the_panel_stays_linear():
    out = _run(_panel(cv_mase=None))
    assert out.selected_model == "linear"


def test_short_line_falls_back_to_linear_when_nothing_scores(monkeypatch):
    from app.domain.engines.model_registry import ModelRegistry

    def no_winner(self, *args, **kwargs):
        result = original(self, *args, **kwargs)
        result.best_model = None
        return result

    original = ModelRegistry.compare_models
    monkeypatch.setattr(ModelRegistry, "compare_models", no_winner)
    out = _run(_panel())
    assert not out.skipped
    assert out.selected_model == "linear"
