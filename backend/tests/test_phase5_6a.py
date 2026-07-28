"""Phase 5 — driver CSV ingest + freshness; Phase 6a — variance attribution."""

from __future__ import annotations

import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.models.actuals import ActualsDataset, ActualsRecord
from app.models.forecast import ForecastVersion
from app.models.fx import FxRate
from app.models.override import Override
from app.services.driver_ingest import (
    driver_freshness,
    ingest_drivers_file,
    persist_macro_as_driver,
)
from app.services.driver_series import create_driver, upsert_driver_values
from app.services.variance_attribution import attribute_variance, attribute_bridge_row


@pytest.mark.asyncio
async def test_ingest_drivers_csv(db_session, seed_users, tmp_path):
    csv_path = tmp_path / "drivers.csv"
    csv_path.write_text(
        "driver_key,period,value,currency,driver_type,name\n"
        "units_na,2024-01,100,USD,volume,Units NA\n"
        "units_na,2024-02,110,USD,volume,Units NA\n"
        "units_na,2024-03,120,USD,volume,Units NA\n"
        "hc_emea,2024-01,50,,headcount,Headcount EMEA\n"
    )
    result = await ingest_drivers_file(
        db_session,
        file_path=str(csv_path),
        actor=seed_users["admin"],
        source_name="drivers.csv",
    )
    assert result["success"] is True
    assert result["drivers_created"] == 2
    assert result["row_count"] == 4
    assert result["file_hash"]

    from app.models.driver import Driver
    from app.services.driver_series import materialize_driver_series

    d = db_session.query(Driver).filter(Driver.key == "units_na").first()
    assert d is not None
    assert d.driver_type == "volume"
    assert d.source == "csv"
    series = materialize_driver_series(db_session, driver_id=d.id)
    assert series["2024-02"] == pytest.approx(110.0)

    fresh = driver_freshness(db_session, d.id)
    assert fresh["stale"] is False
    assert fresh["last_period"] == "2024-03"


@pytest.mark.asyncio
async def test_ingest_rejects_missing_columns(db_session, seed_users, tmp_path):
    csv_path = tmp_path / "bad.csv"
    csv_path.write_text("foo,bar\n1,2\n")
    result = await ingest_drivers_file(
        db_session, file_path=str(csv_path), actor=seed_users["admin"]
    )
    assert result["success"] is False
    assert "Missing required columns" in result["error"]


def test_persist_macro_as_driver(db_session, seed_users):
    meta = persist_macro_as_driver(
        db_session,
        key="fred_unrate",
        name="Unemployment Rate",
        source="fred",
        rows=[
            {"period": "2024-01", "value": 3.7},
            {"period": "2024-02", "value": 3.9},
        ],
        actor=seed_users["admin"],
    )
    db_session.commit()
    assert meta["created"] is True
    assert meta["n_rows"] == 2
    assert meta["freshness"]["stale"] is False

    # Idempotent re-persist
    meta2 = persist_macro_as_driver(
        db_session,
        key="fred_unrate",
        name="Unemployment Rate",
        source="fred",
        rows=[{"period": "2024-02", "value": 4.0}, {"period": "2024-03", "value": 4.1}],
        actor=seed_users["admin"],
    )
    db_session.commit()
    assert meta2["created"] is False
    from app.services.driver_series import materialize_driver_series

    series = materialize_driver_series(db_session, driver_id=meta["driver_id"])
    assert series["2024-02"] == pytest.approx(4.0)
    assert series["2024-03"] == pytest.approx(4.1)


def test_stale_driver_flag(db_session, seed_users):
    d = create_driver(
        db_session, key="stale_one", name="Stale", driver_type="macro", actor=seed_users["admin"]
    )
    db_session.flush()
    upsert_driver_values(
        db_session,
        driver_id=d.id,
        rows=[{"period": "2023-01", "value": 1.0}],
        actor=seed_users["admin"],
    )
    db_session.commit()
    from app.models.driver import DriverValue

    row = db_session.query(DriverValue).filter(DriverValue.driver_id == d.id).first()
    row.ingested_at = datetime.now(timezone.utc) - timedelta(days=120)
    db_session.commit()
    fresh = driver_freshness(db_session, d.id, stale_days=90)
    assert fresh["stale"] is True
    assert fresh["age_days"] >= 90


