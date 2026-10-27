"""Unit tests for services/quantile_aggregation.py."""

import math

import numpy as np
import pytest

from app.services.quantile_aggregation import (
    Z80,
    aggregate_quantiles,
    ar1_correlation,
)


def _lines(n: int, p50: float = 100.0, half: float = 10.0):
    p50s = np.full(n, p50)
    return p50s - half, p50s, p50s + half


def test_independent_lines_band_narrower_than_sum():
    p10, p50, p90 = _lines(4)
    agg = aggregate_quantiles(p10, p50, p90)  # identity correlation
    assert agg.p50 == pytest.approx(400.0)
    # sqrt(4) * 10 rather than 4 * 10
    assert agg.downside == pytest.approx(20.0)
    assert agg.upside == pytest.approx(20.0)
    assert agg.downside < (p50 - p10).sum()


def test_perfectly_correlated_lines_equal_sum():
    p10, p50, p90 = _lines(4)
    agg = aggregate_quantiles(p10, p50, p90, line_corr=np.ones((4, 4)))
    assert agg.p10 == pytest.approx(p10.sum())
    assert agg.p90 == pytest.approx(p90.sum())


def test_perfectly_correlated_grid_equals_sum_over_lines_and_periods():
    p50 = np.array([[100.0, 110.0, 120.0], [50.0, 55.0, 60.0]])
    p10 = p50 * 0.9
    p90 = p50 * 1.2  # skewed band
    agg = aggregate_quantiles(
        p10, p50, p90, line_corr=np.ones((2, 2)), period_corr=np.ones((3, 3))
    )
    assert agg.p10 == pytest.approx(p10.sum())
    assert agg.p50 == pytest.approx(p50.sum())
    assert agg.p90 == pytest.approx(p90.sum())


def test_skew_preserved():
    agg = aggregate_quantiles(np.array([95.0]), np.array([100.0]), np.array([130.0]))
    assert agg.downside == pytest.approx(5.0)
    assert agg.upside == pytest.approx(30.0)


def test_mixed_revenue_and_cost_net_income_basis():
    # Revenue 1000 (P10 900, P90 1050), cost 600 (P10 580, P90 700).
    p10 = np.array([900.0, 580.0])
    p50 = np.array([1000.0, 600.0])
    p90 = np.array([1050.0, 700.0])
    signs = np.array([1.0, -1.0])

    indep = aggregate_quantiles(p10, p50, p90, signs=signs)
    assert indep.p50 == pytest.approx(400.0)
    # Net-income downside = revenue shortfall (100) ⊕ cost overrun (100)
    assert indep.downside == pytest.approx(math.hypot(100.0, 100.0))
    # Net-income upside = revenue beat (50) ⊕ cost saving (20)
    assert indep.upside == pytest.approx(math.hypot(50.0, 20.0))

    # Revenue and cost moving together offsets on a net-income basis.
    corr = np.array([[1.0, 0.8], [0.8, 1.0]])
    hedged = aggregate_quantiles(p10, p50, p90, signs=signs, line_corr=corr)
    assert hedged.downside < indep.downside
    expected = math.sqrt(100.0**2 + 100.0**2 - 2 * 0.8 * 100.0 * 100.0)
    assert hedged.downside == pytest.approx(expected)


def test_period_correlation_widens_between_independent_and_full():
    p50 = np.full((1, 12), 100.0)
    p10, p90 = p50 - 10.0, p50 + 10.0
    indep = aggregate_quantiles(p10, p50, p90, period_corr=np.eye(12))
    default = aggregate_quantiles(p10, p50, p90)
    full = aggregate_quantiles(p10, p50, p90, period_corr=np.ones((12, 12)))
    assert indep.downside < default.downside < full.downside
    assert full.downside == pytest.approx(120.0)


def test_missing_bounds_and_cells():
    p10 = np.array([[np.nan, 90.0]])
    p50 = np.array([[100.0, np.nan]])
    p90 = np.array([[np.nan, 110.0]])
    agg = aggregate_quantiles(p10, p50, p90)
    assert agg.p50 == pytest.approx(100.0)
    assert agg.downside == 0.0 and agg.upside == 0.0


def test_empty():
    agg = aggregate_quantiles(np.zeros((0, 0)), np.zeros((0, 0)), np.zeros((0, 0)))
    assert (agg.p10, agg.p50, agg.p90) == (0.0, 0.0, 0.0)


def test_ar1_correlation():
    R = ar1_correlation(3, 0.5)
    assert R[0, 2] == pytest.approx(0.25)
    assert np.allclose(R, R.T)
    assert Z80 == pytest.approx(1.2816, abs=1e-4)
