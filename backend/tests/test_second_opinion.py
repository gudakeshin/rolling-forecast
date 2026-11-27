"""E2: second opinion. A line whose forecast sits outside Chronos-2's P10-P90
for most of the horizon is flagged for review; no number changes."""

from __future__ import annotations

import asyncio

import numpy as np
import pandas as pd
import pytest

from app.config import settings
from app.domain.engines.chronos2 import FoundationPanelContext, QuantileForecast
from app.domain.engines.global_gbm import GlobalPanelContext
from app.domain.engines.model_registry import ModelRegistry
from app.domain.skills.generate_baseline import _generate_remediation
from app.models.forecast import ForecastLineResult, ForecastVersion, ModelMetadata
from app.models.line_item import LineItem
from app.services.forecast_pipeline import LineForecastContext, forecast_line_item
from app.services.second_opinion import DISAGREE_FLAG, cell_view, disagree_flag, second_opinion

PERIODS = ["2026-01", "2026-02", "2026-03", "2026-04"]


def test_inside_band_does_not_disagree():
    op = second_opinion(
        np.array([100.0] * 4), np.array([90.0] * 4), np.array([100.0] * 4), np.array([110.0] * 4), PERIODS
    )
    assert op is not None
    assert op["outside_share"] == 0 and op["disagree"] is False and op["direction"] is None


def test_mostly_outside_disagrees_and_names_the_side():
    point = np.array([100.0, 150.0, 150.0, 150.0])
    op = second_opinion(point, np.array([90.0] * 4), np.array([100.0] * 4), np.array([110.0] * 4), PERIODS)
    assert op is not None
    assert op["outside_periods"] == PERIODS[1:]
    assert op["outside_share"] == 0.75 and op["disagree"] is True and op["direction"] == "above"
    flag = disagree_flag(op)
    assert flag.startswith(DISAGREE_FLAG) and "above its range" in flag and "3 of 4" in flag

    cell = cell_view(op, "2026-02")
    assert cell == {
        "model": "chronos2", "p10": 90.0, "p50": 100.0, "p90": 110.0, "outside": True,
        "line_outside_share": 0.75, "line_disagrees": True, "direction": "above",
    }
    assert cell_view(op, "2027-01") is None
    assert cell_view(None, "2026-01") is None


def test_one_outlying_period_is_not_enough():
    point = np.array([100.0, 100.0, 100.0, 150.0])
    op = second_opinion(point, np.array([90.0] * 4), np.array([100.0] * 4), np.array([110.0] * 4), PERIODS)
    assert op is not None and op["disagree"] is False


def test_remediation_carries_the_flag():
    r = ForecastLineResult(p10=95.0, p50=100.0, p90=105.0, model_type="ets", model_mape=5.0)
    remed = _generate_remediation(r, analysis_flags=[f"{DISAGREE_FLAG}: outside in 4 of 4 periods"])
    assert remed["severity"] == "warning"
    assert DISAGREE_FLAG in remed["reason"]
    assert "foundation model" in remed["suggestion"]


def _panel(line_id: int, origin: str, level: float) -> GlobalPanelContext:
    panel = GlobalPanelContext.empty()
    horizon = 3
    panel.foundation = FoundationPanelContext(
        status="ok",
        forecasts={(line_id, origin): QuantileForecast(
            p10=np.full(horizon, level - 5), p50=np.full(horizon, level), p90=np.full(horizon, level + 5)
        )},
        production_origin_by_line={line_id: origin},
    )
    return panel


