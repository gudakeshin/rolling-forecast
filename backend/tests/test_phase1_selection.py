"""Phase 1 — capabilities, benchmarks, multi-metric selection."""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from app.domain.engines.base_model import (
    mase_denominator,
    seasonal_period_length,
)
from app.domain.engines.model_registry import (
    ModelRegistry,
    _pick_best,
    effective_selection_rule,
    reset_model_registry,
)
from app.domain.engines.naive import NaiveModel
from app.domain.engines.seasonal_naive import SeasonalNaiveModel
from app.models.forecast import ForecastVersion
from app.models.forecast import ForecastLineResult


@pytest.fixture(autouse=True)
def _reset_registry():
    reset_model_registry()
    yield
    reset_model_registry()


def test_capabilities_on_builtin_models():
    reg = ModelRegistry()
    for name in ("linear", "ets", "arima", "prophet"):
        caps = reg.get(name).capabilities
        assert caps.min_data_points == reg.get(name).min_data_points
        assert caps.cost_class in {"trivial", "cheap", "moderate", "expensive"}
        assert caps.display_label
    assert reg.get("prophet").capabilities.max_folds_short_series == 2
    assert reg.get("linear").capabilities.complexity_rank < reg.get("prophet").capabilities.complexity_rank


def test_benchmarks_registered_when_enabled(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "enable_benchmark_models", True)
    reset_model_registry()
    reg = ModelRegistry()
    assert "naive" in reg.list_models()
    assert "seasonal_naive" in reg.list_models()
    assert reg.get("naive").capabilities.is_benchmark
    assert reg.get("seasonal_naive").capabilities.cost_class == "trivial"


def test_benchmarks_absent_when_disabled(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "enable_benchmark_models", False)
    reset_model_registry()
    reg = ModelRegistry()
    assert "naive" not in reg.list_models()
    assert "seasonal_naive" not in reg.list_models()


def test_naive_fit_predict_roundtrip():
    model = NaiveModel()
    y = pd.Series([10.0, 12.0, 11.0, 13.0])
    dates = pd.date_range("2024-01-01", periods=4, freq="MS")
    params = model.fit(y, dates)
    out = model.predict(params, horizon=3, last_date=dates[-1])
    assert list(out.point_forecast) == [13.0, 13.0, 13.0]
    assert len(out.lower_bound) == 3


def test_seasonal_naive_exact_on_synthetic():
    m = seasonal_period_length()
    season = np.arange(1, m + 1, dtype=float) * 10
    y = pd.Series(np.tile(season, 3))
    dates = pd.date_range("2022-01-01", periods=len(y), freq="MS")
    model = SeasonalNaiveModel()
    params = model.fit(y, dates)
    out = model.predict(params, horizon=m, last_date=dates[-1])
    np.testing.assert_allclose(out.point_forecast, season)


def test_mase_denominator_ladder_flat_and_zero():
    flat = np.ones(30)
    denom, method = mase_denominator(flat, m=12)
    assert method == "mean_abs"
    assert denom == pytest.approx(1.0)

    zeros = np.zeros(20)
    denom, method = mase_denominator(zeros, m=12)
    assert method == "ineligible"
    assert denom == 0.0

    # Seasonal path needs n_train > 2m
    m = 12
    y = np.concatenate([
        np.arange(m, dtype=float),
        np.arange(m, dtype=float) + 1,
        np.arange(m, dtype=float) + 2,
    ])
    assert len(y) > 2 * m
    denom, method = mase_denominator(y, m=m)
    assert method == "seasonal_naive"
    assert denom > 0


def test_evaluate_cv_returns_multi_metrics():
    rng = np.random.default_rng(0)
    n = 36
    values = 100 + np.arange(n) * 2 + rng.normal(0, 3, n)
    series = pd.Series(values)
    dates = pd.date_range("2022-01-01", periods=n, freq="MS")
    from app.domain.engines.linear import LinearTrendModel

    cv = LinearTrendModel().evaluate_cv(series, dates, n_folds=3, fold_horizon=3)
    assert "mean_mase" in cv
    assert "mean_pinball_10" in cv
    assert "mean_pinball_90" in cv
    assert "coverage_80" in cv
    assert cv["mean_mase"] != float("inf")
    assert cv["n"] > 0


def test_selection_prefers_mase_when_multi(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "selection_metric", "multi")
    monkeypatch.setattr(settings, "enable_benchmark_models", True)
    reset_model_registry()
    reg = ModelRegistry()
    # Strong seasonality — seasonal_naive should be competitive / win often
    m = 12
    t = np.arange(48)
    season = 40 * np.sin(2 * np.pi * t / m)
    values = 200 + season + np.random.default_rng(2).normal(0, 1, 48)
    series = pd.Series(values)
    dates = pd.date_range("2021-01-01", periods=48, freq="MS")
    result = reg.compare_models(
        series, dates, test_size=6, models_to_test=["linear", "seasonal_naive", "naive"],
        two_stage=False,
    )
    assert result.selection_rule == "mase_pinball_complexity"
    assert result.best_model in {"seasonal_naive", "linear", "naive"}
    assert result.best_mase != float("inf")


