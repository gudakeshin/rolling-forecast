"""Phase 4 — analytical rigor: outliers, breaks, linear PI, WAPE/FVA, formula tokens."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.domain.engines.linear import LinearTrendModel
from app.services.accuracy_snapshot import _wape, compute_fva
from app.services.coa_dependencies import (
    FormulaAmbiguityError,
    parse_formula_refs,
    resolve_name_token,
)
from app.services.error_handlers import HistoryAnalysis
from app.services.outlier_cleaning import clean_series_for_fit, detect_outliers_stl_mad


class _LI:
    def __init__(self, id, name, category="Revenue", account_code=None, business_unit=None, is_calculated=False):
        self.id = id
        self.name = name
        self.category = category
        self.account_code = account_code or name[:8]
        self.business_unit = business_unit
        self.is_calculated = is_calculated


def test_outlier_spike_flagged_seasonal_peak_not():
    """Injected spike is flagged; clean seasonal peaks are not."""
    rng = np.random.default_rng(0)
    t = np.arange(36)
    seasonal = 100 + 20 * np.sin(2 * np.pi * t / 12)
    series = seasonal + rng.normal(0, 2, size=36)
    series[18] = 400  # spike
    values = pd.Series(series)
    dates = pd.date_range("2023-01-01", periods=36, freq="MS")

    detected = detect_outliers_stl_mad(values, dates, mad_z=3.5)
    assert 18 in detected.outlier_indices
    # Seasonal peaks should not flood the detector — spike is the standout
    non_spike = [i for i in detected.outlier_indices if i != 18]
    assert len(non_spike) <= 2

    cleaned = clean_series_for_fit(values, dates, enabled=True)
    assert cleaned.n_cleaned >= 1
    assert abs(cleaned.cleaned.iloc[18]) < abs(series[18])


def test_structural_break_recovered_near_true_break():
    """Known level shift recovered within ±1 period via CUSUM/Chow."""
    n = 36
    y = np.concatenate([np.full(18, 100.0), np.full(18, 200.0)])
    y = y + np.random.default_rng(1).normal(0, 1, size=n)
    dates = pd.date_range("2022-01-01", periods=n, freq="MS")
    analysis = HistoryAnalysis(pd.Series(y), dates, "BreakSeries")
    assert analysis.has_structural_break
    idx = analysis.structural_break_index
    assert idx is not None
    assert abs(idx - 18) <= 1


def test_linear_pi_coverage_approx_80pct():
    """OLS prediction intervals should cover ~80% of holdout points on linear data."""
    rng = np.random.default_rng(42)
    n_train = 40
    x = np.arange(n_train, dtype=float)
    y = 10 + 2 * x + rng.normal(0, 3, size=n_train)
    dates = pd.date_range("2020-01-01", periods=n_train, freq="MS")
    model = LinearTrendModel()
    params = model.fit(pd.Series(y), dates)

    # Simulate many 1-step ahead points with same DGP
    hits = 0
    trials = 200
    for k in range(trials):
        # Fresh residual draw at next index
        true_next = 10 + 2 * n_train + rng.normal(0, 3)
        out = model.predict(params, horizon=1, last_date=dates[-1], confidence_level=0.80)
        lo, hi = float(out.lower_bound[0]), float(out.upper_bound[0])
        if lo <= true_next <= hi:
            hits += 1
    coverage = hits / trials
    # Generous band — finite-sample PI coverage
    assert 0.65 <= coverage <= 0.95


def test_wape_and_fva_hand_computed():
    published = [100.0, 200.0, 300.0]
    actuals = [110.0, 180.0, 330.0]
    naive = [90.0, 110.0, 180.0]
    # WAPE_pub = (|-10|+|20|+|-30|) / (110+180+330) * 100 = 60/620*100
    assert abs(_wape(published, actuals) - (60 / 620 * 100)) < 1e-9
    # WAPE_naive = (|-20|+|70|+|150|) / 620 * 100 = 240/620*100
    assert abs(_wape(naive, actuals) - (240 / 620 * 100)) < 1e-9
    fva = compute_fva(published, actuals, naive)
    assert fva is not None
    assert abs(fva - (240 / 620 * 100 - 60 / 620 * 100)) < 1e-9


def test_formula_longest_match_deferred_vs_revenue():
    items = [
        _LI(1, "Deferred Revenue", category="Liability"),
        _LI(2, "Revenue", category="Revenue"),
        _LI(3, "COGS", category="COGS"),
    ]
    assert resolve_name_token("Revenue", items).id == 2
    assert resolve_name_token("Deferred Revenue", items).id == 1
    refs = parse_formula_refs("[Deferred Revenue] - Revenue", items)
    assert [r[0].id for r in refs] == [1, 2]
    assert refs[1][1] == "subtract"


def test_formula_bu_ambiguity_raises():
    items = [
        _LI(1, "Revenue", business_unit="NA"),
        _LI(2, "Revenue", business_unit="EU"),
    ]
    with pytest.raises(FormulaAmbiguityError):
        resolve_name_token("Revenue", items)
