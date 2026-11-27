"""Phase B forecasting prerequisites — regression tests.

Each block pins one correctness fix that would otherwise bias a model
comparison (and, later, the Chronos-2 foundation-model backtest):

1. missing periods are reindexed onto the fiscal calendar and flagged;
2. driver history: plan values never overwrite actuals, and the window is
   never backfilled from later values;
3. the FVA naive benchmark uses the last actual at the forecast origin;
4. seasonal period comes from the fiscal calendar, not a hard-coded 12;
5. CPU-heavy fits run off the event loop, several jobs at once.
"""

from __future__ import annotations

import pandas as pd
import pytest

from app.domain.skills.generate_baseline import reindex_to_fiscal_grid
from app.services.period_calendar import CalendarType, FiscalCalendarConfig


# ── 1. Reindex missing periods ─────────────────────────────────────────────


def test_reindexing_fills_and_flags_a_missing_month():
    periods = ["2024-01", "2024-02", "2024-04", "2024-05"]
    values = [100.0, 110.0, 130.0, 140.0]

    grid, filled, missing = reindex_to_fiscal_grid(periods, values)

    assert grid == ["2024-01", "2024-02", "2024-03", "2024-04", "2024-05"]
    assert missing == ["2024-03"]
    assert filled == pytest.approx([100.0, 110.0, 120.0, 130.0, 140.0])


def test_reindexing_is_a_no_op_on_a_complete_series():
    periods = ["2024-01", "2024-02", "2024-03"]
    grid, filled, missing = reindex_to_fiscal_grid(periods, [1.0, 2.0, 3.0])
    assert grid == periods and filled == [1.0, 2.0, 3.0] and missing == []


def test_reindexing_follows_the_445_calendar_across_a_year_end():
    cfg = FiscalCalendarConfig(CalendarType.FISCAL_445)
    periods = ["FY2024-P11", "FY2025-P01", "FY2025-P02"]

    grid, filled, missing = reindex_to_fiscal_grid(periods, [10.0, 30.0, 40.0], cfg)

    assert grid == ["FY2024-P11", "FY2024-P12", "FY2025-P01", "FY2025-P02"]
    assert missing == ["FY2024-P12"]
    assert filled[1] == pytest.approx(20.0)


def test_reindexing_never_extends_beyond_the_observed_span():
    grid, _filled, missing = reindex_to_fiscal_grid(["2024-03", "2024-06"], [3.0, 6.0])
    assert grid[0] == "2024-03" and grid[-1] == "2024-06"
    assert missing == ["2024-04", "2024-05"]


def _write_gappy_actuals_csv(path, seed_line_items) -> None:
    rows = []
    for code, li in seed_line_items.items():
        if li.is_calculated:
            continue
        for month in range(24):
            if month == 5:  # 2024-06 never loaded
                continue
            year = 2024 + month // 12
            m = (month % 12) + 1
            rows.append({
                "account_code": code,
                "account_name": li.name,
                "category": li.category,
                "period": f"{year}-{m:02d}",
                "value": 1000.0 + month * 10,
                "currency": "USD",
            })
    pd.DataFrame(rows).to_csv(path, index=False)


@pytest.mark.asyncio
async def test_generate_baseline_flags_lines_with_a_missing_month(
    db_session, seed_line_items, skill_context, tmp_path
):
    from app.domain.skills.generate_baseline import GenerateBaselineSkill
    from app.domain.skills.ingest_actuals import IngestActualsSkill

    csv_path = tmp_path / "gappy.csv"
    _write_gappy_actuals_csv(csv_path, seed_line_items)
    ingested = await IngestActualsSkill().execute({"file_path": str(csv_path)}, skill_context)
    assert ingested.success, ingested.message

    result = await GenerateBaselineSkill().execute(
        {"horizon_months": 3, "model_type": "linear", "random_seed": 42, "skip_plan_check": True},
        skill_context,
    )
    assert result.success, result.message
    assert result.data["gap_filled_lines"] >= 1
    assert result.data["gap_filled_lines"] == result.data["lines_forecasted"]


# ── 2. Driver history: no plan overwrite, no backfill ─────────────────────


def _driver_line(db_session, key: str, lag: int):
    from app.models.driver import Driver, DriverLink
    from app.models.line_item import LineItem

    li = LineItem(account_code=f"PB-{key}", name=f"PhaseB {key}", category="Revenue", display_order=1)
    db_session.add(li)
    db_session.flush()
    d = Driver(key=key, name=key, driver_type="macro")
    db_session.add(d)
    db_session.flush()
    db_session.add(
        DriverLink(
            driver_id=d.id, line_item_id=li.id, relation="level", lag=lag,
            status="active", link_type="manual", transform="level",
        )
    )
    db_session.flush()
    return li, d


