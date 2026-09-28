"""Phase 0 — cheap correctness fixes."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

import pandas as pd
import pytest

from app.domain.engines.model_registry import ModelRegistry
from app.models.driver_input import DriverFormConfig, DriverInput
from app.models.forecast import ForecastLineResult, ForecastVersion
from app.services.confidence import (
    MODEL_BASE_SCORES,
    classify_confidence,
    compute_confidence_score,
)
from app.services.driver_submission import apply_driver_submission, form_field_defs
from app.services.job_queue import get_job, update_job
from app.services.period_calendar import get_calendar_config, period_to_date


# ──────────────────────────────────────────────────────
# Confidence dedupe
# ──────────────────────────────────────────────────────


def test_compute_confidence_score_shared_and_bounded():
    result = SimpleNamespace(
        model_mape=10.0,
        p10=90.0,
        p50=100.0,
        p90=110.0,
        model_r_squared=0.8,
        model_type="ets",
    )
    score = compute_confidence_score(result)
    assert 0 <= score <= 100
    assert classify_confidence(score) in {"high", "medium", "low"}
    assert "average" in MODEL_BASE_SCORES and "zero" in MODEL_BASE_SCORES


def test_generate_baseline_and_score_confidence_use_same_scorer():
    from app.domain.skills import generate_baseline, score_confidence
    from app.services import confidence as conf

    assert generate_baseline._compute_confidence_score is conf.compute_confidence_score
    assert score_confidence.compute_confidence_score is conf.compute_confidence_score


# ──────────────────────────────────────────────────────
# Driver context key matching
# ──────────────────────────────────────────────────────


def test_build_driver_context_matches_numeric_key(db_session, seed_line_items, seed_users):
    from app.api.dashboard import _build_driver_context, _preload_driver_context

    li = seed_line_items["REV-001"]
    admin = seed_users["admin"]
    version = ForecastVersion(
        id="drv-ctx-v1",
        name="Driver Ctx",
        status="draft",
        version_type="baseline",
        horizon_months=3,
        scenario="base",
    )
    db_session.add(version)
    form = DriverFormConfig(
        business_unit="North America",
        name="Test Form",
        fields_schema={"fields": [{"name": str(li.id), "label": li.name, "type": "number"}]},
        is_active=True,
    )
    db_session.add(form)
    db_session.flush()
    # Legacy REST shape: key IS the line_item_id, no nested line_item_id field
    di = DriverInput(
        version_id=version.id,
        form_config_id=form.id,
        user_id=admin.id,
        business_unit="North America",
        values={str(li.id): {"value": 12345.0, "source": "manual", "reason": "manual bump"}},
        status="submitted",
        submitted_at=datetime.now(timezone.utc),
    )
    db_session.add(di)
    db_session.commit()

    cache = _preload_driver_context(db_session, version.id, [li.id])
    ctx = _build_driver_context(li, [], {}, db_session, version.id, cache=cache)
    assert len(ctx["driver_inputs"]) == 1
    assert ctx["driver_inputs"][0]["value"] == 12345.0
    assert ctx["driver_inputs"][0]["field"] == str(li.id)


def test_build_driver_context_matches_nested_line_item_id(db_session, seed_line_items, seed_users):
    from app.api.dashboard import _build_driver_context, _preload_driver_context

    li = seed_line_items["REV-001"]
    admin = seed_users["admin"]
    version = ForecastVersion(
        id="drv-ctx-v2",
        name="Driver Ctx Nested",
        status="draft",
        version_type="baseline",
        horizon_months=3,
        scenario="base",
    )
    db_session.add(version)
    form = DriverFormConfig(
        business_unit="North America",
        name="Named Form",
        fields_schema={
            "fields": [{"name": "headcount", "label": "Headcount", "type": "number", "line_item_id": li.id}]
        },
        is_active=True,
    )
    db_session.add(form)
    db_session.flush()
    di = DriverInput(
        version_id=version.id,
        form_config_id=form.id,
        user_id=admin.id,
        business_unit="North America",
        values={"headcount": {"value": 50.0, "line_item_id": li.id, "reason": "plan"}},
        status="submitted",
        submitted_at=datetime.now(timezone.utc),
    )
    db_session.add(di)
    db_session.commit()

    cache = _preload_driver_context(db_session, version.id, [li.id])
    ctx = _build_driver_context(li, [], {}, db_session, version.id, cache=cache)
    assert len(ctx["driver_inputs"]) == 1
    assert ctx["driver_inputs"][0]["field"] == "headcount"


# ──────────────────────────────────────────────────────
# Unified driver submission
# ──────────────────────────────────────────────────────


def test_form_field_defs_accepts_list_and_dict():
    form_dict = SimpleNamespace(fields_schema={"fields": [{"name": "a"}]})
    form_list = SimpleNamespace(fields_schema=[{"name": "b"}])
    assert form_field_defs(form_dict)[0]["name"] == "a"
    assert form_field_defs(form_list)[0]["name"] == "b"


def test_apply_driver_submission_applies_overrides(db_session, seed_line_items, seed_users):
    li = seed_line_items["REV-001"]
    admin = seed_users["admin"]
    version = ForecastVersion(
        id="drv-sub-v1",
        name="Submit",
        status="draft",
        version_type="baseline",
        horizon_months=3,
        scenario="base",
    )
    db_session.add(version)
    db_session.flush()
    db_session.add(
        ForecastLineResult(
            id="flr-drv-1",
            version_id=version.id,
            line_item_id=li.id,
            period="2026-01",
            p10=900.0,
            p50=1000.0,
            p90=1100.0,
            model_type="linear",
            confidence_score=70.0,
            confidence_level="medium",
        )
    )
    form = DriverFormConfig(
        business_unit="North America",
        name="Submit Form",
        fields_schema={
            "fields": [{"name": str(li.id), "label": li.name, "type": "number", "line_item_id": li.id}]
        },
        is_active=True,
        soft_deadline_days=30,
        hard_deadline_days=60,
    )
    db_session.add(form)
    db_session.commit()

    result = apply_driver_submission(
        db_session,
        version_id=version.id,
        form=form,
        values={str(li.id): {"value": 1500.0, "source": "manual"}},
        user_id=admin.id,
        business_unit="North America",
        apply_overrides=True,
        actor_username=admin.username,
        audit=True,
        commit=True,
    )
    assert result.overrides_applied == 1
    assert result.enriched_values[str(li.id)]["line_item_id"] == li.id
    flr = db_session.query(ForecastLineResult).filter_by(id="flr-drv-1").one()
    assert flr.is_overridden is True
    assert flr.override_value == 1500.0


# ──────────────────────────────────────────────────────
# Model selection — no silent linear default
# ──────────────────────────────────────────────────────


def test_compare_models_all_fail_returns_none_best_model():
    reg = ModelRegistry()
    series = pd.Series([1.0, 2.0])  # too short for every model
    dates = pd.date_range("2024-01-01", periods=2, freq="MS")
    result = reg.compare_models(series, dates, models_to_test=["linear", "ets", "arima", "prophet"])
    assert result.best_model is None
    assert result.best_mape == float("inf")
    assert all(not c.eligible or c.mape == float("inf") for c in result.comparisons)


# ──────────────────────────────────────────────────────
# Calendar-aware period parsing (plan_forecast path)
# ──────────────────────────────────────────────────────


def test_period_to_date_used_instead_of_string_concat():
    cfg = get_calendar_config()
    d = period_to_date("2025-03", cfg)
    assert d.year == 2025 and d.month == 3 and d.day == 1
    # Fiscal labels must not be parsed via period+"-01"
    fiscal = period_to_date("FY2026-P01", cfg)
    assert fiscal.year in (2025, 2026)


# ──────────────────────────────────────────────────────
# Job progress watchdog plumbing
# ──────────────────────────────────────────────────────


def test_update_job_sets_progress_updated_at_on_progress_change():
    update_job("phase0-job-1", status="running", progress=0.1, step="Starting")
    first = get_job("phase0-job-1")
    assert first is not None
    assert first.get("progress_updated_at")

    update_job("phase0-job-1", status="running", progress=0.1, step="Starting")
    # Identical progress/step should not be required to change the marker;
    # either keep or refresh is fine as long as marker exists.
    mid = get_job("phase0-job-1")
    assert mid is not None
    assert mid.get("progress_updated_at")

    update_job("phase0-job-1", status="running", progress=0.5, step="Halfway")
    second = get_job("phase0-job-1")
    assert second is not None
    assert second["progress"] == 0.5
    assert second.get("progress_updated_at")


@pytest.mark.asyncio
async def test_progress_watchdog_raises_on_stall():
    from app.workers.arq_worker import ProgressStalledError, _progress_watchdog

    update_job(
        "phase0-stall",
        status="running",
        progress=0.2,
        step="Stuck",
        progress_updated_at="2000-01-01T00:00:00+00:00",
    )
    with pytest.raises(ProgressStalledError):
        # Poll quickly with a tiny stall window
        await asyncio.wait_for(_progress_watchdog("phase0-stall", stall_minutes=0.001), timeout=2)


def test_worker_settings_job_timeout():
    from app.workers.arq_worker import WorkerSettings

    assert WorkerSettings.job_timeout >= 3600
    assert WorkerSettings.max_jobs == 1