def test_benchmarks_selectable_under_mape(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "selection_metric", "mape")
    monkeypatch.setattr(settings, "enable_benchmark_models", True)
    reset_model_registry()
    reg = ModelRegistry()
    # Flat series — naive should win MAPE
    series = pd.Series([100.0] * 24)
    dates = pd.date_range("2023-01-01", periods=24, freq="MS")
    result = reg.compare_models(
        series, dates, models_to_test=["linear", "naive"], two_stage=False
    )
    assert result.best_model in {"naive", "linear"}


def test_pick_best_benchmarks_lose_pure_ties():
    from app.domain.engines.model_registry import ModelComparisonResult

    comps = [
        ModelComparisonResult(
            model_name="seasonal_naive", mape=10, mase=1.0, pinball=1.0,
            evaluation_time_ms=1, eligible=True, is_benchmark=True, complexity_rank=1,
            fold_mases=[1.0, 1.0, 1.0], n_folds=3,
        ),
        ModelComparisonResult(
            model_name="ets", mape=10, mase=1.0, pinball=1.0,
            evaluation_time_ms=1, eligible=True, is_benchmark=False, complexity_rank=20,
            fold_mases=[1.0, 1.0, 1.0], n_folds=3,
        ),
    ]
    best, _, _, _ = _pick_best(comps, rule="mase_pinball_complexity")
    assert best == "ets"  # non-benchmark wins pure tie


def test_two_stage_skips_expensive_when_immaterial(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "selection_metric", "multi")
    monkeypatch.setattr(settings, "enable_benchmark_models", False)
    reset_model_registry()
    reg = ModelRegistry()
    # Low CV, cheap MASE will be good on smooth trend → skip prophet
    t = np.arange(36)
    series = pd.Series(100 + 0.5 * t + np.random.default_rng(0).normal(0, 0.1, 36))
    dates = pd.date_range("2022-01-01", periods=36, freq="MS")
    result = reg.compare_models(series, dates, is_material=False, two_stage=True)
    prophet = next(c for c in result.comparisons if c.model_name == "prophet")
    assert prophet.skipped_budget is True


def test_confidence_uses_registry_base_score():
    from app.services.confidence import compute_confidence_score

    result = SimpleNamespace(
        model_mape=10.0,
        p10=90.0,
        p50=100.0,
        p90=110.0,
        model_r_squared=0.8,
        model_type="naive",
    )
    score = compute_confidence_score(result)
    assert 0 <= score <= 100


def test_effective_selection_rule(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "selection_metric", "multi")
    assert effective_selection_rule() == "mase_pinball_complexity"
    monkeypatch.setattr(settings, "selection_metric", "mape")
    assert effective_selection_rule() == "mape"


@pytest.mark.asyncio
async def test_rescore_all_skips_mismatched_rule(db_session, seed_users, seed_line_items, monkeypatch):
    from app.config import settings
    from app.api.dashboard import rescore_all_forecasts
    from app.domain.engines.model_registry import effective_selection_rule

    monkeypatch.setattr(settings, "selection_metric", "multi")
    live = effective_selection_rule()
    assert live == "mase_pinball_complexity"

    admin = seed_users["admin"]
    li = seed_line_items["REV-001"]
    v_ok = ForecastVersion(
        id="sel-ok",
        name="OK",
        status="draft",
        version_type="baseline",
        horizon_months=3,
        scenario="base",
        selection_rule=live,
    )
    v_old = ForecastVersion(
        id="sel-old",
        name="Old",
        status="draft",
        version_type="baseline",
        horizon_months=3,
        scenario="base",
        selection_rule="mape",
    )
    db_session.add_all([v_ok, v_old])
    for vid in ("sel-ok", "sel-old"):
        db_session.add(
            ForecastLineResult(
                id=f"flr-{vid}",
                version_id=vid,
                line_item_id=li.id,
                period="2026-01",
                p10=90.0,
                p50=100.0,
                p90=110.0,
                model_type="linear",
                model_mape=10.0,
                confidence_score=0.0,
                confidence_level="low",
            )
        )
    db_session.commit()

    result = await rescore_all_forecasts(force=False, current_user=admin, db=db_session)
    assert result["versions_skipped"] >= 1
    old = db_session.query(ForecastLineResult).filter_by(id="flr-sel-old").one()
    assert old.confidence_score == 0.0

    result_force = await rescore_all_forecasts(force=True, current_user=admin, db=db_session)
    assert result_force["forced"] is True
    db_session.refresh(old)
    assert old.confidence_score > 0
