"""Agent skills and the review metric for the Phase E foundation-model features."""

from __future__ import annotations

import asyncio

import numpy as np
import pytest

from app.domain.skills.detect_anomalies import DetectAnomaliesSkill
from app.domain.skills.list_model_disagreements import ListModelDisagreementsSkill
from app.domain.skills.preview_forecast import PreviewForecastSkill
from app.models.forecast import ForecastLineResult, ForecastVersion, ModelMetadata
from app.models.fx import ForecastAccuracyRecord
from app.services import foundation_client
from app.services.foundation_client import FoundationBatchResult, QuantilePath
from app.services.second_opinion import DISAGREE_FLAG, review_stats, second_opinion

FLAG = f"{DISAGREE_FLAG}: the forecast is outside Chronos-2's P10-P90 in 3 of 4 periods"


def _version(db, seed_business_unit, vid="fs-v1"):
    v = ForecastVersion(
        id=vid, name=vid, status="draft", version_type="baseline", horizon_months=2,
        scenario="base", business_unit_id=seed_business_unit.id,
    )
    db.add(v)
    db.flush()
    return v


def _cell(db, v, li, period, *, flagged=False, status=None, overridden=False):
    r = ForecastLineResult(
        version_id=v.id, line_item_id=li.id, period=period, p10=90.0, p50=100.0, p90=110.0,
        model_type="ets", confidence_score=90.0, confidence_level="high",
        ai_reasoning=FLAG if flagged else "All quality checks passed",
        review_status=status, is_overridden=overridden,
    )
    db.add(r)
    db.flush()
    if flagged:  # as generation writes it: the durable record is the metadata
        db.add(ModelMetadata(line_result_id=r.id, model_type="ets", parameters={"second_opinion": OPINION}))
        db.flush()
    return r


OPINION = second_opinion(
    np.array([150.0, 150.0, 150.0, 100.0]), np.array([90.0] * 4), np.array([100.0] * 4),
    np.array([110.0] * 4), ["2026-01", "2026-02", "2026-03", "2026-04"],
)


def test_review_stats_measure_reviews_on_flagged_lines(db_session, seed_line_items, seed_business_unit):
    v = _version(db_session, seed_business_unit)
    lines = [li for li in seed_line_items.values() if not li.is_calculated][:4]
    # Line 0: flagged, approved. Line 1: flagged, untouched, overridden.
    # Line 2: not flagged, approved. Line 3: not flagged, untouched.
    _cell(db_session, v, lines[0], "2026-01", flagged=True, status="approved")
    _cell(db_session, v, lines[1], "2026-01", flagged=True, overridden=True)
    _cell(db_session, v, lines[2], "2026-01", status="approved")
    _cell(db_session, v, lines[3], "2026-01")
    db_session.commit()

    s = review_stats(db_session, v.id)
    assert s == {
        "flagged_lines": 2, "reviewed_lines": 2, "flagged_reviewed": 1, "flagged_overridden": 1,
        "share_of_reviews": 0.5, "flagged_reviewed_share": 0.5,
    }
    # Scoped to lines the caller can see.
    assert review_stats(db_session, v.id, [lines[2].id])["flagged_lines"] == 0
    assert review_stats(db_session, v.id, [])["reviewed_lines"] == 0


def test_list_model_disagreements_skill(db_session, seed_line_items, seed_business_unit, skill_context):
    v = _version(db_session, seed_business_unit, "fs-v2")
    lines = [li for li in seed_line_items.values() if not li.is_calculated][:2]
    _cell(db_session, v, lines[0], "2026-01", flagged=True, status="approved")
    _cell(db_session, v, lines[1], "2026-01")
    db_session.commit()

    r = asyncio.run(ListModelDisagreementsSkill().execute({"version_id": v.id}, skill_context))
    assert r.success
    (item,) = r.data["items"]
    assert item["line_item_id"] == lines[0].id and item["outside_share"] == 0.75 and item["direction"] == "above"
    assert r.data["review_stats"]["flagged_reviewed"] == 1
    assert "Models disagree on **1** line" in r.message
    assert r.panel_payload["panel"] == "review_queue"

    # A version where the models agree everywhere.
    calm = _version(db_session, seed_business_unit, "fs-v3")
    _cell(db_session, calm, lines[0], "2026-01")
    db_session.commit()
    r2 = asyncio.run(ListModelDisagreementsSkill().execute({"version_id": calm.id}, skill_context))
    assert r2.success and r2.data["items"] == [] and r2.panel_payload is None


