"""CV scoring correctness: fold recency weights, failed folds, fold horizon,
and the paired-SE Occam band used by model selection."""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd
import pytest

from app.domain.engines.base_model import (
    ForecastOutput,
    IForecastModel,
    ModelCapabilities,
    cv_fold_horizon,
    recency_weighted_mean,
)
from app.domain.engines.model_registry import (
    ModelComparisonResult,
    ModelRegistry,
    _paired_band,
    _pick_best,
    reset_model_registry,
)


@pytest.fixture(autouse=True)
def _reset_registry():
    reset_model_registry()
    yield
    reset_model_registry()


# --- recency_weighted_mean ---------------------------------------------------


def test_most_recent_fold_gets_highest_weight():
    # values[0] is fold 1 = the most recent origin → weight 2; oldest → 1.
    assert recency_weighted_mean([1.0, 10.0]) == pytest.approx((1 * 2 + 10 * 1) / 3)
    assert recency_weighted_mean([10.0, 1.0]) == pytest.approx((10 * 2 + 1 * 1) / 3)
    # Three folds: weights 3, 2, 1.
    assert recency_weighted_mean([3.0, 6.0, 9.0]) == pytest.approx((9 + 12 + 9) / 6)


def test_any_failed_fold_fails_the_model():
    assert recency_weighted_mean([1.0, float("inf"), 1.0]) == float("inf")
    assert recency_weighted_mean([float("nan"), 1.0]) == float("inf")
    assert recency_weighted_mean([]) == float("inf")


def test_data_unscorable_fold_is_skipped_without_shifting_weights():
    # Fold 1 unscorable (None); folds 2 and 3 keep their positional weights 2, 1.
    assert recency_weighted_mean([None, 1.0, 4.0]) == pytest.approx((1 * 2 + 4 * 1) / 3)
    assert recency_weighted_mean([None, None]) == float("inf")


# --- evaluate_cv / compare_models with controllable models --------------------


class _ScriptedModel(IForecastModel):
    """Forecasts last value + a per-fold offset; can raise on a chosen fold.

    Folds are identified by training length: with fold_horizon h and n points,
    fold k trains on n - h*k points.
    """

    def __init__(self, name: str, offsets: dict[int, float], *, fail_on: int | None = None, rank: int = 50):
        self._name = name
        self._offsets = offsets  # train_len -> additive forecast error
        self._fail_on = fail_on
        self._rank = rank

    @property
    def name(self) -> str:
        return self._name

    @property
    def min_data_points(self) -> int:
        return 12

    @property
    def capabilities(self) -> ModelCapabilities:
        return ModelCapabilities(min_data_points=12, complexity_rank=self._rank, display_label=self._name)

    def fit(self, series: pd.Series, dates: pd.DatetimeIndex) -> dict[str, Any]:
        if self._fail_on is not None and len(series) == self._fail_on:
            raise RuntimeError("scripted fold failure")
        return {"n": len(series)}

    def predict(self, params, horizon, last_date, confidence_level=0.80) -> ForecastOutput:
        n = params["n"]
        # Truth is the flat series 100; error is the scripted offset for this fold.
        point = np.full(horizon, 100.0 + self._offsets.get(n, 0.0))
        return ForecastOutput(
            point_forecast=point,
            lower_bound=point - 5,
            upper_bound=point + 5,
            periods=[str(i) for i in range(horizon)],
            model_type=self._name,
        )


def _trend_series(n: int = 60) -> tuple[pd.Series, pd.DatetimeIndex]:
    # Non-flat training segment so MASE has a valid denominator; actuals the
    # scripted models are scored against are the values themselves.
    rng = np.random.default_rng(0)
    values = 100.0 + rng.normal(0, 3, n)
    return pd.Series(values), pd.date_range("2020-01-01", periods=n, freq="MS")


def test_evaluate_cv_lists_folds_most_recent_first():
    series, dates = _trend_series(60)
    # Error only on the most recent fold (train = 60 - 12 = 48).
    model = _ScriptedModel("m", {48: 50.0})
    cv = model.evaluate_cv(series, dates, n_folds=3, fold_horizon=12)
    assert cv["fold_horizon"] == 12
    assert cv["fold_mases"][0] > 5 * cv["fold_mases"][1]
    # Weighted mean is dominated by the bad (most recent) fold.
    unweighted = float(np.mean(cv["fold_mases"]))
    assert cv["mean_mase"] > unweighted


