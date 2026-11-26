"""Chronos-2 range blend: the selected model's point, a band averaged with
Chronos-2's P10/P90 (interval_calibration.blend_with_foundation_range)."""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

from app.config import settings
from app.domain.engines.base_model import ForecastOutput
from app.domain.engines.chronos2 import FoundationPanelContext, QuantileForecast
from app.services.forecast_pipeline import _foundation_range_blend
from app.services.interval_calibration import blend_with_foundation_range


def test_quantiles_average_and_point_stays_inside():
    point = np.array([100.0, 100.0, 100.0])
    lower, upper = np.array([95.0, 95.0, 95.0]), np.array([105.0, 105.0, 105.0])
    fm_lo, fm_hi = np.array([80.0, 99.0, 102.0]), np.array([120.0, 101.0, 130.0])
    out = blend_with_foundation_range(point, lower, upper, fm_lo, fm_hi, weight=0.5)
    assert out is not None
    lo, hi, meta = out
    np.testing.assert_allclose(lo, [87.5, 97.0, 98.5])
    np.testing.assert_allclose(hi, [112.5, 103.0, 117.5])
    assert np.all(lo <= point) and np.all(hi >= point)
    assert meta["weight"] == 0.5


def test_point_outside_foundation_band_is_still_covered():
    point = np.array([100.0])
    out = blend_with_foundation_range(
        point, np.array([99.0]), np.array([101.0]), np.array([150.0]), np.array([160.0])
    )
    assert out is not None
    lo, hi, _ = out
    assert lo[0] <= 100.0 <= hi[0]


def test_short_or_bad_foundation_paths():
    point = np.array([10.0, 10.0, 10.0])
    lo, hi = point - 1, point + 1
    padded = blend_with_foundation_range(point, lo, hi, np.array([5.0]), np.array([15.0]))
    assert padded is not None and len(padded[0]) == 3
    assert blend_with_foundation_range(point, lo, hi, np.array([np.nan]), np.array([15.0])) is None
    assert blend_with_foundation_range(point, lo, hi, np.array([]), np.array([])) is None


def test_non_negative_floor():
    out = blend_with_foundation_range(
        np.array([1.0]), np.array([0.5]), np.array([2.0]), np.array([-10.0]), np.array([5.0]),
        non_negative=True,
    )
    assert out is not None and out[0][0] == 0.0


def _panel(line_id: int = 7) -> SimpleNamespace:
    fm = FoundationPanelContext(
        status="ok",
        provenance={"model_id": "amazon/chronos-2", "model_revision": "abc"},
        forecasts={(line_id, "2025-12"): QuantileForecast(
            p10=np.array([80.0, 80.0]), p50=np.array([100.0, 100.0]), p90=np.array([120.0, 120.0])
        )},
        production_origin_by_line={line_id: "2025-12"},
    )
    return SimpleNamespace(foundation=fm)


def _output() -> ForecastOutput:
    return ForecastOutput(
        point_forecast=np.array([100.0, 100.0]),
        lower_bound=np.array([96.0, 96.0]),
        upper_bound=np.array([104.0, 104.0]),
        periods=["2026-01", "2026-02"],
        model_type="ets",
    )


@pytest.fixture
def blend_on(monkeypatch):
    monkeypatch.setattr(settings, "foundation_range_blend_enabled", True)
    monkeypatch.setattr(settings, "foundation_range_blend_weight", 0.5)


def test_pipeline_seam_blends_when_on(blend_on):
    out = _foundation_range_blend(_panel(), 7, "ets", _output(), non_negative=False)
    assert out is not None
    lo, hi, meta = out
    np.testing.assert_allclose(lo, [88.0, 88.0])
    np.testing.assert_allclose(hi, [112.0, 112.0])
    assert meta["model_revision"] == "abc"


def test_pipeline_seam_skips(blend_on, monkeypatch):
    # Chronos-2 selected or in the blend: its band is already in the forecast.
    assert _foundation_range_blend(_panel(), 7, "chronos2", _output(), non_negative=False) is None
    assert _foundation_range_blend(
        _panel(), 7, "ensemble(chronos2+ets)", _output(), non_negative=False
    ) is None
    # Line not in the batch, no context, or service down.
    assert _foundation_range_blend(_panel(), 8, "ets", _output(), non_negative=False) is None
    assert _foundation_range_blend(None, 7, "ets", _output(), non_negative=False) is None
    down = SimpleNamespace(foundation=FoundationPanelContext.unavailable("timeout"))
    assert _foundation_range_blend(down, 7, "ets", _output(), non_negative=False) is None
    # Setting off.
    monkeypatch.setattr(settings, "foundation_range_blend_enabled", False)
    assert _foundation_range_blend(_panel(), 7, "ets", _output(), non_negative=False) is None
