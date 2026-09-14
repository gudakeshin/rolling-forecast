"""Pure unit tests for the global panel model's engine code (no DB).

DB-aware panel construction (app.services.panel_forecast) is covered
separately in test_panel_forecast.py.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.domain.engines.global_gbm import (
    GlobalGBMModel,
    GlobalPanelContext,
    fiscal_features,
    horizon_feature,
    lag_rolling_features,
)


class _FakeQuantileModel:
    """Stand-in for a fitted HistGradientBoostingRegressor.predict()."""

    def __init__(self, values: np.ndarray):
        self._values = values

    def predict(self, X: pd.DataFrame) -> np.ndarray:
        return self._values[: len(X)]


def test_lag_rolling_features_shape_and_values():
    values = np.arange(1, 15, dtype=float)  # 1..14
    feats = lag_rolling_features(values, origin_idx=13)  # last index -> value 14
    assert feats["lag_1"] == 14.0
    assert feats["lag_2"] == 13.0
    assert feats["lag_3"] == 12.0
    assert feats["lag_12"] == 3.0
    assert feats["roll_mean_3"] == pytest.approx(13.0)  # 12,13,14
    assert feats["roll_std_3"] > 0


def test_lag_rolling_features_short_history_is_nan_not_error():
    values = np.array([5.0, 6.0])
    feats = lag_rolling_features(values, origin_idx=1)
    assert np.isnan(feats["lag_3"])
    assert np.isnan(feats["lag_12"])
    assert feats["lag_1"] == 6.0
    # window=3 but only 2 points exist -> std computed over the 2 available
    assert feats["roll_std_3"] == pytest.approx(0.5)


def test_fiscal_features_gregorian_month():
    feats = fiscal_features("2024-03")
    assert feats["period_position"] == 3.0


def test_fiscal_features_445_period():
    feats = fiscal_features("FY2026-P01")
    assert feats["period_position"] == 1.0
    assert feats["period_sin"] == pytest.approx(0.0, abs=1e-9)
    assert feats["period_cos"] == pytest.approx(1.0, abs=1e-9)


def test_fiscal_features_cyclical_adjacency():
    """Period 12 and period 1 should be close in sin/cos space (seasonally
    adjacent), unlike their raw ordinal distance of 11."""
    p12 = fiscal_features("2024-12")
    p1 = fiscal_features("2025-01")
    dist = np.hypot(p12["period_sin"] - p1["period_sin"], p12["period_cos"] - p1["period_cos"])
    assert dist < 0.6  # much closer than e.g. period 1 vs period 6/7


def test_horizon_feature():
    assert horizon_feature(3) == {"h": 3.0}


def _make_panel(line_item_id: int, horizon: int, feature_columns: list[str]) -> GlobalPanelContext:
    future = pd.DataFrame(
        [{c: 0.0 for c in feature_columns} for _ in range(horizon)]
    )
    return GlobalPanelContext(
        feature_columns=feature_columns,
        production_models={
            0.10: _FakeQuantileModel(np.full(horizon, 90.0)),
            0.50: _FakeQuantileModel(np.full(horizon, 100.0)),
            0.90: _FakeQuantileModel(np.full(horizon, 110.0)),
        },
        predict_features_by_line={line_item_id: future},
        per_line_cv_results={},
        fitted_at="2026-01-01T00:00:00+00:00",
        n_training_rows=100,
        n_lines=5,
    )


def test_fit_raises_without_panel():
    model = GlobalGBMModel()
    series = pd.Series(np.arange(12, dtype=float))
    dates = pd.DatetimeIndex(pd.date_range("2024-01", periods=12, freq="MS"))
    with pytest.raises(RuntimeError, match="GlobalPanelContext"):
        model.fit(series, dates)


def test_fit_raises_for_unknown_line():
    model = GlobalGBMModel()
    series = pd.Series(np.arange(12, dtype=float))
    dates = pd.DatetimeIndex(pd.date_range("2024-01", periods=12, freq="MS"))
    panel = _make_panel(line_item_id=1, horizon=3, feature_columns=["lag_1"])
    with pytest.raises(RuntimeError):
        model.fit(series, dates, panel=panel, line_item_id=999)


def test_fit_predict_round_trip():
    model = GlobalGBMModel()
    series = pd.Series(np.arange(12, dtype=float))
    dates = pd.DatetimeIndex(pd.date_range("2024-01", periods=12, freq="MS"))
    panel = _make_panel(line_item_id=1, horizon=3, feature_columns=["lag_1"])

    params = model.fit(series, dates, panel=panel, line_item_id=1)
    assert params["line_item_id"] == 1

    out = model.predict(params, horizon=3, last_date=dates[-1], panel=panel)
    assert len(out.point_forecast) == 3
    assert len(out.lower_bound) == 3
    assert len(out.upper_bound) == 3
    assert out.model_type == "global_gbm"
    np.testing.assert_allclose(out.point_forecast, 100.0)
    np.testing.assert_allclose(out.lower_bound, 90.0)
    np.testing.assert_allclose(out.upper_bound, 110.0)


def test_predict_enforces_monotonicity_on_crossed_quantiles():
    """Independently-trained quantile models are not guaranteed monotonic —
    predict() must sort per row so lower <= point <= upper always holds."""
    model = GlobalGBMModel()
    dates = pd.DatetimeIndex(pd.date_range("2024-01", periods=1, freq="MS"))
    feature_columns = ["lag_1"]
    future = pd.DataFrame([{"lag_1": 0.0}])
    # Deliberately crossed: "p90" model predicts LOWER than "p10" model.
    panel = GlobalPanelContext(
        feature_columns=feature_columns,
        production_models={
            0.10: _FakeQuantileModel(np.array([120.0])),
            0.50: _FakeQuantileModel(np.array([100.0])),
            0.90: _FakeQuantileModel(np.array([80.0])),
        },
        predict_features_by_line={1: future},
        per_line_cv_results={},
        fitted_at="2026-01-01T00:00:00+00:00",
    )
    out = model.predict({"line_item_id": 1}, horizon=1, last_date=dates[-1], panel=panel)
    assert out.lower_bound[0] <= out.point_forecast[0] <= out.upper_bound[0]
    # The three raw predictions {80, 100, 120} sorted -> 80, 100, 120
    assert out.lower_bound[0] == 80.0
    assert out.point_forecast[0] == 100.0
    assert out.upper_bound[0] == 120.0


def test_evaluate_cv_panel_returns_precomputed_result():
    model = GlobalGBMModel()
    series = pd.Series(np.arange(12, dtype=float))
    dates = pd.DatetimeIndex(pd.date_range("2024-01", periods=12, freq="MS"))
    cv_result = {
        "mean_mape": 5.0, "mean_smape": 5.0, "mean_mase": 0.4,
        "mean_pinball_10": 1.0, "mean_pinball_90": 1.0, "mean_pinball": 1.0,
        "coverage_80": 0.8, "fold_residuals": {1: [0.1]}, "n": 3,
        "fold_mapes": [5.0], "fold_mases": [0.4], "n_folds_used": 1,
        "mase_scale_method": "seasonal_naive",
    }
    panel = GlobalPanelContext(
        feature_columns=[], production_models={}, predict_features_by_line={},
        per_line_cv_results={1: cv_result}, fitted_at="2026-01-01T00:00:00+00:00",
    )
    result = model.evaluate_cv_panel(1, series, dates, panel)
    assert result is cv_result

    # Ignoring n_folds/fold_horizon args is intentional (see docstring).
    result_ignored_args = model.evaluate_cv_panel(1, series, dates, panel, n_folds=99, fold_horizon=99)
    assert result_ignored_args is cv_result

    missing = model.evaluate_cv_panel(2, series, dates, panel)
    assert missing["mase_scale_method"] == "ineligible"
    assert missing["mean_mase"] == float("inf")


def test_bare_evaluate_cv_degrades_gracefully_without_panel():
    """A stray models_to_test=['global_gbm'] without panel plumbing must
    degrade to an ineligible-looking result, never crash the CV loop."""
    model = GlobalGBMModel()
    series = pd.Series(np.arange(24, dtype=float) + 100.0)
    dates = pd.DatetimeIndex(pd.date_range("2023-01", periods=24, freq="MS"))
    result = model.evaluate_cv(series, dates, n_folds=3, fold_horizon=3)
    assert result["mean_mase"] == float("inf")
    assert result["n_folds_used"] == 0