def test_plan_values_never_overwrite_driver_actuals(db_session):
    from app.services.driver_exog import build_exog_for_line
    from app.services.driver_series import upsert_driver_values

    li, d = _driver_line(db_session, "pb_plan_vs_actual", lag=0)
    upsert_driver_values(
        db_session, driver_id=d.id,
        rows=[{"period": f"2024-0{m}", "value": 100.0 + m} for m in range(1, 5)],
    )
    # The budget covers the whole year, history included.
    upsert_driver_values(
        db_session, driver_id=d.id, value_type="plan", version_id="pb-v1",
        rows=[{"period": f"2024-0{m}", "value": 999.0} for m in range(1, 7)],
    )
    db_session.commit()

    out = build_exog_for_line(
        db_session, line_item_id=li.id,
        train_periods=["2024-01", "2024-02", "2024-03", "2024-04"],
        future_periods=["2024-05", "2024-06"],
        version_id="pb-v1",
    )
    assert out is not None
    assert out.exog_train.iloc[:, 0].tolist() == [101.0, 102.0, 103.0, 104.0]
    # The plan still supplies the future, where there is no actual.
    assert out.exog_future.iloc[:, 0].tolist() == [999.0, 999.0]


def test_plan_outranks_a_stored_driver_forecast_for_future_periods(db_session):
    from app.services.driver_exog import build_exog_for_line
    from app.services.driver_series import upsert_driver_values

    li, d = _driver_line(db_session, "pb_plan_vs_fc", lag=0)
    upsert_driver_values(
        db_session, driver_id=d.id,
        rows=[{"period": f"2024-0{m}", "value": float(m)} for m in range(1, 4)],
    )
    upsert_driver_values(
        db_session, driver_id=d.id, value_type="forecast", version_id="pb-v2",
        rows=[{"period": "2024-04", "value": 40.0}],
    )
    upsert_driver_values(
        db_session, driver_id=d.id, value_type="plan", version_id="pb-v2",
        rows=[{"period": "2024-04", "value": 44.0}],
    )
    db_session.commit()

    out = build_exog_for_line(
        db_session, line_item_id=li.id,
        train_periods=["2024-01", "2024-02", "2024-03"], future_periods=["2024-04"],
        version_id="pb-v2",
    )
    assert out is not None
    assert out.exog_future.iloc[0, 0] == 44.0


def test_the_driver_window_has_no_backfill_from_later_values(db_session):
    from app.services.driver_exog import build_exog_for_line
    from app.services.driver_series import upsert_driver_values

    # Driver history only starts mid-window: the old ffill().bfill() copied
    # the 2024-04 value into 2024-01..03.
    li, d = _driver_line(db_session, "pb_late_driver", lag=0)
    upsert_driver_values(
        db_session, driver_id=d.id,
        rows=[{"period": f"2024-0{m}", "value": 10.0 * m} for m in range(4, 7)],
    )
    db_session.commit()

    out = build_exog_for_line(
        db_session, line_item_id=li.id,
        train_periods=[f"2024-0{m}" for m in range(1, 7)], future_periods=["2024-07"],
        version_id=None,
    )
    if out is not None:
        assert f"d{d.id}_lag0" not in out.exog_train.columns
        assert all(sp.driver_id != d.id for sp in out.specs)


def test_lagged_driver_reads_real_history_before_the_window(db_session):
    from app.services.driver_exog import build_exog_for_line
    from app.services.driver_series import upsert_driver_values

    li, d = _driver_line(db_session, "pb_lag_history", lag=2)
    rows = [{"period": "2023-11", "value": 7.0}, {"period": "2023-12", "value": 8.0}]
    rows += [{"period": f"2024-0{m}", "value": float(m)} for m in range(1, 5)]
    upsert_driver_values(db_session, driver_id=d.id, rows=rows)
    db_session.commit()

    out = build_exog_for_line(
        db_session, line_item_id=li.id,
        train_periods=[f"2024-0{m}" for m in range(1, 5)], future_periods=["2024-05"],
        version_id=None,
    )
    assert out is not None
    # driver(t-2): 2023-11, 2023-12, 2024-01, 2024-02
    assert out.exog_train.iloc[:, 0].tolist() == [7.0, 8.0, 1.0, 2.0]
    assert out.exog_future.iloc[0, 0] == 3.0


