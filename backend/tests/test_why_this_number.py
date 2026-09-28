"""Phase 5.1 — 'why this number?' drawer: model selection, exog, calibration,
reconciliation delta, override history, and realized coverage per cell."""

from __future__ import annotations

import asyncio

import numpy as np
import pandas as pd
import pytest
from fastapi import HTTPException

from app.models.forecast import ForecastLineResult, ForecastVersion, ModelMetadata
from app.models.fx import ForecastAccuracyRecord
from app.models.line_item import LineItem
from app.models.override import Override


def test_auto_selection_persists_comparisons_into_model_metadata(monkeypatch):
    """The pipeline must not just compute the runner-up/margin and discard it —
    ModelMetadata.parameters["model_selection"] is what the endpoint reads."""
    from app.config import settings
    from app.domain.engines.model_registry import ModelRegistry, reset_model_registry
    from app.services.forecast_pipeline import LineForecastContext, forecast_line_item
    from app.services.period_calendar import get_calendar_config

    monkeypatch.setattr(settings, "enable_benchmark_models", True)
    monkeypatch.setattr(settings, "outlier_cleaning_enabled", False)
    monkeypatch.setattr(settings, "selection_metric", "multi")
    reset_model_registry()
    try:
        li = LineItem(account_code="TEST-AUTO", name="Auto Fixture", category="Revenue", display_order=1)
        li.id = 9101

        n = 24
        values = pd.Series(np.arange(n, dtype=float) * 10.0 + 100.0)
        periods = [f"{2023 + (i // 12)}-{(i % 12) + 1:02d}" for i in range(n)]
        dates = pd.DatetimeIndex([pd.Timestamp(f"{p}-01") for p in periods])
        registry = ModelRegistry()
        ctx = LineForecastContext(
            version_id="wtn-auto-v1", horizon=6, random_seed=42, model_type="auto",
            selection_rule="mase_pinball_complexity", is_material=True,
            cal_cfg=get_calendar_config(), model_registry=registry,
        )
        out = forecast_line_item(None, li, values, dates, periods, ctx)  # type: ignore[arg-type]

        assert len(out.metadata_rows) == 1
        model_selection = out.metadata_rows[0].parameters.get("model_selection")
        assert model_selection is not None
        assert model_selection["best_model"] == out.selected_model
        assert len(model_selection["comparisons"]) > 1
        assert any(c["selected"] for c in model_selection["comparisons"])
    finally:
        reset_model_registry()


@pytest.fixture
def base_version(db_session):
    v = ForecastVersion(
        id="wtn-v1", name="WTN Test", status="published",
        version_type="baseline", horizon_months=1, scenario="base",
    )
    db_session.add(v)
    db_session.commit()
    return v


def test_basic_fields_and_no_extras_when_nothing_special_happened(
    db_session, seed_line_items, base_version
):
    from app.api.panel import why_this_number

    li = seed_line_items["REV-001"]
    result = ForecastLineResult(
        id="wtn-flr-plain", version_id=base_version.id, line_item_id=li.id,
        period="2026-01", p10=80_000, p50=100_000, p90=120_000,
        model_type="ets", confidence_score=72.0, confidence_level="medium",
        bounds_method="model",
    )
    db_session.add(result)
    db_session.commit()

    out = asyncio.run(why_this_number(result_id=result.id, current_user=None, db=db_session))
    assert out.data["model_type"] == "ets"
    assert out.data["p50"] == 100_000
    assert out.data["model_selection"] is None
    assert out.data["exog"] is None
    assert out.data["reconciliation"] is None
    assert out.data["override_history"] == []
    assert out.data["realized_coverage"] is None
    assert out.data["structural_break"] is None