def test_fx_only_attribution(db_session, seed_line_items):
    li = seed_line_items["REV-001"]
    ds = ActualsDataset(
        source_type="test",
        source_name="fx",
        file_hash="fxhash",
        row_count=2,
        period_start="2024-01",
        period_end="2024-02",
        periods_count=2,
    )
    db_session.add(ds)
    db_session.flush()
    db_session.add_all([
        ActualsRecord(
            dataset_id=ds.id, line_item_id=li.id, period="2024-01", value=100.0, currency="EUR"
        ),
        ActualsRecord(
            dataset_id=ds.id, line_item_id=li.id, period="2024-02", value=100.0, currency="EUR"
        ),
        FxRate(from_currency="EUR", to_currency="USD", period="2024-01", rate=1.10),
        FxRate(from_currency="EUR", to_currency="USD", period="2024-02", rate=1.20),
    ])
    db_session.commit()

    # Force reporting currency USD via SystemSetting if needed — default is USD
    attr = attribute_variance(
        db_session,
        line_item_id=li.id,
        period_from="2024-01",
        period_to="2024-02",
        basis="fx_only",
    )
    assert attr["method"] == "fx_only"
    assert attr["buckets"]["fx"] == pytest.approx(10.0)  # 100 * (1.2 - 1.1)
    assert attr["buckets"]["constant_currency"] == pytest.approx(0.0)
    assert "unattributed" in attr["buckets"]
    assert attr["explained_pct"] > 0
    assert attr["waterfall"]["chart_type"] == "bridge"


def test_override_reason_text_no_keyword_buckets(db_session, seed_users, seed_line_items):
    li = seed_line_items["REV-001"]
    version = ForecastVersion(
        name="v1",
        status="draft",
        created_by=seed_users["analyst"].id,
        horizon_months=3,
    )
    db_session.add(version)
    db_session.flush()
    db_session.add(
        Override(
            version_id=version.id,
            line_item_id=li.id,
            period="2024-06",
            original_model_value=1000.0,
            override_value=1200.0,
            reason="volume growth from new logo wins this quarter",
            user_id=seed_users["analyst"].id,
            status="active",
        )
    )
    db_session.commit()

    attr = attribute_variance(
        db_session,
        line_item_id=li.id,
        version_id=version.id,
        basis="override_reason_text",
    )
    assert attr["method"] == "override_reason_text"
    assert attr["explained_pct"] == 0.0
    assert attr["buckets"]["unattributed"] == pytest.approx(200.0)
    assert "volume" not in attr["buckets"]
    assert attr["overrides"][0]["reason"].startswith("volume growth")

    bridge = attribute_bridge_row(
        db_session,
        line_item_id=li.id,
        version_id=version.id,
        variance_vs_prior=200.0,
    )
    assert bridge["explained_pct"] == 0.0
    assert bridge["buckets"]["unattributed"] == pytest.approx(200.0)


def test_keyword_bucketing_retired_in_service(db_session, seed_users, seed_line_items):
    """Even with 'price' / 'fx' in the reason, all delta is unattributed."""
    li = seed_line_items["REV-001"]
    version = ForecastVersion(
        name="v2",
        status="draft",
        created_by=seed_users["analyst"].id,
        horizon_months=3,
    )
    db_session.add(version)
    db_session.flush()
    db_session.add(
        Override(
            version_id=version.id,
            line_item_id=li.id,
            period="2024-07",
            original_model_value=500.0,
            override_value=450.0,
            reason="price compression and fx headwind on EUR bookings",
            user_id=seed_users["analyst"].id,
            status="active",
        )
    )
    db_session.commit()
    attr = attribute_variance(
        db_session,
        line_item_id=li.id,
        version_id=version.id,
        basis="override",
    )
    assert set(attr["buckets"].keys()) == {"unattributed"}
    assert attr["buckets"]["unattributed"] == pytest.approx(-50.0)