# ── 3. FVA naive benchmark at the forecast origin ─────────────────────────


def test_naive_benchmark_uses_the_last_actual_at_the_origin(db_session):
    from app.models.actuals import ActualsDataset, ActualsRecord
    from app.models.forecast import ForecastLineResult, ForecastVersion
    from app.models.fx import ForecastAccuracyRecord
    from app.models.line_item import LineItem
    from app.services.accuracy_snapshot import AccuracySnapshotService

    li = LineItem(account_code="PB-FVA", name="PhaseB FVA", category="Revenue", display_order=1)
    db_session.add(li)
    db_session.flush()

    hist = ActualsDataset(
        source_type="csv", source_name="pb-hist.csv", file_hash="pb-hist",
        period_start="2023-01", period_end="2024-06", periods_count=18, row_count=18,
    )
    db_session.add(hist)
    db_session.flush()
    for i in range(18):  # 2023-01 .. 2024-06, value = 100 + i
        period = f"{2023 + i // 12}-{i % 12 + 1:02d}"
        db_session.add(ActualsRecord(
            dataset_id=hist.id, line_item_id=li.id, period=period,
            value=100.0 + i, currency="USD",
        ))

    version = ForecastVersion(
        name="PB-FVA-v1", status="draft", version_type="scheduled",
        base_period="2024-06", horizon_months=3,
    )
    db_session.add(version)
    db_session.flush()
    targets = ["2024-07", "2024-08", "2024-09"]
    for p in targets:
        db_session.add(ForecastLineResult(
            version_id=version.id, line_item_id=li.id, period=p,
            p10=100.0, p50=120.0, p90=140.0, model_type="linear",
        ))

    new = ActualsDataset(
        source_type="csv", source_name="pb-new.csv", file_hash="pb-new",
        period_start="2024-07", period_end="2024-09", periods_count=3, row_count=3,
    )
    db_session.add(new)
    db_session.flush()
    for j, p in enumerate(targets):
        db_session.add(ActualsRecord(
            dataset_id=new.id, line_item_id=li.id, period=p,
            value=200.0 + j, currency="USD",
        ))
    db_session.commit()

    AccuracySnapshotService(db_session).on_actuals_ingested(new.id)
    db_session.commit()

    recs = {
        r.period: r
        for r in db_session.query(ForecastAccuracyRecord)
        .filter(ForecastAccuracyRecord.version_id == version.id)
        .all()
    }
    assert set(recs) == set(targets)
    origin_actual = 117.0  # 2024-06
    for p in targets:
        # Flat from the origin for every horizon — never the post-origin
        # actual just before the target (200 for 2024-08, 201 for 2024-09).
        assert recs[p].naive_p50 == origin_actual
    # Seasonal naive: same period last year, all of it at or before the origin.
    assert recs["2024-07"].seasonal_naive_p50 == 106.0
    assert recs["2024-09"].seasonal_naive_p50 == 108.0


# ── 4. Seasonal period comes from the fiscal calendar ─────────────────────


def test_outlier_stl_uses_the_calendar_seasonal_period(monkeypatch):
    import numpy as np

    import app.domain.engines.base_model as base_model
    from app.services.outlier_cleaning import detect_outliers_stl_mad

    monkeypatch.setattr(base_model, "seasonal_period_length", lambda: 4)
    # 10 points: two 4-period cycles is enough for STL, two 12-period cycles is not.
    rng = np.random.default_rng(0)
    t = np.arange(10)
    values = pd.Series(100.0 + np.array([-6.0, -2.0, 2.0, 6.0])[t % 4] + rng.normal(0, 1.0, 10))
    dates = pd.date_range("2024-01-01", periods=10, freq="MS")

    out = detect_outliers_stl_mad(values, dates)

    assert out.method == "stl_mad"
    assert out.details["seasonal_period"] == 4


def test_outlier_stl_explicit_period_still_wins():
    import numpy as np

    from app.services.outlier_cleaning import detect_outliers_stl_mad

    rng = np.random.default_rng(1)
    t = np.arange(10)
    values = pd.Series(100.0 + np.array([-6.0, -2.0, 2.0, 6.0])[t % 4] + rng.normal(0, 1.0, 10))
    dates = pd.date_range("2024-01-01", periods=10, freq="MS")
    out = detect_outliers_stl_mad(values, dates, seasonal_period=4)
    assert out.details["seasonal_period"] == 4


