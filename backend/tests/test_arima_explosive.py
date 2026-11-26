"""ARIMA never ships a fit with explosive AR roots.

From the Phase D backtest (ENHANCEMENT_PLAN_VERIFICATION.md): on 30 points of
seasonal history the order search, which fits with enforce_stationarity=False,
picked ARIMA(2,1,1)x(1,1,1,12) by AIC and forecast 2,833 -> -47,821 in six
months for a series that stays between 2,000 and 3,300.
"""

from __future__ import annotations

import warnings

import numpy as np
import pandas as pd

from app.domain.engines.arima import ARIMAModel, _explosive


def _seasonal(n: int = 30, seed: int = 9) -> pd.Series:
    rng = np.random.default_rng(seed)
    t = np.arange(n)
    base = 2700.0
    return pd.Series(base + 0.2 * base * np.sin(2 * np.pi * t / 12 + 1.3) + rng.normal(0, base * 0.03, n))


class _Fit:
    def __init__(self, roots):
        self.arroots = np.asarray(roots)


def test_unit_and_inside_roots_count_as_explosive():
    assert _explosive(_Fit([0.6, 2.0]))
    assert _explosive(_Fit([1.0]))
    assert not _explosive(_Fit([1.5, -3.0 + 1j]))
    assert not _explosive(_Fit([]))  # pure MA / no AR part


def test_short_seasonal_forecast_stays_in_range():
    for seed in range(6):
        y = _seasonal(seed=seed)
        dates = pd.date_range("2023-01-01", periods=len(y), freq="MS")
        model = ARIMAModel()
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            params = model.fit(y, dates)
            out = model.predict(params, 6, dates[-1])
        lo, hi = float(y.min()), float(y.max())
        span = hi - lo
        assert np.all(out.point_forecast > lo - span), (seed, params.get("order"), out.point_forecast)
        assert np.all(out.point_forecast < hi + span), (seed, params.get("order"), out.point_forecast)
