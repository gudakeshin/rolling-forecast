"""Andrews' sup-F break test on STL-adjusted data (Phase F)."""

from __future__ import annotations

import numpy as np
import pandas as pd

from app.config import settings
from app.services.error_handlers import HistoryAnalysis, supf_break


def _series(kind: str, rng, n: int = 36) -> np.ndarray:
    t = np.arange(n)
    base = 1000 + 5 * t + 200 * np.sin(2 * np.pi * t / 12)
    noise = rng.normal(0, 50, n)
    if kind == "break":
        return base + np.where(t >= 20, 300, 0) + noise
    return base + noise


def test_finds_a_real_break_where_it_is():
    rng = np.random.default_rng(3)
    hits = [supf_break(_series("break", rng)) for _ in range(40)]
    assert sum(d for d, _ in hits) >= 38
    idx = [i for d, i in hits if d]
    assert abs(np.median(idx) - 20) <= 1


def test_seasonality_alone_is_not_a_break():
    rng = np.random.default_rng(4)
    rate = np.mean([supf_break(_series("none", rng))[0] for _ in range(200)])
    assert rate < 0.12  # nominal 5%


def test_degenerate_inputs():
    assert supf_break(np.ones(36)) == (False, None)
    assert supf_break(np.arange(8.0)) == (False, None)
    assert supf_break(np.array([1.0, np.nan] * 18)) == (False, None)


def test_history_analysis_uses_it_behind_the_setting(monkeypatch):
    rng = np.random.default_rng(5)
    y = pd.Series(_series("none", rng))
    dates = pd.date_range("2023-01-01", periods=36, freq="MS")
    monkeypatch.setattr(settings, "supf_break_test_enabled", True)
    h = HistoryAnalysis(y, dates, "x", min_history_months=12)
    assert h.has_structural_break is False
    yb = pd.Series(_series("break", rng))
    hb = HistoryAnalysis(yb, dates, "x", min_history_months=12)
    assert hb.has_structural_break is True
    assert hb.structural_break_period == "2024-09"  # index 20