def test_prophet_seasonality_period_follows_the_calendar(monkeypatch):
    pytest.importorskip("prophet")
    import numpy as np

    import app.domain.engines.prophet_model as prophet_model

    monkeypatch.setattr(prophet_model, "seasonal_period_length", lambda: 4)
    n = 12  # >= 2 cycles of 4, < 2 cycles of 12
    y = pd.Series(100.0 + np.tile([0.0, 5.0, 10.0, 5.0], 3) + np.arange(n))
    dates = pd.date_range("2023-01-01", periods=n, freq="MS")

    model = prophet_model.ProphetModel()
    params = model.fit(y, dates)
    assert params.get("yearly_seasonality") is True
    out = model.predict(params, 3, dates[-1])
    assert out.diagnostics["seasonality_period"] == 4


# ── 5. CPU-heavy fits off the event loop, in parallel ─────────────────────


async def _ingest_two_years(skill_context, seed_line_items, tmp_path, name: str):
    from app.domain.skills.ingest_actuals import IngestActualsSkill

    rows = []
    for code, li in seed_line_items.items():
        if li.is_calculated:
            continue
        for month in range(24):
            rows.append({
                "account_code": code, "account_name": li.name, "category": li.category,
                "period": f"{2024 + month // 12}-{month % 12 + 1:02d}",
                "value": 1000.0 + 25.0 * month + (month % 12) * 7.0,
                "currency": "USD",
            })
    path = tmp_path / name
    pd.DataFrame(rows).to_csv(path, index=False)
    out = await IngestActualsSkill().execute({"file_path": str(path)}, skill_context)
    assert out.success, out.message


def _p50_by_line_period(db_session, version_id: str) -> dict[tuple[int, str], float]:
    from app.models.forecast import ForecastLineResult

    rows = db_session.query(ForecastLineResult).filter(
        ForecastLineResult.version_id == version_id
    ).all()
    return {(r.line_item_id, r.period): round(float(r.p50), 6) for r in rows}


@pytest.mark.asyncio
async def test_parallel_line_fits_match_the_serial_run(
    db_session, db_engine, seed_line_items, skill_context, tmp_path, monkeypatch
):
    from concurrent.futures import ThreadPoolExecutor

    from sqlalchemy.orm import sessionmaker

    import app.database as database
    from app.domain.skills.generate_baseline import GenerateBaselineSkill
    from app.services import fit_parallel

    await _ingest_two_years(skill_context, seed_line_items, tmp_path, "par.csv")
    params = {"horizon_months": 3, "model_type": "auto", "random_seed": 7, "skip_plan_check": True}

    serial = await GenerateBaselineSkill().execute(params, skill_context)
    assert serial.success, serial.message

    # Pool children open their own session; point it at the unit-test DB.
    monkeypatch.setattr(database, "SessionLocal", sessionmaker(bind=db_engine))
    calls: list[int] = []
    real_fit = fit_parallel.fit_line_task

    def counting_fit(task):
        calls.append(task.line_item_id)
        return real_fit(task)

    monkeypatch.setattr(fit_parallel, "fit_line_task", counting_fit)
    pool = ThreadPoolExecutor(max_workers=3)
    fit_parallel.set_fit_executor(pool)
    try:
        parallel = await GenerateBaselineSkill().execute(params, skill_context)
    finally:
        fit_parallel.set_fit_executor(None)
        pool.shutdown(wait=True)
    assert parallel.success, parallel.message

    assert len(calls) == serial.data["lines_forecasted"]  # every line went through the pool
    a = _p50_by_line_period(db_session, serial.data["version_id"])
    b = _p50_by_line_period(db_session, parallel.data["version_id"])
    assert a and a == b


