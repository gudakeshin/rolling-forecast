"""Value metrics across versions (enhancement plan 6.4)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.models.business_unit import BusinessUnit
from app.models.forecast import ForecastLineResult, ForecastVersion
from app.models.fx import ForecastAccuracyRecord
from app.models.line_item import LineItem
from app.services.value_metrics import compute_value_metrics

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _version(db, bu, name, created, done_days, rows, accuracy=()):
    """rows: (is_overridden, review_status, ai_recommendation) per line."""
    v = ForecastVersion(
        name=name, business_unit_id=bu.id, created_at=created, status="published",
        published_at=created + timedelta(days=done_days) if done_days is not None else None,
    )
    db.add(v)
    db.flush()
    for i, (overridden, review, ai) in enumerate(rows):
        li = LineItem(account_code=f"{name}-{i}", name=f"{name} {i}", category="Revenue", business_unit_id=bu.id)
        db.add(li)
        db.flush()
        db.add(ForecastLineResult(
            version_id=v.id, line_item_id=li.id, period="2026-01", p50=100.0,
            is_overridden=overridden, review_status=review, ai_recommendation=ai,
        ))
        for pub, act, naive in accuracy[i:i + 1]:
            db.add(ForecastAccuracyRecord(
                version_id=v.id, line_item_id=li.id, period="2026-01", horizon_offset=1,
                predicted_p50=pub, naive_p50=naive, actual=act, absolute_error=abs(pub - act),
            ))
    db.flush()
    return v


@pytest.fixture
def company(db_session):
    bu = BusinessUnit(name="ValueCo")
    db_session.add(bu)
    db_session.flush()
    # Early cycle: slow, half overridden, reviewers spent half their effort on unflagged lines.
    _version(db_session, bu, "V1", T0, 10, [
        (True, "approved", "approve"), (True, "approved", "review"),
        (False, None, "approve"), (False, None, "approve"),
    ], accuracy=[(110.0, 100.0, 130.0)])
    # Later cycle: faster, fewer overrides, review aimed only at flagged lines.
    _version(db_session, bu, "V2", T0 + timedelta(days=30), 4, [
        (True, "approved", "override"), (False, "approved", "review"),
        (False, None, "approve"), (False, None, "approve"),
    ], accuracy=[(100.0, 100.0, 120.0)])
    db_session.commit()
    return bu


def test_per_version_metrics(db_session, company):
    out = compute_value_metrics(db_session, business_unit_id=company.id)
    v1, v2 = out["versions"]
    assert (v1["version_name"], v2["version_name"]) == ("V1", "V2")
    assert v1["cycle_days"] == 10.0 and v2["cycle_days"] == 4.0
    assert v1["override_rate"] == 0.5 and v2["override_rate"] == 0.25
    assert v1["flagged_review_share"] == 0.5 and v2["flagged_review_share"] == 1.0
    # FVA = naive WAPE - published WAPE: V1 30 - 10 = 20, V2 20 - 0 = 20
    assert v1["fva_vs_naive"] == 20.0 and v2["fva_vs_naive"] == 20.0


def test_trends_know_which_direction_is_better(db_session, company):
    t = compute_value_metrics(db_session, business_unit_id=company.id)["trends"]
    assert t["cycle_days"] == {"latest": 4.0, "change": -6.0, "improving": True}
    assert t["override_rate"]["improving"] is True
    assert t["flagged_review_share"]["improving"] is True
    assert t["fva_vs_naive"]["improving"] is None  # flat


def test_unfinished_and_unreviewed_versions_report_none(db_session, company):
    _version(db_session, company, "V3", T0 + timedelta(days=60), None, [(False, None, "approve")])
    db_session.commit()
    v3 = compute_value_metrics(db_session, business_unit_id=company.id)["versions"][-1]
    assert v3["cycle_days"] is None
    assert v3["flagged_review_share"] is None
    assert v3["fva_vs_naive"] is None


def test_only_the_requested_company(db_session, company):
    other = BusinessUnit(name="OtherValueCo")
    db_session.add(other)
    db_session.commit()
    assert compute_value_metrics(db_session, business_unit_id=other.id)["versions"] == []
