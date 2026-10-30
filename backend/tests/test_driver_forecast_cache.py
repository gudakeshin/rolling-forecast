"""Regression test: driver-forecast cache must not outlive the data it was built from.

forecast_driver_at_origin's cache used to key purely on
(driver_id, train_end_period, horizon) with no way to notice that the
underlying driver actuals had changed underneath it. Nothing in the app ever
called clear_driver_forecast_cache() either, so once a driver's forecast at a
given origin was computed, a later re-upload or correction of that same
history kept serving the original stale forecast for the life of the process.
"""

from __future__ import annotations

import pandas as pd

from app.domain.engines.model_registry import ModelRegistry
from app.services.driver_forecast_cache import forecast_driver_at_origin
from app.services.driver_series import create_driver, upsert_driver_values
from app.services.period_calendar import get_calendar_config


def _periods(n: int, start_year: int = 2023) -> list[str]:
    return [f"{start_year + (m // 12)}-{(m % 12) + 1:02d}" for m in range(n)]


def test_forecast_driver_at_origin_reflects_reuploaded_data(db_session, seed_users):
    d = create_driver(db_session, key="drv_reupload", name="Reupload Driver", driver_type="macro", actor=seed_users["admin"])
    db_session.flush()

    periods = _periods(18)
    flat_rows = [{"period": p, "value": 100.0} for p in periods]
    upsert_driver_values(db_session, driver_id=d.id, rows=flat_rows)
    db_session.commit()

    train_end = periods[11]
    future = periods[12:15]

    first = forecast_driver_at_origin(
        db_session,
        driver_id=d.id,
        train_end_period=train_end,
        future_periods=future,
        registry=ModelRegistry(),
        cal_cfg=get_calendar_config(),
        random_seed=42,
    )
    assert not first.empty
    assert first.round(3).tolist() == [100.0, 100.0, 100.0]

    # The analyst shares corrected/fresh data for the exact same history —
    # a sharp trend instead of a flat line — without any explicit cache clear.
    trending_rows = [
        {"period": p, "value": 100.0 + 50.0 * i} for i, p in enumerate(periods[:12])
    ]
    upsert_driver_values(db_session, driver_id=d.id, rows=trending_rows)
    db_session.commit()

    second = forecast_driver_at_origin(
        db_session,
        driver_id=d.id,
        train_end_period=train_end,
        future_periods=future,
        registry=ModelRegistry(),
        cal_cfg=get_calendar_config(),
        random_seed=42,
    )
    assert not second.empty
    # A forecast fit on a driver rising ~50/month must not match the flat-100
    # forecast from before the re-upload — proves the cache missed rather than
    # replaying the first run's result.
    assert second.iloc[0] > first.iloc[0] + 25