def test_preview_forecast_skill(db_session, seed_actuals, skill_context, monkeypatch):
    monkeypatch.setattr(
        foundation_client, "forecast_batch",
        lambda series: FoundationBatchResult(
            provenance={},
            forecasts={s["id"]: QuantilePath(p10=(90.0,) * 3, p50=(100.0,) * 3, p90=(110.0,) * 3) for s in series},
        ),
    )
    r = asyncio.run(PreviewForecastSkill().execute({"horizon_months": 3}, skill_context))
    assert r.success, r.message
    assert r.data["dataset_id"] == seed_actuals.id and r.data["lines"] > 0
    assert r.panel_payload == {
        "panel": "forecast_preview", "params": {"dataset_id": seed_actuals.id}, "title": "Instant forecast preview",
    }
    assert "not saved" in r.message

    def down(series):
        raise foundation_client.FoundationServiceUnavailable("circuit open")

    monkeypatch.setattr(foundation_client, "forecast_batch", down)
    r2 = asyncio.run(PreviewForecastSkill().execute({"dataset_id": seed_actuals.id}, skill_context))
    assert not r2.success and "circuit open" in r2.message


def test_preview_forecast_skill_without_a_dataset(db_session, skill_context):
    r = asyncio.run(PreviewForecastSkill().execute({"dataset_id": "nope"}, skill_context))
    assert not r.success and "Upload actuals first" in r.message


@pytest.mark.parametrize("method, expect", [("vintage_band", True), ("all", True), ("zscore", False)])
def test_detect_anomalies_reports_vintage_band_misses(
    db_session, seed_line_items, seed_business_unit, skill_context, method, expect
):
    li = next(li for li in seed_line_items.values() if not li.is_calculated)
    v = _version(db_session, seed_business_unit, f"fs-va-{method}")
    db_session.add(ForecastAccuracyRecord(
        version_id=v.id, line_item_id=li.id, period="2025-09", horizon_offset=1,
        predicted_p10=90_000.0, predicted_p50=100_000.0, predicted_p90=110_000.0,
        actual=150_000.0, absolute_error=50_000.0, within_p10_p90=False,
    ))
    db_session.commit()
    skill_context.context_manager.set_active_version_id(v.id)
    r = asyncio.run(DetectAnomaliesSkill().execute(
        {"target": "actuals", "method": method, "line_item_name": li.account_code}, skill_context
    ))
    assert r.success
    data = r.data or {}
    if expect:
        assert data.get("total_anomalies", 0) >= 1
    # The vintage finding itself is checked directly on the helper.
    found = DetectAnomaliesSkill()._vintage_band_detection(db_session, [li])
    assert len(found) == 1 and found[0]["method"] == "vintage_band" and found[0]["severity"] == "critical"
    assert "above the P10-P90" in found[0]["note"]


def test_review_dashboard_summary_carries_the_stats(db_session, seed_users, seed_line_items, seed_business_unit):
    from app.api.dashboard import get_review_dashboard

    v = _version(db_session, seed_business_unit, "fs-v4")
    lines = [li for li in seed_line_items.values() if not li.is_calculated][:2]
    _cell(db_session, v, lines[0], "2026-01", flagged=True, status="approved")
    _cell(db_session, v, lines[1], "2026-01", status="approved")
    db_session.commit()
    out = asyncio.run(get_review_dashboard(version_id=v.id, current_user=seed_users["admin"], db=db_session))
    stats = out.data["summary"]["second_opinion"]
    assert stats["flagged_lines"] == 1 and stats["share_of_reviews"] == 0.5


def test_flag_survives_review_triage_rewriting_ai_reasoning(
    db_session, seed_users, seed_line_items, seed_business_unit
):
    """Opening the review dashboard re-runs AI triage and rewrites every cell's
    ai_reasoning; the disagreement must still drive the queue and its badge."""
    from app.api.dashboard import get_review_dashboard
    from app.api.panel import get_review_queue

    v = _version(db_session, seed_business_unit, "fs-v5")
    li = next(li for li in seed_line_items.values() if not li.is_calculated)
    cell = _cell(db_session, v, li, "2026-01", flagged=True)
    db_session.commit()

    asyncio.run(get_review_dashboard(version_id=v.id, current_user=seed_users["admin"], db=db_session))
    db_session.refresh(cell)
    assert DISAGREE_FLAG not in (cell.ai_reasoning or "")  # triage replaced the text

    queue = asyncio.run(get_review_queue(version_id=v.id, current_user=seed_users["admin"], db=db_session))
    (item,) = queue.data["items"]
    assert item["id"] == cell.id and item["models_disagree"] is True