@pytest.mark.parametrize("level, flagged", [(1000.0, True), (None, False)])
def test_pipeline_flags_a_disagreeing_line(monkeypatch, level, flagged):
    monkeypatch.setattr(settings, "foundation_range_blend_enabled", False)
    n = 24
    dates = pd.date_range("2024-01-01", periods=n, freq="MS")
    values = pd.Series(100.0 + np.arange(n, dtype=float))
    periods = [d.strftime("%Y-%m") for d in dates]
    li = LineItem(id=5, account_code="L5", name="Line five", allow_negative=False)
    # level=None: Chronos-2 agrees, centred on the line's own next value.
    panel = _panel(5, periods[-1], level if level is not None else 125.0)
    ctx = LineForecastContext(
        version_id="v", horizon=3, model_type="linear", model_registry=ModelRegistry(), panel=panel
    )
    out = forecast_line_item(None, li, values, dates, periods, ctx)  # type: ignore[arg-type]
    assert out.metadata_rows[0].parameters["second_opinion"]["disagree"] is flagged
    assert any(f.startswith(DISAGREE_FLAG) for f in out.flags) is flagged
    # A flag only: the published numbers are the selected model's.
    assert [r["p50"] for r in sorted(out.line_rows, key=lambda r: r["period"])] == pytest.approx(
        [124.0, 125.0, 126.0], rel=1e-6
    )


def test_off_switch(monkeypatch):
    monkeypatch.setattr(settings, "second_opinion_enabled", False)
    n = 24
    dates = pd.date_range("2024-01-01", periods=n, freq="MS")
    values = pd.Series(100.0 + np.arange(n, dtype=float))
    periods = [d.strftime("%Y-%m") for d in dates]
    li = LineItem(id=5, account_code="L5", name="Line five", allow_negative=False)
    ctx = LineForecastContext(
        version_id="v", horizon=3, model_type="linear", model_registry=ModelRegistry(),
        panel=_panel(5, periods[-1], 1000.0),
    )
    out = forecast_line_item(None, li, values, dates, periods, ctx)  # type: ignore[arg-type]
    assert "second_opinion" not in out.metadata_rows[0].parameters
    assert not any(f.startswith(DISAGREE_FLAG) for f in out.flags)


def test_review_queue_and_why_this_number(db_session, seed_line_items, seed_users):
    from app.api.panel import get_review_queue, why_this_number

    v = ForecastVersion(
        id="so-v1", name="SO", status="draft", version_type="baseline", horizon_months=1, scenario="base"
    )
    db_session.add(v)
    li = seed_line_items["REV-001"]
    flagged = ForecastLineResult(
        id="so-flagged", version_id=v.id, line_item_id=li.id, period="2026-02",
        p10=140.0, p50=150.0, p90=160.0, model_type="ets",
        confidence_score=90.0, confidence_level="high",
        ai_reasoning=f"{DISAGREE_FLAG}: the forecast is outside Chronos-2's P10-P90 in 3 of 4 periods",
    )
    other = next(x for k, x in seed_line_items.items() if k != "REV-001" and not x.is_calculated)
    calm = ForecastLineResult(  # a different line: the flag is per line
        id="so-calm", version_id=v.id, line_item_id=other.id, period="2026-03",
        p10=95.0, p50=100.0, p90=105.0, model_type="ets",
        confidence_score=90.0, confidence_level="high", ai_reasoning="All quality checks passed",
    )
    db_session.add_all([flagged, calm])
    db_session.flush()
    op = second_opinion(
        np.array([100.0, 150.0, 150.0, 150.0]), np.array([90.0] * 4), np.array([100.0] * 4),
        np.array([110.0] * 4), PERIODS,
    )
    db_session.add(ModelMetadata(line_result_id=flagged.id, model_type="ets", parameters={"second_opinion": op}))
    db_session.commit()

    queue = asyncio.run(get_review_queue(version_id=v.id, current_user=seed_users["admin"], db=db_session))
    items = queue.data["items"]
    assert [i["id"] for i in items] == ["so-flagged"]  # high confidence, but flagged
    assert items[0]["models_disagree"] is True

    why = asyncio.run(why_this_number(result_id=flagged.id, current_user=None, db=db_session))
    assert why.data["second_opinion"]["outside"] is True
    assert why.data["second_opinion"]["p50"] == 100.0
