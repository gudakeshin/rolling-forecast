"""Phase F P2: bias / tracking signal, revision stability, FVA by user and reason."""

from __future__ import annotations

import asyncio

import pytest

from app.models.forecast import ForecastLineResult, ForecastVersion
from app.models.fx import ForecastAccuracyRecord
from app.models.override import Override
from app.services.accuracy_diagnostics import (
    bias_signals,
    diagnostics,
    reason_theme,
    revision_stability,
)


def _rec(version_id, li_id, period, horizon, p50, actual, model_p50=None):
    return ForecastAccuracyRecord(
        version_id=version_id, line_item_id=li_id, period=period, horizon_offset=horizon,
        predicted_p10=p50 - 10, predicted_p50=p50, predicted_p90=p50 + 10,
        model_p50=model_p50, actual=actual, absolute_error=abs(p50 - actual),
        within_p10_p90=abs(p50 - actual) <= 10,
    )


def test_tracking_signal_separates_bias_from_noise():
    periods = [f"2025-{m:02d}" for m in range(1, 9)]
    always_over = [_rec("v", 1, p, 1, 110.0, 100.0) for p in periods]
    noisy = [_rec("v", 2, p, 1, 100.0 + (10 if i % 2 else -10), 100.0) for i, p in enumerate(periods)]
    out = {d["line_item_id"]: d for d in bias_signals(always_over + noisy)}
    assert out[1]["tracking_signal"] == pytest.approx(8.0) and out[1]["biased"] and out[1]["direction"] == "over"
    assert out[1]["bias_pct"] == pytest.approx(10.0)
    assert out[2]["tracking_signal"] == pytest.approx(0.0) and not out[2]["biased"]
    # Too few observations: no verdict.
    assert bias_signals(always_over[:3]) == []


def test_tracking_signal_uses_the_closest_vintage_only():
    recs = [_rec("old", 1, f"2025-{m:02d}", 6, 200.0, 100.0) for m in range(1, 6)]
    recs += [_rec("new", 1, f"2025-{m:02d}", 1, 100.0, 100.0) for m in range(1, 6)]
    (row,) = bias_signals(recs)
    assert row["mean_error"] == 0.0


def test_revision_stability_measures_size_and_flip_flops():
    # Line 1: 100 -> 110 -> 100 -> 110 on an actual of 100: 10% each, every move reverses.
    churn = [_rec(f"v{h}", 1, "2025-06", h, p, 100.0) for h, p in ((4, 100.0), (3, 110.0), (2, 100.0), (1, 110.0))]
    # Line 2: steady convergence 120 -> 110 -> 105, no reversals.
    steady = [_rec(f"v{h}", 2, "2025-06", h, p, 100.0) for h, p in ((3, 120.0), (2, 110.0), (1, 105.0))]
    out = revision_stability(churn + steady)
    by_line = {d["line_item_id"]: d for d in out["lines"]}
    assert by_line[1]["mean_abs_revision_pct"] == pytest.approx(10.0)
    assert by_line[1]["flip_rate"] == pytest.approx(1.0)
    assert by_line[2]["mean_abs_revision_pct"] == pytest.approx(7.5)
    assert by_line[2]["flip_rate"] == pytest.approx(0.0)
    assert out["n_revisions"] == 5


@pytest.mark.parametrize("text, theme", [
    ("Price increase effective March per pricing committee", "pricing"),
    ("Contract with Acme signed, order lands in Q2", "pipeline / deals"),
    ("Invoice timing slipped into next month", "timing"),
    ("Align to the approved budget", "plan / budget"),
    ("One-off legal settlement", "one-off"),
    ("Gut feel from the regional lead", "other"),
])
def test_reason_themes(text, theme):
    assert reason_theme(text) == theme


def test_fva_by_user_and_reason_and_the_endpoint(db_session, seed_users, seed_line_items):
    from app.api.dashboard import get_accuracy_tracking

    li = seed_line_items["REV-001"]
    v = ForecastVersion(id="ad-v1", name="AD", status="published", version_type="baseline",
                        horizon_months=2, scenario="base")
    db_session.add(v)
    db_session.flush()
    analyst, admin = seed_users["analyst"], seed_users["admin"]
    # Jan: model 100, analyst overrode to 120, actual 118 -> helped by 16.
    # Feb: model 100, admin overrode to 80, actual 105 -> hurt by 20.
    for period, model, published, actual, user, reason in (
        ("2025-01", 100.0, 120.0, 118.0, analyst, "Price increase landed early"),
        ("2025-02", 100.0, 80.0, 105.0, admin, "Customer contract delayed"),
    ):
        db_session.add(ForecastLineResult(version_id=v.id, line_item_id=li.id, period=period,
                                          p10=published - 10, p50=published, p90=published + 10))
        db_session.add(_rec(v.id, li.id, period, 1, published, actual, model_p50=model))
        db_session.add(Override(version_id=v.id, line_item_id=li.id, period=period,
                                original_model_value=model, override_value=published,
                                reason=reason, status="active", user_id=user.id))
    db_session.commit()

    out = diagnostics(db_session, [li.id])
    by_user = {r["user"]: r for r in out["fva_by_user"]}
    assert by_user["analyst"]["value_added"] == pytest.approx(16.0)
    assert by_user["analyst"]["helped_share"] == 1.0
    assert by_user["admin"]["value_added"] == pytest.approx(-20.0)
    by_theme = {r["reason_theme"]: r for r in out["fva_by_reason"]}
    assert set(by_theme) == {"pricing", "timing"}  # "contract delayed" is a timing slip

    resp = asyncio.run(get_accuracy_tracking(version_id=v.id, current_user=admin, db=db_session))
    diag = resp.data["diagnostics"]
    assert {r["user"] for r in diag["fva_by_user"]} == {"analyst", "admin"}
    assert "revision_stability" in diag and "bias_signals" in diag


def test_crps_from_quantiles():
    from app.services.interval_calibration import crps_from_quantiles

    # Median only: CRPS is the absolute error.
    assert crps_from_quantiles(110.0, {0.5: 100.0}) == pytest.approx(10.0)
    q = {0.1: 90.0, 0.5: 100.0, 0.9: 110.0}
    inside = crps_from_quantiles(100.0, q)
    outside = crps_from_quantiles(130.0, q)
    # 2 * mean(0.1*10, 0, 0.1*10) = 4/3 at the median; far outside costs more.
    assert inside == pytest.approx(4.0 / 3.0)
    assert outside > inside
    # A sharper band that still covers scores better.
    assert crps_from_quantiles(100.0, {0.1: 95.0, 0.5: 100.0, 0.9: 105.0}) < inside
    assert crps_from_quantiles(100.0, {0.1: None}) is None
