"""E5: an actual outside the band its closest prior vintage published is an anomaly."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from app.models.forecast import ForecastLineResult, ForecastVersion
from app.models.fx import ForecastAccuracyRecord
from app.services.vintage_anomalies import vintage_band_misses


def _version(db, vid: str, dataset_id=None) -> ForecastVersion:
    v = ForecastVersion(
        id=vid, name=vid, status="published", version_type="baseline", horizon_months=3,
        scenario="base", actuals_dataset_id=dataset_id,
    )
    db.add(v)
    db.flush()
    return v


def _rec(db, version_id, li_id, period, horizon, p10, p50, p90, actual, *, age_days=0):
    db.add(ForecastAccuracyRecord(
        version_id=version_id, line_item_id=li_id, period=period, horizon_offset=horizon,
        predicted_p10=p10, predicted_p50=p50, predicted_p90=p90, actual=actual,
        absolute_error=abs(actual - p50), within_p10_p90=p10 <= actual <= p90,
        created_at=datetime.now(timezone.utc) - timedelta(days=age_days),
    ))


def test_closest_vintage_decides_and_severity_scales(db_session, seed_line_items):
    li = seed_line_items["REV-001"]
    old, new = _version(db_session, "va-old"), _version(db_session, "va-new")
    # 2025-10: the 3-month-ahead band missed, but the 1-month-ahead one held -> no anomaly.
    _rec(db_session, old.id, li.id, "2025-10", 3, 90, 100, 110, 130)
    _rec(db_session, new.id, li.id, "2025-10", 1, 110, 125, 140, 130)
    # 2025-11: 1.5 band widths above -> critical.
    _rec(db_session, new.id, li.id, "2025-11", 1, 90, 100, 110, 140)
    # 2025-12: 0.25 widths below -> info.
    _rec(db_session, new.id, li.id, "2025-12", 1, 90, 100, 110, 85)
    db_session.commit()

    out = vintage_band_misses(db_session, [li.id])
    by_period = {f["period"]: f for f in out[li.id]}
    assert set(by_period) == {"2025-11", "2025-12"}
    assert by_period["2025-11"]["severity"] == "critical"
    assert by_period["2025-11"]["direction"] == "above"
    assert by_period["2025-11"]["band_widths_outside"] == 1.5
    assert by_period["2025-12"]["severity"] == "info"
    assert by_period["2025-12"]["direction"] == "below"
    assert vintage_band_misses(db_session, []) == {}


def test_only_recent_periods(db_session, seed_line_items):
    li = seed_line_items["REV-001"]
    v = _version(db_session, "va-recent")
    for m in range(1, 10):
        _rec(db_session, v.id, li.id, f"2025-{m:02d}", 1, 90, 100, 110, 200)
    db_session.commit()
    periods = [f["period"] for f in vintage_band_misses(db_session, [li.id], max_periods=3)[li.id]]
    assert periods == ["2025-07", "2025-08", "2025-09"]


def test_anomaly_dashboard_shows_the_miss(db_session, seed_users, seed_line_items, seed_actuals):
    from app.api.dashboard import get_anomaly_dashboard

    li = next(li for li in seed_line_items.values() if not li.is_calculated)
    prior = _version(db_session, "va-prior")
    _rec(db_session, prior.id, li.id, "2025-09", 1, 90_000, 100_000, 110_000, 150_000)
    current = _version(db_session, "va-current", seed_actuals.id)
    db_session.add(ForecastLineResult(
        id="va-flr", version_id=current.id, line_item_id=li.id, period="2026-01",
        p10=95_000, p50=100_000, p90=105_000, model_type="ets",
        confidence_score=85.0, confidence_level="high",
    ))
    db_session.commit()

    out = asyncio.run(
        get_anomaly_dashboard(version_id=current.id, current_user=seed_users["admin"], db=db_session)
    )
    item = next(a for a in out.data["anomalies"] if a["line_item_id"] == li.id)
    miss = next(f for f in item["findings"] if f["type"] == "vintage_band_miss")
    assert miss["severity"] == "critical" and miss["period"] == "2025-09"
    assert "above the P10-P90" in miss["detail"]
    assert any(a["label"] == "Explain the miss" for a in item["actions"])
