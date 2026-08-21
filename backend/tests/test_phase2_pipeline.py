"""Phase 2 — pipeline extraction characterization tests.

These assert that forecast_line_item is deterministic under a fixed seed /
calendar / FX-free series, and that zero/average edge cases go through the
registry path (batched rows + ModelMetadata). This is the safety net for
Phases 3 and 8.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.domain.engines.model_registry import ModelRegistry, reset_model_registry
from app.models.line_item import LineItem
from app.services.forecast_pipeline import LineForecastContext, forecast_line_item
from app.services.period_calendar import get_calendar_config
from app.services.reconciliation import BOUNDS_METHOD_MODEL


@pytest.fixture(autouse=True)
def _reset_registry(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "enable_benchmark_models", True)
    monkeypatch.setattr(settings, "outlier_cleaning_enabled", False)
    monkeypatch.setattr(settings, "selection_metric", "multi")
    reset_model_registry()
    yield
    reset_model_registry()


def _fingerprint(line_out) -> dict:
    """Comparable snapshot — strips non-deterministic UUIDs."""
    rows = [
        {
            "period": r["period"],
            "p10": r["p10"],
            "p50": r["p50"],
            "p90": r["p90"],
            "model_p50": r["model_p50"],
            "model_type": r["model_type"],
            "model_mape": r["model_mape"],
            "model_mase": r["model_mase"],
            "model_pinball": r["model_pinball"],
            "model_r_squared": r["model_r_squared"],
            "bounds_method": r["bounds_method"],
            "confidence_score": r["confidence_score"],
            "confidence_level": r["confidence_level"],
        }
        for r in line_out.line_rows
    ]
    metas = []
    for m in line_out.metadata_rows:
        metas.append({
            "model_type": m.model_type,
            "parameters": m.parameters,
            "training_window_start": m.training_window_start,
            "training_window_end": m.training_window_end,
            "training_points": m.training_points,
            "mape": m.mape,
            "r_squared": m.r_squared,
            "aic": m.aic,
            "seasonality_detected": m.seasonality_detected,
            "seasonality_period": m.seasonality_period,
            "structural_break_detected": m.structural_break_detected,
            "structural_break_period": m.structural_break_period,
            "cleaned_periods": m.cleaned_periods,
            "outliers_cleaned": m.outliers_cleaned,
            "random_seed": m.random_seed,
        })
    return {
        "selected_model": line_out.selected_model,
        "honest_mape": line_out.honest_mape,
        "selection_mase": line_out.selection_mase,
        "selection_pinball": line_out.selection_pinball,
        "was_zero": line_out.was_zero,
        "was_sparse": line_out.was_sparse,
        "skipped": line_out.skipped,
        "rows": rows,
        "metadata": metas,
    }


def _make_li(account_code: str, name: str, **kwargs) -> LineItem:
    """In-memory line item — no DB insert needed for the pipeline."""
    li = LineItem(
        account_code=account_code,
        name=name,
        category=kwargs.get("category", "Revenue"),
        display_order=kwargs.get("display_order", 1),
        allow_negative=kwargs.get("allow_negative", False),
        sign_convention=kwargs.get("sign_convention", "positive"),
    )
    li.id = kwargs.get("id", hash(account_code) % 10_000_000)
    return li


def _run_linear_fixture(seed: int = 42):
    li = _make_li("TEST-LIN", "Linear Fixture", id=9001)

    n = 24
    values = pd.Series(np.arange(n, dtype=float) * 10.0 + 100.0)
    periods = [f"{2023 + (i // 12)}-{(i % 12) + 1:02d}" for i in range(n)]
    cal = get_calendar_config()
    dates = pd.DatetimeIndex([pd.Timestamp(f"{p}-01") for p in periods])
    registry = ModelRegistry()
    ctx = LineForecastContext(
        version_id="char-v1",
        horizon=6,
        random_seed=seed,
        model_type="linear",
        selection_rule="mase_pinball_complexity",
        is_material=True,
        cal_cfg=cal,
        model_registry=registry,
    )
    # db unused by pipeline for the happy path (no FX / no queries)
    return forecast_line_item(None, li, values, dates, periods, ctx)  # type: ignore[arg-type]


def test_characterization_identical_across_calls():
    """Same inputs → byte-identical forecast fields (UUIDs excluded)."""
    a = _fingerprint(_run_linear_fixture(seed=42))
    b = _fingerprint(_run_linear_fixture(seed=42))
    assert a == b
    assert a["selected_model"] == "linear"
    assert len(a["rows"]) == 6
    assert all(r["bounds_method"] == BOUNDS_METHOD_MODEL for r in a["rows"])
    assert len(a["metadata"]) == 1
    assert a["metadata"][0]["random_seed"] == 42
    assert a["metadata"][0]["model_type"] == "linear"
    # Upward linear trend → first forecast > last actual (~330)
    assert a["rows"][0]["p50"] > 300


def test_characterization_seed_stable():
    a = _fingerprint(_run_linear_fixture(seed=1))
    b = _fingerprint(_run_linear_fixture(seed=1))
    assert a == b


def test_zero_model_via_pipeline_writes_metadata():
    li = _make_li("TEST-ZERO", "Zero Line", category="OpEx", allow_negative=False, id=9002)
    values = pd.Series([0.0] * 12)
    periods = [f"2024-{i:02d}" for i in range(1, 13)]
    dates = pd.DatetimeIndex([pd.Timestamp(f"{p}-01") for p in periods])
    out = forecast_line_item(
        None,  # type: ignore[arg-type]
        li,
        values,
        dates,
        periods,
        LineForecastContext(
            version_id="char-zero",
            horizon=3,
            random_seed=42,
            model_type="auto",
            model_registry=ModelRegistry(),
            cal_cfg=get_calendar_config(),
        ),
    )
    assert out.was_zero
    assert out.selected_model == "zero"
    assert not out.skipped
    assert len(out.line_rows) == 3
    assert all(r["p50"] == 0.0 for r in out.line_rows)
    assert len(out.metadata_rows) == 1
    assert out.metadata_rows[0].model_type == "zero"


def test_average_model_via_pipeline_for_very_sparse():
    li = _make_li("TEST-AVG", "Sparse Line", category="OpEx", id=9003)
    values = pd.Series([100.0, 120.0, 110.0])  # < 6 → very sparse
    periods = ["2024-01", "2024-02", "2024-03"]
    dates = pd.DatetimeIndex([pd.Timestamp(f"{p}-01") for p in periods])
    out = forecast_line_item(
        None,  # type: ignore[arg-type]
        li,
        values,
        dates,
        periods,
        LineForecastContext(
            version_id="char-avg",
            horizon=2,
            random_seed=42,
            model_type="auto",
            model_registry=ModelRegistry(),
            cal_cfg=get_calendar_config(),
        ),
    )
    assert out.was_sparse
    assert out.selected_model == "average"
    assert len(out.line_rows) == 2
    assert out.line_rows[0]["p50"] == pytest.approx(110.0)
    assert len(out.metadata_rows) == 1


def test_zero_and_average_not_auto_selected_on_normal_series():
    reg = ModelRegistry()
    assert "zero" in reg.list_models()
    assert "average" in reg.list_models()
    assert reg.get("zero").capabilities.auto_selectable is False
    n = 36
    values = pd.Series(100 + np.arange(n) * 2.0)
    dates = pd.date_range("2022-01-01", periods=n, freq="MS")
    result = reg.compare_models(values, dates, models_to_test=None, two_stage=False)
    assert result.best_model not in ("zero", "average")
    names = {c.model_name for c in result.comparisons}
    assert "zero" not in names
    assert "average" not in names


@pytest.mark.asyncio
async def test_generate_baseline_uses_pipeline(db_session, seed_actuals, skill_context):
    """Smoke: skill still generates and metadata is written via the batch path."""
    from app.domain.skills.generate_baseline import GenerateBaselineSkill
    from app.models.forecast import ForecastLineResult, ModelMetadata

    skill_context.context_manager.set_memory("last_dataset_id", seed_actuals.id)
    skill = GenerateBaselineSkill()
    result = await skill.execute(
        {"horizon_months": 3, "model_type": "linear", "random_seed": 42, "skip_plan_check": True},
        skill_context,
    )
    assert result.success, result.message
    vid = result.data["version_id"]
    rows = (
        db_session.query(ForecastLineResult)
        .filter(ForecastLineResult.version_id == vid)
        .all()
    )
    assert len(rows) > 0
    assert all(r.model_type for r in rows)
    metas = (
        db_session.query(ModelMetadata)
        .join(ForecastLineResult, ModelMetadata.line_result_id == ForecastLineResult.id)
        .filter(ForecastLineResult.version_id == vid)
        .all()
    )
    assert len(metas) >= 1