def test_model_that_fails_one_fold_is_ineligible():
    series, dates = _trend_series(60)
    # "fragile" is near-perfect when it runs but crashes on the oldest fold;
    # "steady" is mediocre everywhere. Old behaviour scored fragile's
    # survivors and picked it.
    fragile = _ScriptedModel("fragile", {}, fail_on=60 - 36, rank=10)
    steady = _ScriptedModel("steady", {48: 4.0, 36: 4.0, 24: 4.0}, rank=20)

    cv = fragile.evaluate_cv(series, dates, n_folds=3, fold_horizon=12)
    assert cv["mean_mase"] == float("inf")
    assert cv["mean_mape"] == float("inf")

    reg = ModelRegistry()
    reg.register(fragile)
    reg.register(steady)
    result = reg.compare_models(
        series, dates, models_to_test=["fragile", "steady"],
        two_stage=False, selection_rule="mase_pinball_complexity",
    )
    assert result.best_model == "steady"
    fragile_cmp = next(c for c in result.comparisons if c.model_name == "fragile")
    assert fragile_cmp.error is not None


def test_cv_fold_horizon_matches_forecast_horizon_when_history_allows():
    assert cv_fold_horizon(12, 60) == 12
    assert cv_fold_horizon(6, 60) == 6
    # Short history: shortened so a 12-point model still gets two folds.
    assert cv_fold_horizon(12, 24) == 6
    assert cv_fold_horizon(12, 14) == 3
    # Never longer than the forecast itself.
    assert cv_fold_horizon(2, 60) == 2


def test_compare_models_scores_at_requested_horizon():
    series, dates = _trend_series(60)
    reg = ModelRegistry()
    reg.register(_ScriptedModel("m", {}))
    default = reg.compare_models(series, dates, models_to_test=["m"], two_stage=False)
    cmp = default.comparisons[0]
    assert cmp.fold_horizon == 12  # settings.default_horizon_months
    assert max(cmp.fold_residuals) == 12  # residuals now exist out to h=12

    six = reg.compare_models(series, dates, models_to_test=["m"], two_stage=False, forecast_horizon=6)
    assert six.comparisons[0].fold_horizon == 6


# --- paired-SE Occam band -----------------------------------------------------


def _cmp(name: str, folds: list[float], *, rank: int, fold_horizon: int | None = 12) -> ModelComparisonResult:
    return ModelComparisonResult(
        model_name=name,
        mape=10.0,
        mase=recency_weighted_mean(folds),
        pinball=1.0,
        evaluation_time_ms=1,
        eligible=True,
        complexity_rank=rank,
        fold_mases=folds,
        n_folds=len(folds),
        fold_horizon=fold_horizon,
    )


def test_consistently_worse_simple_model_is_outside_paired_band():
    # Fold difficulty varies a lot (shared across models); the simple model is
    # worse by 0.05 on every fold. Pooled SE (~0.2) would have let it win the
    # Occam tie-break; the paired differences have zero spread.
    complex_ = _cmp("complex", [0.50, 0.90, 0.30, 1.20, 0.70], rank=40)
    simple = _cmp("simple", [0.55, 0.95, 0.35, 1.25, 0.75], rank=10)
    winner, *_ = _pick_best([complex_, simple], rule="mase_pinball_complexity")
    assert winner == "complex"
    assert _paired_band(complex_.fold_mases, simple.fold_mases, complex_.mase) == pytest.approx(0.0)


def test_noisy_tie_goes_to_simpler_model():
    # Fold-by-fold the two models trade places: the mean gap is inside the SE
    # of the paired differences, so Occam picks the simpler one.
    complex_ = _cmp("complex", [0.50, 0.90, 0.30, 1.20, 0.70], rank=40)
    simple = _cmp("simple", [0.60, 0.80, 0.42, 1.10, 0.80], rank=10)
    assert simple.mase > complex_.mase
    winner, *_ = _pick_best([complex_, simple], rule="mase_pinball_complexity")
    assert winner == "simple"


def test_few_folds_keep_five_percent_floor():
    # ≤ 3 paired folds: SE is under-powered, band floored at 5% of best.
    best = [1.0, 1.0, 1.0]
    cand = [1.04, 1.04, 1.04]
    assert _paired_band(best, cand, 1.0) == pytest.approx(0.05)


def test_unpairable_folds_fall_back_to_unpaired_band():
    complex_ = _cmp("complex", [0.50, 0.90, 0.30, 1.20, 0.70], rank=40)
    panel = _cmp("panel", [0.65, 1.05, 0.45], rank=10, fold_horizon=3)
    assert panel.mase > complex_.mase
    band = _paired_band(complex_.fold_mases, panel.fold_mases, complex_.mase, paired=False)
    assert band > 0.05
    winner, *_ = _pick_best([complex_, panel], rule="mase_pinball_complexity")
    assert winner == "panel"  # mismatched fold structure → pooled SE, as before
    assert math.isfinite(band)
