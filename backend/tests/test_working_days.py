"""Working-day (trading-day) effects."""

from __future__ import annotations

import dataclasses

import numpy as np
import pandas as pd
import pytest

from app.config import settings
from app.domain.engines.model_registry import ModelRegistry
from app.models.line_item import LineItem
from app.services.period_calendar import CalendarType, FiscalCalendarConfig
from app.services.working_days import estimate_effect, working_days

GREG = FiscalCalendarConfig()


def _months(n: int, start: str = "2021-01") -> list[str]:
    return [str(p) for p in pd.period_range(start, periods=n, freq="M")]


def test_counts_weekdays_and_holidays():
    # January 2025: 23 weekdays; US holidays on weekdays: Jan 1, Jan 20 (MLK).
    assert working_days(["2025-01"], GREG)[0] == 23
    assert working_days(["2025-01"], GREG, "US")[0] == 21
    # February 2024 (leap year) vs 2025.
    assert working_days(["2024-02", "2025-02"], GREG).tolist() == [21, 20]
    # An unknown country code degrades to weekdays only.
    assert working_days(["2025-01"], GREG, "XX")[0] == 23


def test_445_periods_are_whole_weeks():
    cal = FiscalCalendarConfig(calendar_type=CalendarType.FISCAL_445)
    from app.services.period_calendar import forecast_horizon_periods

    periods = forecast_horizon_periods("FY2025-P12", 12, cal)
    wd = working_days(periods, cal)
    # 4 or 5 weeks of five weekdays; a 53-week year gives its last period 6.
    assert set(wd.tolist()) <= {20.0, 25.0, 30.0}


def _effect_series(n: int, beta: float, rng) -> tuple[np.ndarray, np.ndarray]:
    periods = _months(n)
    wd = working_days(periods, GREG)
    t = np.arange(n)
    season = 1 + 0.2 * np.sin(2 * np.pi * t / 12)
    base = 1000 * (1.01**t) * season
    y = base * (wd / wd.mean()) ** beta * np.exp(rng.normal(0, 0.005, n))
    return y, wd


def test_detects_a_real_effect_and_ignores_none():
    rng = np.random.default_rng(0)
    y, wd = _effect_series(48, 1.0, rng)
    eff = estimate_effect(y, wd)
    assert eff is not None and abs(eff.beta - 1.0) < 0.2 and eff.t_stat > 2
    y0, wd0 = _effect_series(48, 0.0, rng)
    assert estimate_effect(y0, wd0) is None
    # Too short, non-positive, or no variation in the count: no verdict.
    assert estimate_effect(y[:20], wd[:20]) is None
    assert estimate_effect(-y, wd) is None
    assert estimate_effect(y, np.full_like(wd, 21.0)) is None


def test_pipeline_forecasts_per_working_day_and_scales_back(monkeypatch):
    from app.services.forecast_pipeline import LineForecastContext, forecast_line_item

    rng = np.random.default_rng(1)
    n = 48
    y, wd = _effect_series(n, 1.0, rng)
    periods = _months(n)
    dates = pd.DatetimeIndex([pd.Timestamp(f"{p}-01") for p in periods])
    li = LineItem(id=1, account_code="WD-1", name="Billable revenue", allow_negative=False)

    monkeypatch.setattr(settings, "foundation_range_blend_enabled", False)
    from app.services import analysis_profile

    fields = {name: getattr(settings, name) for name in analysis_profile.ANALYSIS_PROFILE_FIELDS}
    on = analysis_profile.AnalysisSettings(**{**fields, "working_day_adjustment_enabled": True})
    off = dataclasses.replace(on, working_day_adjustment_enabled=False)

    def run(analysis):
        ctx = LineForecastContext(
            version_id="v", horizon=12, model_type="ets", model_registry=ModelRegistry(), analysis=analysis
        )
        return forecast_line_item(None, li, pd.Series(y), dates, periods, ctx)  # type: ignore[arg-type]

    out_on = run(on)
    meta = out_on.metadata_rows[0].parameters["working_day_adjustment"]
    assert abs(meta["beta"] - 1.0) < 0.2
    future_wd = working_days([r["period"] for r in sorted(out_on.line_rows, key=lambda r: r["period"])], GREG)
    assert meta["factors"] == pytest.approx((future_wd / meta["mean_working_days"]) ** meta["beta"], rel=1e-3)

    out_off = run(off)
    assert "working_day_adjustment" not in out_off.metadata_rows[0].parameters
    # The adjusted forecast differs from the plain one by the working-day pattern.
    def p50(out):
        return np.array([r["p50"] for r in sorted(out.line_rows, key=lambda r: r["period"])])

    ratio = p50(out_on) / p50(out_off)
    assert np.corrcoef(ratio, np.array(meta["factors"]))[0, 1] > 0.8


def test_holiday_country_is_validated():
    from app.services.analysis_profile import validate_analysis_patch

    assert validate_analysis_patch({"holiday_country": "in"}) == {"holiday_country": "IN"}
    assert validate_analysis_patch({"holiday_country": ""}) == {"holiday_country": ""}
    with pytest.raises(ValueError):
        validate_analysis_patch({"holiday_country": "Narnia"})
