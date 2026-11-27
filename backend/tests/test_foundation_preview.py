"""E4: instant Chronos-2 preview of an uploaded actuals dataset."""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.models.actuals import ActualsDataset, ActualsRecord
from app.models.line_item import LineItem
from app.services import foundation_client
from app.services.foundation_client import (
    FoundationBatchResult,
    FoundationServiceUnavailable,
    QuantilePath,
)
from app.services.foundation_preview import build_preview


@pytest.fixture
def dataset(db_session, seed_business_unit):
    lines = [
        LineItem(account_code="PV-REV", name="Preview revenue", category="Revenue",
                 business_unit_id=seed_business_unit.id),
        LineItem(account_code="PV-NEW", name="Preview new", category="Revenue",
                 business_unit_id=seed_business_unit.id),
        LineItem(account_code="PV-ZERO", name="Preview zero", category="Opex",
                 business_unit_id=seed_business_unit.id),
    ]
    db_session.add_all(lines)
    ds = ActualsDataset(
        source_type="upload", source_name="preview.csv", file_hash="preview-hash", row_count=0,
        period_start="2024-01", period_end="2025-12", periods_count=24,
        business_unit_id=seed_business_unit.id,
    )
    db_session.add(ds)
    db_session.flush()
    for i in range(24):
        p = f"{2024 + i // 12}-{i % 12 + 1:02d}"
        db_session.add(ActualsRecord(dataset_id=ds.id, line_item_id=lines[0].id, period=p, value=100.0 + i))
        db_session.add(ActualsRecord(dataset_id=ds.id, line_item_id=lines[2].id, period=p, value=0.0))
    for p in ("2025-11", "2025-12"):  # two points: below the 3-point minimum
        db_session.add(ActualsRecord(dataset_id=ds.id, line_item_id=lines[1].id, period=p, value=50.0))
    db_session.commit()
    return ds, lines


def test_preview_is_one_call_and_skips_what_it_cannot_forecast(db_session, dataset, monkeypatch):
    ds, lines = dataset
    calls = []

    def fake(series):
        calls.append(series)
        return FoundationBatchResult(
            provenance={"model_id": "amazon/chronos-2"},
            forecasts={
                s["id"]: QuantilePath(p10=(110.0,) * 3, p50=(124.0, 125.0, 126.0), p90=(140.0,) * 3)
                for s in series
            },
        )

    monkeypatch.setattr(foundation_client, "forecast_batch", fake)
    out = build_preview(db_session, ds, horizon=3)
    assert len(calls) == 1 and [s["id"] for s in calls[0]] == [str(lines[0].id)]
    assert out["available"] is True and out["preview"] is True
    (line,) = out["lines"]
    assert line["line_item_name"] == "Preview revenue"
    assert [f["period"] for f in line["forecast"]] == ["2026-01", "2026-02", "2026-03"]
    assert line["total_p50"] == pytest.approx(375.0)
    assert line["last_12_actual"] == pytest.approx(sum(100.0 + i for i in range(12, 24)))
    assert len(line["history"]) == 12
    reasons = {s["line_item"]: s["reason"] for s in out["skipped"]}
    assert reasons == {"Preview new": "fewer than 3 periods", "Preview zero": "all zero"}


def test_preview_reports_an_unavailable_service(db_session, dataset, monkeypatch):
    ds, _ = dataset

    def down(series):
        raise FoundationServiceUnavailable("circuit open")

    monkeypatch.setattr(foundation_client, "forecast_batch", down)
    out = build_preview(db_session, ds)
    assert out["available"] is False and "circuit open" in out["reason"] and out["lines"] == []


def test_endpoint_is_company_scoped(db_session, dataset, seed_users, monkeypatch):
    from app.api.panel import forecast_preview
    from app.models.business_unit import BusinessUnit

    ds, _ = dataset
    monkeypatch.setattr(
        foundation_client, "forecast_batch",
        lambda series: FoundationBatchResult(provenance={}, forecasts={}),
    )
    ok = forecast_preview(dataset_id=ds.id, horizon=3, current_user=seed_users["admin"], db=db_session)
    assert ok.panel_type == "forecast_preview"

    other = BusinessUnit(name="Other Co")
    db_session.add(other)
    db_session.commit()
    analyst = seed_users["analyst"]
    analyst.business_unit_id = other.id
    db_session.commit()
    with pytest.raises(HTTPException) as e:
        forecast_preview(dataset_id=ds.id, horizon=3, current_user=analyst, db=db_session)
    assert e.value.status_code == 404
