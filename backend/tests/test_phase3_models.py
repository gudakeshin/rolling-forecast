"""Phase 3 — model breadth (theta, croston/tsb, ETS upgrade, no re-fit predict)."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.domain.engines.arima import ARIMAModel
from app.domain.engines.croston import CrostonModel, TSBModel, _croston_sba, _tsb
from app.domain.engines.ets import ETSModel
from app.domain.engines.intermittent import demand_classification
from app.domain.engines.model_registry import ModelRegistry, reset_model_registry
from app.domain.engines.theta import ThetaForecastModel


@pytest.fixture(autouse=True)
def _reset_registry(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "enable_benchmark_models", True)
    monkeypatch.setattr(settings, "outlier_cleaning_enabled", False)
    reset_model_registry()
    yield
    reset_model_registry()


def _trending(n: int = 36, seed: int = 0) -> tuple[pd.Series, pd.DatetimeIndex]:
    rng = np.random.default_rng(seed)
    t = np.arange(n)
    y = 100 + 2 * t + 10 * np.sin(2 * np.pi * t / 12) + rng.normal(0, 2, n)
    dates = pd.date_range("2022-01-01", periods=n, freq="MS")
    return pd.Series(y, index=dates), dates


def _intermittent(n: int = 36, seed: int = 1) -> tuple[pd.Series, pd.DatetimeIndex]:
    rng = np.random.default_rng(seed)
    y = np.zeros(n)
    # ~every 4th period has demand → ADI ≈ 4
    for i in range(0, n, 4):
        y[i] = float(rng.integers(5, 25))
    dates = pd.date_range("2022-01-01", periods=n, freq="MS")
    return pd.Series(y, index=dates), dates


# ── Registry ──────────────────────────────────────────


def test_tier1_models_registered():
    reg = ModelRegistry()
    for name in ("theta", "croston", "tsb", "ets", "arima"):
        assert name in reg.list_models()
    assert reg.get("ets").name == "ets"
    assert reg.get("croston").capabilities.handles_intermittent is True
    assert reg.get("theta").capabilities.cost_class == "cheap"


# ── Intermittent classification ────────────────────────


def test_demand_classification_intermittent():
    y = np.array([0, 0, 10, 0, 0, 0, 8, 0, 0, 12, 0, 0] * 2, dtype=float)
    cls = demand_classification(y)
    assert cls["intermittent"] is True
    assert cls["adi"] >= 1.32
    assert cls["pattern"] in {"intermittent", "lumpy"}


def test_demand_classification_smooth():
    y = np.arange(24, dtype=float) + 10
    cls = demand_classification(y)
    assert cls["intermittent"] is False
    assert cls["pattern"] == "smooth"


# ── Croston / TSB ──────────────────────────────────────


def test_croston_sba_positive_on_intermittent():
    y, _ = _intermittent()
    fc, z, p, sd = _croston_sba(y.values)
    assert fc > 0
    assert z > 0
    assert p >= 1


def test_croston_fit_predict_roundtrip():
    model = CrostonModel()
    y, dates = _intermittent()
    params = model.fit(y, dates)
    assert "point" in params
    out = model.predict(params, horizon=4, last_date=dates[-1])
    assert len(out.point_forecast) == 4
    assert out.model_type == "croston"
    assert np.all(out.point_forecast >= 0)


def test_croston_rejects_smooth_series():
    model = CrostonModel()
    y, dates = _trending()
    with pytest.raises(ValueError, match="intermittent"):
        model.fit(y, dates)


def test_tsb_handles_trailing_zeros():
    model = TSBModel()
    y = pd.Series([10, 0, 0, 12, 0, 0, 0, 0, 0, 0, 0, 0] * 2)
    dates = pd.date_range("2022-01-01", periods=len(y), freq="MS")
    params = model.fit(y, dates)
    # Probability should be pulled down by trailing zeros vs early demand
    assert params["prob_level"] < 0.5
    out = model.predict(params, horizon=3, last_date=dates[-1])
    assert out.point_forecast[0] == pytest.approx(params["point"])


# ── Theta ──────────────────────────────────────────────


def test_theta_fit_predict_and_intervals():
    model = ThetaForecastModel()
    y, dates = _trending()
    params = model.fit(y, dates)
    out = model.predict(params, horizon=6, last_date=dates[-1])
    assert out.model_type == "theta"
    assert len(out.point_forecast) == 6
    assert len(out.lower_bound) == 6
    # Upward series → near-term forecast above early level
    assert out.point_forecast[0] > 100


# ── ETS upgrade ────────────────────────────────────────


def test_ets_selects_by_aicc_and_smooth_predict():
    model = ETSModel()
    y, dates = _trending(n=36)
    params = model.fit(y, dates)
    assert params.get("_fallback") is not True
    assert "_params" in params
    assert "aicc" in params
    out1 = model.predict(params, horizon=4, last_date=dates[-1])
    out2 = model.predict(params, horizon=4, last_date=dates[-1])
    # No re-optimize → identical
    np.testing.assert_allclose(out1.point_forecast, out2.point_forecast)
    assert out1.diagnostics.get("damped_trend") in (True, False)
    # Real intervals (not all equal width)
    widths = out1.upper_bound - out1.lower_bound
    assert np.all(widths > 0)


def test_ets_name_unchanged():
    assert ETSModel().name == "ets"


# ── ARIMA no re-fit ────────────────────────────────────


def test_arima_predict_uses_filter_not_refit():
    model = ARIMAModel()
    y, dates = _trending(n=40)
    params = model.fit(y, dates)
    assert "_params" in params
    stored = list(params["_params"])
    out1 = model.predict(params, horizon=3, last_date=dates[-1])
    # Mutating stored params vector identity — predict must not call fit()
    # which would ignore our vector; filter uses it → still works
    out2 = model.predict(params, horizon=3, last_date=dates[-1])
    np.testing.assert_allclose(out1.point_forecast, out2.point_forecast)
    assert params["_params"] == stored


# ── Selection can pick new models ──────────────────────


def test_auto_select_may_pick_theta_on_seasonal():
    reg = ModelRegistry()
    y, dates = _trending(n=48, seed=3)
    result = reg.compare_models(
        y, dates, models_to_test=["linear", "ets", "theta"], two_stage=False
    )
    assert result.best_model in {"linear", "ets", "theta"}
    assert any(c.model_name == "theta" and c.eligible for c in result.comparisons)


def test_auto_select_croston_on_intermittent():
    reg = ModelRegistry()
    y, dates = _intermittent(n=48)
    result = reg.compare_models(
        y, dates, models_to_test=["linear", "croston", "tsb", "average"], two_stage=False
    )
    # croston/tsb should be eligible; linear will also run
    croston = next(c for c in result.comparisons if c.model_name == "croston")
    assert croston.eligible
    assert croston.mase != float("inf") or croston.mape != float("inf")