def test_line_fit_task_survives_a_real_spawned_process(db_session):
    """The task (context, registry, analysis settings) pickles into a spawned
    child, which opens its own session and returns a LineForecast."""
    import pickle

    from app.domain.engines.model_registry import registry_for_analysis
    from app.services import fit_parallel
    from app.services.analysis_profile import resolve_analysis_settings
    from app.services.forecast_pipeline import LineForecastContext
    from app.services.period_calendar import get_calendar_config

    analysis = resolve_analysis_settings(db_session, business_unit_id=None)
    ctx = LineForecastContext(
        version_id="pb-spawn", horizon=3, cal_cfg=get_calendar_config(),
        model_registry=registry_for_analysis(analysis), analysis=analysis,
    )
    task = fit_parallel.make_task(
        line_item_id=-424242,  # does not exist in any DB
        values=[1.0, 2.0, 3.0], dates=pd.date_range("2024-01-01", periods=3, freq="MS"),
        periods=["2024-01", "2024-02", "2024-03"], is_material=False, ctx=ctx,
    )
    assert pickle.loads(pickle.dumps(task)).ctx.horizon == 3

    # The child opens its own session on the app database (DATABASE_URL), not
    # this test's in-memory one. Create its tables here rather than relying on
    # an earlier TestClient test having started the app (test order varies).
    from app.database import Base, engine

    Base.metadata.create_all(engine)

    pool = fit_parallel.create_fit_pool(1)
    try:
        out = pool.submit(fit_parallel.fit_line_task, task).result(timeout=120)
    finally:
        pool.shutdown(wait=True)
    assert out.skipped and out.skip_reason == "line_item_not_found"


@pytest.mark.asyncio
async def test_a_cancelled_job_stops_between_lines(
    db_session, seed_line_items, skill_context, tmp_path
):
    from app.domain.skills.generate_baseline import GenerateBaselineSkill
    from app.services.fit_parallel import clear_cancel, request_cancel

    await _ingest_two_years(skill_context, seed_line_items, tmp_path, "cancel.csv")
    request_cancel("pb-cancel-job")
    try:
        result = await GenerateBaselineSkill().execute(
            {"horizon_months": 3, "model_type": "linear", "random_seed": 1,
             "skip_plan_check": True, "_job_id": "pb-cancel-job"},
            skill_context,
        )
    finally:
        clear_cancel("pb-cancel-job")
    assert result.data["partial"] is True
    assert result.data["lines_completed"] == 0


@pytest.mark.asyncio
async def test_baseline_job_does_not_block_the_event_loop(monkeypatch):
    import asyncio
    import time

    import app.workers.arq_worker as worker

    async def cpu_bound(job_id, params, user_id):
        time.sleep(0.6)  # synchronous work, like a real fit loop
        return {"success": True, "message": "ok", "data": {}}

    monkeypatch.setattr(worker, "_execute_baseline", cpu_bound)
    ticks = 0

    async def ticker():
        nonlocal ticks
        while True:
            await asyncio.sleep(0.05)
            ticks += 1

    t = asyncio.create_task(ticker())
    try:
        out = await worker.run_generate_baseline({}, "pb-loop-job", {}, None)
    finally:
        t.cancel()
    assert out["success"] is True
    assert ticks >= 5  # the loop kept running while the job computed


@pytest.mark.asyncio
async def test_a_stalled_job_is_told_to_stop(monkeypatch):
    import time

    import app.workers.arq_worker as worker
    from app.config import settings
    from app.services.fit_parallel import is_cancelled
    from app.services.job_queue import update_job

    seen_cancel: list[bool] = []

    async def hung(job_id, params, user_id):
        update_job(job_id, status="running", progress=0.2, step="stuck",
                   progress_updated_at="2000-01-01T00:00:00+00:00")
        deadline = time.time() + 5
        while time.time() < deadline and not is_cancelled(job_id):
            time.sleep(0.02)
        seen_cancel.append(is_cancelled(job_id))
        return {"success": False, "message": "stopped", "data": {}}

    monkeypatch.setattr(worker, "_execute_baseline", hung)
    monkeypatch.setattr(settings, "job_progress_stall_minutes", 0.001)

    with pytest.raises(worker.ProgressStalledError):
        await worker.run_generate_baseline({}, "pb-stall-job", {}, None)
    # Give the worker thread a moment to observe the flag.
    deadline = time.time() + 5
    while not seen_cancel and time.time() < deadline:
        time.sleep(0.02)
    assert seen_cancel == [True]


def test_worker_creates_and_shuts_down_the_fit_pool(monkeypatch):
    import asyncio

    import app.workers.arq_worker as worker
    from app.services import fit_parallel

    monkeypatch.setenv("FORECAST_FIT_PROCESSES", "1")
    ctx: dict = {}
    asyncio.run(worker.WorkerSettings.on_startup(ctx))
    try:
        assert fit_parallel.get_fit_executor() is ctx["fit_pool"]
        assert worker.WorkerSettings.max_jobs > 1
    finally:
        asyncio.run(worker.WorkerSettings.on_shutdown(ctx))
    assert fit_parallel.get_fit_executor() is None