def test_model_selection_and_exog_surfaced_from_metadata_parameters(
    db_session, seed_line_items, base_version
):
    from app.api.panel import why_this_number

    li = seed_line_items["REV-001"]
    result = ForecastLineResult(
        id="wtn-flr-selected", version_id=base_version.id, line_item_id=li.id,
        period="2026-02", p10=80_000, p50=100_000, p90=120_000,
        model_type="ets", confidence_score=80.0, confidence_level="high",
        bounds_method="conformal",
    )
    db_session.add(result)
    db_session.flush()
    db_session.add(ModelMetadata(
        line_result_id=result.id,
        model_type="ets",
        parameters={
            "model_selection": {
                "best_model": "ets",
                "best_mape": 8.2,
                "comparisons": [
                    {"model": "ets", "mape": 8.2, "selected": True},
                    {"model": "arima", "mape": 9.1, "selected": False},
                ],
            },
            "exog_spec": {
                "mode": "rejected",
                "exog_admitted": False,
                "exog_rejected_reason": "insufficient_train_points",
            },
            "interval_calibration": {"method": "conformal", "n_residuals": 24},
        },
        structural_break_detected=True,
        structural_break_period="2025-06",
        cleaned_periods=["2024-03"],
        outliers_cleaned=1,
    ))
    db_session.commit()

    out = asyncio.run(why_this_number(result_id=result.id, current_user=None, db=db_session))
    assert out.data["model_selection"]["best_model"] == "ets"
    assert len(out.data["model_selection"]["comparisons"]) == 2
    assert out.data["exog"]["admitted"] is False
    assert out.data["exog"]["rejected_reason"] == "insufficient_train_points"
    assert out.data["calibration"]["method"] == "conformal"
    assert out.data["structural_break"] == {"detected": True, "period": "2025-06"}
    assert out.data["outlier_cleaning"] == {"cleaned_periods": ["2024-03"], "n_cleaned": 1}


def test_reconciliation_delta_only_shown_when_mint_actually_moved_it(
    db_session, seed_line_items, base_version
):
    from app.api.panel import why_this_number

    li = seed_line_items["REV-001"]
    result = ForecastLineResult(
        id="wtn-flr-reconciled", version_id=base_version.id, line_item_id=li.id,
        period="2026-03", p10=80_000, p50=105_000, p90=125_000,
        pre_reconcile_p50=100_000, model_type="ets",
        confidence_score=70.0, confidence_level="medium",
    )
    db_session.add(result)
    db_session.commit()

    out = asyncio.run(why_this_number(result_id=result.id, current_user=None, db=db_session))
    assert out.data["reconciliation"] == {
        "pre_reconcile_p50": 100_000,
        "published_p50": 105_000,
        "delta": 5_000.0,
        "delta_pct": 5.0,
    }


def test_override_history_spans_versions_for_the_same_cell(
    db_session, seed_users, seed_line_items, base_version
):
    from app.api.panel import why_this_number

    li = seed_line_items["REV-001"]
    other_version = ForecastVersion(
        id="wtn-v2", name="WTN Test v2", status="draft",
        version_type="baseline", horizon_months=1, scenario="base",
    )
    db_session.add(other_version)
    result = ForecastLineResult(
        id="wtn-flr-overridden", version_id=base_version.id, line_item_id=li.id,
        period="2026-04", p10=80_000, p50=100_000, p90=120_000,
        model_type="ets", confidence_score=70.0, confidence_level="medium",
    )
    db_session.add(result)
    # An override on a DIFFERENT version, same line item + period.
    db_session.add(Override(
        version_id=other_version.id, line_item_id=li.id, period="2026-04",
        original_model_value=98_000, override_value=110_000,
        reason="BU flagged a signed contract not yet in the model",
        status="active", user_id=seed_users["analyst"].id,
    ))
    db_session.commit()

    out = asyncio.run(why_this_number(result_id=result.id, current_user=None, db=db_session))
    assert len(out.data["override_history"]) == 1
    assert out.data["override_history"][0]["version_id"] == other_version.id
    assert out.data["override_history"][0]["override_value"] == 110_000


def test_realized_coverage_aggregated_across_vintages(
    db_session, seed_line_items, base_version
):
    from app.api.panel import why_this_number

    li = seed_line_items["REV-001"]
    result = ForecastLineResult(
        id="wtn-flr-coverage", version_id=base_version.id, line_item_id=li.id,
        period="2026-05", p10=80_000, p50=100_000, p90=120_000,
        model_type="ets", confidence_score=70.0, confidence_level="medium",
    )
    db_session.add(result)
    db_session.add(ForecastAccuracyRecord(
        version_id=base_version.id, line_item_id=li.id, period="2026-05",
        horizon_offset=1, predicted_p50=100_000, actual=97_000,
        absolute_error=3_000, pct_error=3.0, within_p10_p90=True,
    ))
    db_session.commit()

    out = asyncio.run(why_this_number(result_id=result.id, current_user=None, db=db_session))
    assert out.data["realized_coverage"] == {
        "n_vintages": 1, "n_within_band": 1, "latest_actual": 97_000, "latest_pct_error": 3.0,
    }


def test_unknown_result_id_is_404(db_session):
    from app.api.panel import why_this_number

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(why_this_number(result_id="does-not-exist", current_user=None, db=db_session))
    assert exc_info.value.status_code == 404
