"""Phase 8 — exogenous regressor wiring tests."""

from __future__ import annotations

import numpy as np
import pandas as pd

from app.domain.engines.model_registry import ModelRegistry
from app.domain.skills.generate_baseline import forecast_linked_drivers
from app.models.driver import Driver, DriverLink
from app.models.line_item import LineItem
from app.services.driver_exog import build_exog_for_line
from app.services.driver_series import upsert_driver_values
from app.services.forecast_pipeline import LineForecastContext, forecast_line_item
from app.services.period_calendar import get_calendar_config


def test_build_exog_for_line_applies_lag_and_future_fill(db_session):
    li = LineItem(account_code="REV-EXOG-1", name="Exog Revenue", category="Revenue", display_order=1)
    db_session.add(li)
    db_session.flush()
    d = Driver(key="headcount_na", name="Headcount NA", driver_type="headcount")
    db_session.add(d)
    db_session.flush()
    upsert_driver_values(
        db_session,
        driver_id=d.id,
        rows=[
            {"period": "2024-01", "value": 100.0},
            {"period": "2024-02", "value": 102.0},
            {"period": "2024-03", "value": 105.0},
            {"period": "2024-04", "value": 108.0},
        ],
    )
    db_session.add(
        DriverLink(
            driver_id=d.id,
            line_item_id=li.id,
            relation="level",
            lag=1,
            status="active",
            link_type="manual",
            transform="level",
        )
    )
    db_session.commit()

    out = build_exog_for_line(
        db_session,
        line_item_id=li.id,
        train_periods=["2024-01", "2024-02", "2024-03", "2024-04"],
        future_periods=["2024-05", "2024-06"],
        version_id=None,
        max_links=3,
    )
    assert out is not None
    assert list(out.exog_train.columns) == [f"d{d.id}_lag1"]
    # lag=1 then conservative fill produces stable values
    assert float(out.exog_train.iloc[0, 0]) == 100.0
    assert float(out.exog_train.iloc[1, 0]) == 100.0
    # future is forward-filled from latest shifted value
    assert float(out.exog_future.iloc[0, 0]) == 108.0


def test_forecast_pipeline_stamps_exog_spec_when_enabled(db_session):
    li = LineItem(account_code="REV-EXOG-2", name="Exog Revenue 2", category="Revenue", display_order=2)
    db_session.add(li)
    db_session.flush()
    d = Driver(key="driver_x", name="Driver X", driver_type="macro")
    db_session.add(d)
    db_session.flush()

    periods = []
    vals = []
    drv = []
    for i in range(30):
        period = f"{2022 + (i // 12)}-{(i % 12) + 1:02d}"
        x = 100 + i
        y = 200 + 2.5 * x + np.random.default_rng(42 + i).normal(0, 2)
        periods.append(period)
        drv.append({"period": period, "value": float(x)})
        vals.append(float(y))
    upsert_driver_values(db_session, driver_id=d.id, rows=drv)
    db_session.add(
        DriverLink(
            driver_id=d.id,
            line_item_id=li.id,
            relation="level",
            lag=0,
            status="active",
            link_type="manual",
            transform="level",
        )
    )
    db_session.commit()

    values = pd.Series(vals, dtype=float)
    dates = pd.DatetimeIndex([pd.Timestamp(f"{p}-01") for p in periods])
    out = forecast_line_item(
        db_session,
        li,
        values,
        dates,
        periods,
        LineForecastContext(
            version_id="phase8-v1",
            horizon=6,
            random_seed=42,
            model_type="arima",
            selection_rule="mase_pinball_complexity",
            is_material=True,
            cal_cfg=get_calendar_config(),
            model_registry=ModelRegistry(),
            enable_driver_forecasting=True,
        ),
    )
    assert out.skipped is False
    assert out.selected_model == "arima"
    assert out.metadata_rows
    params = out.metadata_rows[0].parameters or {}
    assert "exog_spec" in params
    assert params["exog_spec"]["drivers"][0]["driver_id"] == d.id
    # Next-step metadata: compare exog modes for transparency
    assert "exog_mode_scores" in params["exog_spec"]
    assert params["exog_spec"]["exog_mode_scores"]["n_folds"] >= 0
    assert out.comparison is not None
    assert "exog_mode_scores" in out.comparison


def test_stage0_driver_forecast_writes_versioned_driver_values(db_session):
    li = LineItem(account_code="REV-EXOG-3", name="Exog Revenue 3", category="Revenue", display_order=3)
    db_session.add(li)
    db_session.flush()
    d = Driver(key="driver_y", name="Driver Y", driver_type="macro")
    db_session.add(d)
    db_session.flush()

    rows = []
    for i in range(24):
        period = f"{2023 + (i // 12)}-{(i % 12) + 1:02d}"
        rows.append({"period": period, "value": float(50 + i)})
    upsert_driver_values(db_session, driver_id=d.id, rows=rows)
    db_session.add(
        DriverLink(
            driver_id=d.id,
            line_item_id=li.id,
            relation="level",
            lag=2,
            status="active",
            link_type="manual",
            transform="level",
        )
    )
    db_session.commit()

    out = forecast_linked_drivers(
        db_session,
        version_id="phase8-v2",
        horizon=6,
        random_seed=42,
        model_registry=ModelRegistry(),
        cal_cfg=get_calendar_config(),
    )
    assert out["drivers_considered"] >= 1
    assert out["drivers_forecasted"] >= 1
    # horizon + max_lag
    assert out["rows_written"] >= 8

