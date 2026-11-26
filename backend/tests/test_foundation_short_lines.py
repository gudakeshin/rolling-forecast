"""E1: new and short lines take Chronos-2's forecast (foundation_short_lines_enabled)."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.config import settings
from app.domain.engines.chronos2 import FoundationPanelContext, QuantileForecast
from app.domain.engines.global_gbm import GlobalPanelContext
from app.domain.engines.model_registry import ModelRegistry
from app.models.line_item import LineItem
from app.services.forecast_pipeline import (
    EffectiveSeriesResult,
    LineForecastContext,
    _short_line_foundation_model,
    forecast_line_item,
)
from app.services.foundation_forecast import build_foundation_batch

HORIZON = 3


def _series(n: int) -> tuple[pd.Series, pd.DatetimeIndex, list[str]]:
    dates = pd.date_range("2025-01-01", periods=n, freq="MS")
    values = pd.Series(100.0 + np.arange(n, dtype=float))
    return values, dates, [d.strftime("%Y-%m") for d in dates]


def _panel(line_id: int, origin: str) -> GlobalPanelContext:
    panel = GlobalPanelContext.empty()
    panel.foundation = FoundationPanelContext(
        status="ok",
        provenance={"model_id": "amazon/chronos-2"},
        forecasts={(line_id, origin): QuantileForecast(
            p10=np.array([90.0, 91.0, 92.0]),
            p50=np.array([110.0, 111.0, 112.0]),
            p90=np.array([130.0, 131.0, 132.0]),
        )},
        production_origin_by_line={line_id: origin},
    )
    return panel


def _ctx(panel, model_type: str = "auto") -> LineForecastContext:
    return LineForecastContext(
        version_id="v", horizon=HORIZON, model_type=model_type,
        model_registry=ModelRegistry(), panel=panel,
    )


def _effective(n: int, forced: str | None = None, sparse: bool = True) -> EffectiveSeriesResult:
    values, dates, _ = _series(n)
    return EffectiveSeriesResult(values=values, dates=dates, forced=forced, is_sparse=sparse)


@pytest.fixture
def short_on(monkeypatch):
    monkeypatch.setattr(settings, "foundation_short_lines_enabled", True)


def test_gate(short_on, monkeypatch):
    panel = _panel(1, "2025-08")
    assert _short_line_foundation_model(_ctx(panel), 1, _effective(8)) is not None
    # A flat-average line keeps its point (it only takes the range blend).
    assert _short_line_foundation_model(_ctx(panel), 1, _effective(4, forced="average")) is None
    # Not short, all-zero, a forced model type, no cached forecast.
    assert _short_line_foundation_model(_ctx(panel), 1, _effective(24, sparse=False)) is None
    assert _short_line_foundation_model(_ctx(panel), 1, _effective(8, forced="zero")) is None
    assert _short_line_foundation_model(_ctx(panel, "linear"), 1, _effective(8)) is None
    assert _short_line_foundation_model(_ctx(panel), 2, _effective(8)) is None
    assert _short_line_foundation_model(_ctx(None), 1, _effective(8)) is None
    monkeypatch.setattr(settings, "foundation_short_lines_enabled", False)
    assert _short_line_foundation_model(_ctx(panel), 1, _effective(8)) is None


def test_short_line_is_forecast_by_chronos2(short_on):
    values, dates, periods = _series(8)
    li = LineItem(id=1, account_code="NEW-1", name="New line", allow_negative=False)
    out = forecast_line_item(None, li, values, dates, periods, _ctx(_panel(1, periods[-1])))  # type: ignore[arg-type]
    assert not out.skipped
    assert out.selected_model == "chronos2"
    assert out.comparison["selection_method"] == "short_history_foundation"
    rows = sorted(out.line_rows, key=lambda r: r["period"])
    assert [r["p50"] for r in rows] == pytest.approx([110.0, 111.0, 112.0])


def test_without_the_setting_a_short_line_keeps_its_old_path():
    values, dates, periods = _series(8)
    li = LineItem(id=1, account_code="NEW-1", name="New line", allow_negative=False)
    out = forecast_line_item(None, li, values, dates, periods, _ctx(_panel(1, periods[-1])))  # type: ignore[arg-type]
    assert out.selected_model != "chronos2"


def test_batch_includes_short_and_average_lines_only_when_on(short_on, monkeypatch):
    lines = [
        LineItem(id=1, account_code="A", name="a"),
        LineItem(id=2, account_code="B", name="b"),
        LineItem(id=3, account_code="C", name="c"),
        LineItem(id=4, account_code="D", name="d"),
    ]
    eff = {
        1: _effective(8),
        2: _effective(4, forced="average"),
        3: _effective(8, forced="zero"),
        4: _effective(2),
    }
    periods = {i: _series(len(e.values))[2] for i, e in eff.items()}
    kwargs = dict(
        line_items=lines, effective_series_by_line=eff, periods_by_line=periods, cal_cfg=None,
        version_id="v", horizon=HORIZON, random_seed=42, registry=ModelRegistry(),
        include_covariates=False,
    )
    series, plan = build_foundation_batch(None, **kwargs)  # type: ignore[arg-type]
    assert sorted(plan) == [1, 2]  # zero-forced and 2-point lines stay out
    assert all(not p["windows"] for p in plan.values())  # production task only, no CV fold
    assert len(series) == 2

    monkeypatch.setattr(settings, "foundation_short_lines_enabled", False)
    series, plan = build_foundation_batch(None, **kwargs)  # type: ignore[arg-type]
    assert plan == {} and series == []


def test_average_line_keeps_its_point_and_takes_the_range_blend(short_on, monkeypatch):
    monkeypatch.setattr(settings, "foundation_range_blend_enabled", True)
    values, dates, periods = _series(4)
    li = LineItem(id=1, account_code="NEW-1", name="New line", allow_negative=False)
    out = forecast_line_item(None, li, values, dates, periods, _ctx(_panel(1, periods[-1])))  # type: ignore[arg-type]
    assert out.selected_model == "average"
    assert "foundation_range_blend" in out.metadata_rows[0].parameters
