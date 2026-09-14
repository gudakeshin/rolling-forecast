"""Split-conformal interval calibration (Phase 1).

The claim under test is narrow and checkable: bands built from out-of-sample CV
residuals should cover ~80% of *held-out* errors, and should do so better than
the parametric band they replace when the model's distributional assumption is
wrong. Everything else here guards the honesty rails -- refusing to calibrate on
thin evidence, and never silently breaking the p10 <= p50 <= p90 invariant that
MinT and the non-negative clamp both depend on.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.config import settings
from app.domain.engines.linear import LinearTrendModel
from app.domain.engines.model_registry import ModelRegistry
from app.services.interval_calibration import (
    DEFAULT_ALPHA,
    apply_bands,
    conformal_bands,
    rescale_for_realized_coverage,
)
from app.services.period_calendar import get_calendar_config
from app.services.reconciliation import BOUNDS_METHOD_CONFORMAL, BOUNDS_METHOD_MODEL

TARGET = 1.0 - DEFAULT_ALPHA


def _residuals(rng, sigma_by_h: dict[int, float], n: int, bias: float = 0.0):
    return {h: list(rng.normal(bias, s, n)) for h, s in sigma_by_h.items()}


# ---------------------------------------------------------------- coverage ---

def test_covers_target_on_held_out_errors():
    """Calibrate on one error sample, measure on a fresh one: ~80% inside."""
    rng = np.random.default_rng(7)
    sigma = {1: 10.0, 2: 14.0, 3: 17.0}
    bands = conformal_bands(_residuals(rng, sigma, 200))
    assert bands is not None

    hits = total = 0
    for h, s in sigma.items():
        fresh = rng.normal(0, s, 5000)
        hits += int(np.sum((fresh >= bands.lower_offsets[h]) & (fresh <= bands.upper_offsets[h])))
        total += fresh.size

    coverage = hits / total
    assert abs(coverage - TARGET) <= 0.05, f"coverage {coverage:.3f} off target {TARGET}"


def test_beats_the_parametric_band_under_heavy_tails():
    """The band conformal replaces assumes normality. When that is wrong, the
    earned band should be closer to nominal than the assumed one."""
    rng = np.random.default_rng(11)
    # Student-t(3) errors: the Gaussian z=1.2816 band is far too narrow.
    draws = {1: list(rng.standard_t(3, 400) * 10.0)}
    bands = conformal_bands(draws)
    assert bands is not None

    fresh = rng.standard_t(3, 20000) * 10.0
    conformal_cov = float(np.mean((fresh >= bands.lower_offsets[1]) & (fresh <= bands.upper_offsets[1])))

    sigma = float(np.std(draws[1]))
    z = 1.2816  # the constant the engines use for an 80% band
    analytic_cov = float(np.mean(np.abs(fresh) <= z * sigma))

    assert abs(conformal_cov - TARGET) < abs(analytic_cov - TARGET)
    assert abs(conformal_cov - TARGET) <= 0.05


def test_bands_widen_with_horizon():
    """Pooling horizons would flatten this; per-horizon quantiles must not."""
    rng = np.random.default_rng(3)
    bands = conformal_bands(_residuals(rng, {1: 5.0, 6: 15.0, 12: 30.0}, 60))
    assert bands is not None
    widths = [bands.upper_offsets[h] - bands.lower_offsets[h] for h in (1, 6, 12)]
    assert widths[0] < widths[1] < widths[2]
    assert bands.method == "conformal_per_horizon"


# -------------------------------------------------------------- honesty ---

def test_refuses_to_calibrate_on_thin_evidence():
    """Too few residuals must yield None so the caller keeps the analytic band.

    Fabricating a band from three numbers would be worse than the parametric
    one it replaced, so this is a deliberate outcome rather than a failure.
    """
    assert conformal_bands({1: [1.0, 2.0, 3.0]}) is None
    assert conformal_bands({}) is None
    assert conformal_bands({1: [float("nan"), float("inf")]}) is None


def test_thin_horizon_borrows_pooled_residuals_and_says_so():
    rng = np.random.default_rng(5)
    thin = {1: list(rng.normal(0, 5, 20)), 2: [1.0, 2.0]}
    bands = conformal_bands(thin, min_per_horizon=5)
    assert bands is not None
    assert bands.method == "conformal_pooled"
    assert bands.per_horizon_counts == {1: 20, 2: 2}


def test_saturation_is_reported_not_hidden():
    """Below ~9 residuals the 90th conformal quantile cannot be certified."""
    rng = np.random.default_rng(9)
    bands = conformal_bands({1: list(rng.normal(0, 5, 9))}, min_per_horizon=5, min_total=9)
    assert bands is not None
    # n=9 sits exactly at the boundary; n=8 pooled below it must flag.
    tight = conformal_bands(
        {1: list(rng.normal(0, 5, 5)), 2: list(rng.normal(0, 5, 4))},
        min_per_horizon=5,
        min_total=9,
    )
    assert tight is not None
    assert tight.saturated_horizons, "small-sample saturation should be recorded"


def test_bias_is_surfaced_and_band_still_contains_the_point():
    """A biased model yields an asymmetric band, but p10 <= p50 <= p90 holds.

    MinT backs leaf sigma out of the p10/p90 gap and the pipeline floors p10 at
    zero, so the invariant has to survive even when the bias is large.
    """
    rng = np.random.default_rng(13)
    bands = conformal_bands(_residuals(rng, {1: 2.0}, 200, bias=50.0))
    assert bands is not None
    assert bands.median_bias[1] > 40  # the bias is reported, not absorbed

    point = np.array([100.0])
    lower, upper, diag = apply_bands(point, bands)
    assert lower[0] <= point[0] <= upper[0]
    assert diag["bias_clamped_steps"] == 1


def test_non_negative_lines_never_get_a_negative_floor():
    rng = np.random.default_rng(17)
    bands = conformal_bands(_residuals(rng, {1: 80.0}, 100))
    assert bands is not None
    lower, _, _ = apply_bands(np.array([10.0]), bands, non_negative=True)
    assert lower[0] >= 0.0


def test_horizons_beyond_calibration_widen_rather_than_flatten():
    rng = np.random.default_rng(19)
    bands = conformal_bands(_residuals(rng, {1: 10.0, 2: 12.0, 3: 14.0}, 40))
    assert bands is not None
    lower, upper, diag = apply_bands(np.array([100.0] * 9), bands)
    widths = upper - lower
    assert diag["calibrated_steps"] == 3
    assert diag["extrapolated_steps"] == 6
    assert widths[8] > widths[2], "extrapolated band must keep widening"


# ----------------------------------------------- realized-vintage feedback ---

@pytest.mark.parametrize(
    "realized,expect",
    [(0.60, "wider"), (0.95, "tighter"), (TARGET, "same"), (None, "same")],
)
def test_realized_coverage_corrects_the_cv_band(realized, expect):
    rng = np.random.default_rng(23)
    bands = conformal_bands(_residuals(rng, {1: 10.0}, 100))
    assert bands is not None
    out = rescale_for_realized_coverage(bands, realized)
    if expect == "wider":
        assert out.scale > 1.0 and out.method.endswith("+realized")
    elif expect == "tighter":
        assert out.scale < 1.0
    else:
        assert out.scale == pytest.approx(1.0, abs=1e-3)


def test_rescale_is_clamped_against_noisy_coverage_estimates():
    """A handful of realized points must not be allowed to blow the band up."""
    rng = np.random.default_rng(29)
    bands = conformal_bands(_residuals(rng, {1: 10.0}, 100))
    assert bands is not None
    assert rescale_for_realized_coverage(bands, 0.02).scale <= 2.0
    assert rescale_for_realized_coverage(bands, 0.999).scale >= 0.5


# ----------------------------------------------------- CV plumbing / e2e ---

def test_evaluate_cv_returns_residuals_keyed_by_horizon():
    """The calibration set has to actually come out of the CV that selects."""
    n = 36
    series = pd.Series(np.arange(n, dtype=float) * 3.0 + 50.0)
    dates = pd.date_range("2022-01-01", periods=n, freq="MS")
    cv = LinearTrendModel().evaluate_cv(series, dates, n_folds=3, fold_horizon=3)

    residuals = cv["fold_residuals"]
    assert set(residuals) == {1, 2, 3}, "one bucket per step ahead of the fold origin"
    assert all(len(v) == 3 for v in residuals.values()), "three folds each"
    assert all(np.isfinite(v) for vals in residuals.values() for v in vals)


def test_registry_exposes_residuals_for_the_model_actually_used():
    n = 36
    series = pd.Series(
        np.arange(n, dtype=float) * 3.0 + 50.0
        + np.sin(np.arange(n) * np.pi / 6.0) * 5.0
    )
    dates = pd.date_range("2022-01-01", periods=n, freq="MS")
    result = ModelRegistry().compare_models(series, dates, test_size=6)

    assert result.best_model
    assert result.residuals_for(result.best_model), "winner must carry a calibration set"
    assert result.residuals_for("nope-not-a-model") == {}
    assert result.residuals_for(None) == {}
    # Kept off the serialized payload -- it is in-process calibration data.
    assert all("fold_residuals" not in c for c in result.to_dict()["comparisons"])


def _run_line(monkeypatch, enabled: bool, n: int = 36):
    from app.services.forecast_pipeline import LineForecastContext, forecast_line_item
    from app.models.line_item import LineItem

    monkeypatch.setattr(settings, "conformal_calibration_enabled", enabled, raising=False)

    li = LineItem(
        account_code="CAL-001",
        name="Calibration Fixture",
        category="Revenue",
        display_order=1,
        allow_negative=False,
        sign_convention="positive",
    )
    li.id = 9101

    rng = np.random.default_rng(31)
    values = pd.Series(np.arange(n, dtype=float) * 8.0 + 400.0 + rng.normal(0, 20, n))
    periods = [f"{2022 + (i // 12)}-{(i % 12) + 1:02d}" for i in range(n)]
    dates = pd.DatetimeIndex([pd.Timestamp(f"{p}-01") for p in periods])

    ctx = LineForecastContext(
        version_id="cal-v1",
        horizon=6,
        model_type="linear",
        selection_rule="mase_pinball_complexity",
        is_material=True,
        cal_cfg=get_calendar_config(),
        model_registry=ModelRegistry(),
    )
    return forecast_line_item(None, li, values, dates, periods, ctx)  # type: ignore[arg-type]


def test_pipeline_tags_bounds_method_and_keeps_the_ordering_invariant(monkeypatch):
    out = _run_line(monkeypatch, enabled=True)
    assert not out.skipped, out.skip_reason
    assert out.line_rows
    assert all(r["bounds_method"] == BOUNDS_METHOD_CONFORMAL for r in out.line_rows)
    for r in out.line_rows:
        assert r["p10"] <= r["p50"] <= r["p90"]
        assert r["p10"] >= 0.0  # allow_negative=False

    meta = out.metadata_rows[0].parameters["interval_calibration"]
    assert meta["method"].startswith("conformal")
    assert meta["n_residuals"] >= 9


def test_calibration_is_off_by_default_so_upgrades_do_not_move_published_bands(monkeypatch):
    out = _run_line(monkeypatch, enabled=False)
    assert all(r["bounds_method"] == BOUNDS_METHOD_MODEL for r in out.line_rows)
    assert "interval_calibration" not in (out.metadata_rows[0].parameters or {})


def test_short_history_falls_back_to_the_analytic_band(monkeypatch):
    """Not enough folds to calibrate -> keep the engine band, and say why."""
    out = _run_line(monkeypatch, enabled=True, n=13)
    assert not out.skipped, out.skip_reason
    assert all(r["bounds_method"] == BOUNDS_METHOD_MODEL for r in out.line_rows)
    assert out.comparison.get("calibration_skipped") == "insufficient_cv_residuals"
