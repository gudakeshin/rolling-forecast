"""Behavioural checks for enhancement-plan items 2.2 and 3.1 that had no direct test."""

from __future__ import annotations

import numpy as np
import pandas as pd

from app.domain.engines.croston import _CROSTON_ALPHA_GRID, _TSB_ALPHA_GRID, _croston_sba, _tsb
from app.domain.engines.ets import ETSModel


def _proportional_seasonal(n: int = 48) -> tuple[pd.Series, pd.DatetimeIndex]:
    """Revenue whose seasonal swing grows with its level (the plan's motivating shape)."""
    t = np.arange(n)
    level = 1000.0 * (1.03 ** t)
    season = 1 + 0.35 * np.sin(2 * np.pi * t / 12)
    noise = 1 + np.random.default_rng(0).normal(0, 0.01, n)
    return pd.Series(level * season * noise), pd.DatetimeIndex(pd.date_range("2022-01", periods=n, freq="MS"))


def test_ets_grid_offers_multiplicative_and_log_specs_for_positive_data():
    specs = ETSModel()._candidate_specs(n=48, m=12, positive=True)
    assert any(s["error"] == "mul" for s in specs)
    assert any(s["seasonal"] == "mul" for s in specs)
    assert any(s["transform"] == "log" for s in specs)
    # Non-positive data must never get multiplicative or log components.
    neg = ETSModel()._candidate_specs(n=48, m=12, positive=False)
    assert not any(s["error"] == "mul" or s["seasonal"] == "mul" or s["transform"] == "log" for s in neg)


def test_ets_picks_a_proportional_spec_on_proportional_seasonality():
    values, dates = _proportional_seasonal()
    params = ETSModel().fit(values, dates)
    chosen = (params.get("error"), params.get("seasonal"), params.get("transform"))
    assert chosen[1] == "mul" or chosen[2] == "log" or chosen[0] == "mul", chosen


def test_croston_alpha_is_searched_not_fixed():
    # Demand that shifts level: a heavier weight on recent sizes fits better than 0.1.
    y = np.array([0, 5, 0, 0, 5, 0, 0, 0, 40, 0, 45, 0, 0, 50, 0, 48, 0, 0, 52, 0], dtype=float)
    *_, alpha = _croston_sba(y)
    assert alpha in _CROSTON_ALPHA_GRID and alpha != 0.1


def test_tsb_alpha_beta_are_searched():
    y = np.array([0, 0, 0, 3, 0, 0, 0, 0, 3, 0, 4, 4, 0, 5, 4, 0, 5, 5, 4, 5], dtype=float)
    *_, alpha, beta = _tsb(y)
    assert alpha in _TSB_ALPHA_GRID
    assert (alpha, beta) != (0.1, 0.1)


def test_scheduled_jobs_are_registered_with_the_worker():
    """Plan 3.1: the refresh, pull, report and sweep jobs run on a schedule."""
    from app.workers.arq_worker import WorkerSettings

    names = {job.name for job in WorkerSettings.cron_jobs}
    assert {
        "cron:scheduled_integration_sync",
        "cron:scheduled_driver_freshness_sweep",
        "cron:scheduled_accuracy_report",
        "cron:scheduled_driver_overdue_sweep",
        "cron:scheduled_anomaly_sweep",
    } <= names


# ── Plan 2.3: run_ensemble respects the fiscal calendar and the single
# confidence source of truth; the exog MASE scale follows the calendar. ──────

import pytest  # noqa: E402


@pytest.fixture
def fiscal_version(db_session):
    from app.models.actuals import ActualsDataset, ActualsRecord
    from app.models.forecast import ForecastLineResult, ForecastVersion
    from app.models.line_item import LineItem

    li = LineItem(account_code="FY-REV-1", name="Fiscal Revenue", category="Revenue")
    db_session.add(li)
    db_session.flush()
    labels = [f"FY{2024 + i // 12}-P{i % 12 + 1:02d}" for i in range(24)]
    ds = ActualsDataset(
        source_type="csv", source_name="fy.csv", file_hash="fyhash", row_count=24,
        period_start=labels[0], period_end=labels[-1], periods_count=24,
    )
    db_session.add(ds)
    db_session.flush()
    rng = np.random.default_rng(3)
    for i, p in enumerate(labels):
        db_session.add(ActualsRecord(
            dataset_id=ds.id, line_item_id=li.id, period=p,
            value=float(1000 + 20 * i + rng.normal(0, 15)), currency="USD",
        ))
    v = ForecastVersion(
        name="FY-v1", status="draft", version_type="scheduled", actuals_dataset_id=ds.id,
        horizon_months=3, base_period=labels[-1],
    )
    db_session.add(v)
    db_session.flush()
    for h, p in enumerate(["FY2026-P01", "FY2026-P02", "FY2026-P03"]):
        db_session.add(ForecastLineResult(
            version_id=v.id, line_item_id=li.id, period=p, p10=1400, p50=1500 + h, p90=1600,
            confidence_score=40, confidence_level="low", model_type="linear", model_mape=9.0,
        ))
    db_session.commit()
    return v


@pytest.mark.asyncio
async def test_run_ensemble_handles_fiscal_periods_and_uses_shared_confidence(
    db_session, context_manager, fiscal_version
):
    from app.domain.base_skill import SkillContext
    from app.domain.skills.run_ensemble import RunEnsembleSkill
    from app.models.forecast import ForecastLineResult
    from app.services.confidence import compute_confidence_score
    from app.services.period_calendar import CalendarType, FiscalCalendarConfig, push_calendar, reset_calendar

    token = push_calendar(FiscalCalendarConfig(CalendarType.FISCAL_445))
    try:
        ctx = SkillContext(
            db=db_session, context_manager=context_manager, user_id="t",
            user_role="admin", conversation_id="c",
        )
        result = await RunEnsembleSkill().execute(
            {"version_id": fiscal_version.id, "weighting_method": "equal", "top_k": 2}, ctx
        )
    finally:
        reset_calendar(token)

    # The old `pd.Timestamp(p + "-01")` raised on "FY2024-P01"-style labels.
    assert result.success, result.message
    rows = db_session.query(ForecastLineResult).filter_by(version_id=fiscal_version.id).all()
    assert rows
    for r in rows:
        # No ad-hoc "+10 ensemble bonus": confidence is exactly the shared score.
        assert r.confidence_score == pytest.approx(compute_confidence_score(r))


def test_exog_mase_scale_follows_the_fiscal_calendar(monkeypatch):
    """forecast_pipeline used a hardcoded m = 12 for the MASE denominator."""
    import inspect

    from app.services import forecast_pipeline

    src = inspect.getsource(forecast_pipeline)
    assert "m = 12" not in src
    assert "seasonal_period_length()" in src
