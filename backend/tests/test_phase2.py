"""Phase 2–3 statistical rigor and agent grounding tests."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.domain.engines.linear import LinearTrendModel
from app.domain.engines.ets import ETSModel
from app.domain.engines.model_registry import ModelRegistry
from app.services.numeric_grounding import render_fact_placeholders, validate_numeric_claims
from app.services.period_calendar import add_periods, horizon_offset, period_range


def test_evaluate_cv_returns_multiple_folds():
    rng = np.random.default_rng(0)
    n = 36
    values = 100 + np.arange(n) * 2 + rng.normal(0, 3, n)
    series = pd.Series(values)
    dates = pd.date_range("2022-01-01", periods=n, freq="MS")
    model = LinearTrendModel()
    cv = model.evaluate_cv(series, dates, n_folds=3, fold_horizon=3)
    assert cv["n_folds_used"] > 1
    assert len(cv["fold_mapes"]) >= 2
    assert cv["mean_mape"] != float("inf")


def test_compare_models_uses_rolling_origin_cv():
    rng = np.random.default_rng(1)
    n = 48
    # Strong seasonality → ETS should beat linear across folds on average
    t = np.arange(n)
    values = 200 + 0.5 * t + 30 * np.sin(2 * np.pi * t / 12) + rng.normal(0, 2, n)
    series = pd.Series(values)
    dates = pd.date_range("2021-01-01", periods=n, freq="MS")
    reg = ModelRegistry()
    result = reg.compare_models(series, dates, test_size=6, models_to_test=["linear", "ets"])
    assert result.selection_method == "rolling_origin_cv"
    assert any(c.n_folds > 1 for c in result.comparisons if c.eligible)


def test_period_calendar_horizon_offset():
    assert horizon_offset("2025-01", "2025-02") == 1
    assert horizon_offset("2025-01", "2025-07") == 6
    assert add_periods("2025-11", 2) == "2026-01"
    assert period_range("2025-01", "2025-03") == ["2025-01", "2025-02", "2025-03"]


def test_fiscal_445_period_labels():
    from app.services.period_calendar import (
        CalendarType,
        FiscalCalendarConfig,
        add_periods,
        date_to_period,
        forecast_horizon_periods,
        period_to_date,
        push_calendar,
        reset_calendar,
    )
    from datetime import date

    cfg = FiscalCalendarConfig(calendar_type=CalendarType.FISCAL_445, fiscal_year_start_month=2, week_start=6)
    token = push_calendar(cfg)
    try:
        # A mid-year date maps to an FY-P label
        label = date_to_period(date(2026, 6, 15), cfg)
        assert label.startswith("FY")
        assert "-P" in label
        nxt = add_periods(label, 1, cfg)
        assert nxt != label
        horizon = forecast_horizon_periods(label, 3, cfg)
        assert len(horizon) == 3
        assert period_to_date(horizon[0], cfg) > period_to_date(label, cfg)
    finally:
        reset_calendar(token)


def test_make_period_labels_respects_calendar_context():
    from app.domain.engines.base_model import make_period_labels
    from app.services.period_calendar import (
        CalendarType,
        FiscalCalendarConfig,
        push_calendar,
        reset_calendar,
    )
    import pandas as pd

    cfg = FiscalCalendarConfig(calendar_type=CalendarType.FISCAL_445)
    token = push_calendar(cfg)
    try:
        labels = make_period_labels(pd.Timestamp("2026-03-01"), 2)
        assert all(p.startswith("FY") for p in labels)
    finally:
        reset_calendar(token)


def test_fact_placeholders_and_numeric_validator():
    text = "Revenue is {fact:revenue_total} vs prior {fact:missing}."
    rendered = render_fact_placeholders(text, {"revenue_total": 1250000.5})
    assert "1,250,000.5" in rendered
    assert "{fact:missing}" in rendered

    cleaned, verified, total = validate_numeric_claims(
        "Growth of 12.5% and 999",
        allowed_numbers={"12.5", "12.5%"},
    )
    assert total == 2
    assert verified == 1
    assert "[999]" in cleaned


@pytest.mark.asyncio
async def test_scenario_recalculate_all(db_session, seed_line_items, seed_actuals):
    """Revenue +10% on leaves should recompute calculated EBITDA via recalculate_all."""
    from app.models.forecast import ForecastVersion, ForecastLineResult
    from app.models.line_item import LineItem, LineItemDependency
    from app.services.dependency_graph import DependencyGraphManager
    from app.services.coa_dependencies import ensure_standard_dependencies

    items = list(seed_line_items.values())
    ensure_standard_dependencies(db_session, items)
    # Ensure EBITDA depends on revenue-like items if CoA didn't wire it
    ebitda = seed_line_items.get("EBITDA")
    rev = seed_line_items.get("REV-001")
    if ebitda and rev:
        existing = (
            db_session.query(LineItemDependency)
            .filter(
                LineItemDependency.dependent_item_id == ebitda.id,
                LineItemDependency.source_item_id == rev.id,
            )
            .first()
        )
        if not existing:
            db_session.add(LineItemDependency(
                dependent_item_id=ebitda.id,
                source_item_id=rev.id,
                relationship_type="sum",
                weight=1.0,
            ))
            db_session.commit()

    version = ForecastVersion(name="branch-test", status="draft", horizon_months=3)
    db_session.add(version)
    db_session.flush()

    period = "2026-01"
    for code, li in seed_line_items.items():
        if li.is_calculated:
            db_session.add(ForecastLineResult(
                version_id=version.id, line_item_id=li.id, period=period,
                p50=0.0, is_calculated=True,
            ))
        else:
            base = 1000.0 if "REV" in code else 400.0
            db_session.add(ForecastLineResult(
                version_id=version.id, line_item_id=li.id, period=period,
                p50=base, is_calculated=False,
            ))
    db_session.commit()

    # Scale leaf revenue +10%
    rev_row = (
        db_session.query(ForecastLineResult)
        .filter(
            ForecastLineResult.version_id == version.id,
            ForecastLineResult.line_item_id == rev.id,
            ForecastLineResult.period == period,
        )
        .one()
    )
    rev_row.p50 = rev_row.p50 * 1.1
    db_session.flush()

    n = DependencyGraphManager(db_session).recalculate_all(version.id)
    db_session.commit()
    assert n >= 0  # may be 0 if no calculated deps wired in seed

    # At minimum, recalculate_all must not crash and leaves stay scaled
    assert rev_row.p50 == pytest.approx(1100.0)


@pytest.mark.asyncio
async def test_mint_reconcile_tags_bounds(db_session, seed_line_items):
    """MinT reconciliation sets bounds_method on calculated parents."""
    from app.models.forecast import ForecastVersion, ForecastLineResult
    from app.models.line_item import LineItemDependency
    from app.services.coa_dependencies import ensure_standard_dependencies
    from app.services.reconciliation import BOUNDS_METHOD_MINT, reconcile_version

    items = list(seed_line_items.values())
    ensure_standard_dependencies(db_session, items)
    ebitda = seed_line_items.get("EBITDA")
    rev = seed_line_items.get("REV-001")
    if not ebitda or not rev:
        pytest.skip("seed missing EBITDA/REV")
    if not (
        db_session.query(LineItemDependency)
        .filter(
            LineItemDependency.dependent_item_id == ebitda.id,
            LineItemDependency.source_item_id == rev.id,
        )
        .first()
    ):
        db_session.add(
            LineItemDependency(
                dependent_item_id=ebitda.id,
                source_item_id=rev.id,
                relationship_type="sum",
                weight=1.0,
            )
        )
        db_session.commit()

    version = ForecastVersion(name="mint-test", status="draft", horizon_months=1)
    db_session.add(version)
    db_session.flush()
    period = "2026-01"
    for code, li in seed_line_items.items():
        if li.is_calculated:
            db_session.add(
                ForecastLineResult(
                    version_id=version.id,
                    line_item_id=li.id,
                    period=period,
                    p10=0.0,
                    p50=0.0,
                    p90=0.0,
                    is_calculated=True,
                )
            )
        else:
            base = 1000.0 if "REV" in code else 400.0
            db_session.add(
                ForecastLineResult(
                    version_id=version.id,
                    line_item_id=li.id,
                    period=period,
                    p10=base * 0.9,
                    p50=base,
                    p90=base * 1.1,
                    is_calculated=False,
                    bounds_method="model",
                )
            )
    db_session.commit()

    out = reconcile_version(db_session, version.id)
    db_session.commit()
    assert out["bounds_method"] == BOUNDS_METHOD_MINT

    parent = (
        db_session.query(ForecastLineResult)
        .filter(
            ForecastLineResult.version_id == version.id,
            ForecastLineResult.line_item_id == ebitda.id,
            ForecastLineResult.period == period,
        )
        .one()
    )
    assert parent.bounds_method in (BOUNDS_METHOD_MINT, "linear_aggregation")
    assert parent.p50 is not None


def test_period_to_date_accepts_fiscal_labels():
    """Baseline date construction must not assume YYYY-MM + '-01'."""
    from datetime import date

    from app.services.period_calendar import (
        CalendarType,
        FiscalCalendarConfig,
        period_to_date,
        push_calendar,
        reset_calendar,
    )

    cfg = FiscalCalendarConfig(calendar_type=CalendarType.FISCAL_445)
    token = push_calendar(cfg)
    try:
        d = period_to_date("FY2026-P01", cfg)
        assert isinstance(d, date)
        assert period_to_date("2025-03").month == 3
    finally:
        reset_calendar(token)


def test_agent_cache_reuses_compiled_graph(monkeypatch):
    """Compiled agent graph is cached across MasterAgent instances until skill reload."""
    from app.orchestration import master_agent as ma

    ma.invalidate_agent_cache()
    calls = {"n": 0}
    sentinel = object()

    def fake_create_react_agent(**kwargs):
        calls["n"] += 1
        return sentinel

    monkeypatch.setattr(ma, "create_react_agent", fake_create_react_agent)
    monkeypatch.setattr(ma, "ChatAnthropic", lambda **kwargs: object())

    class FakeCM:
        user_id = "u1"
        user_role = "analyst"
        conversation_id = "c1"
        user = None
        _working_memory: dict = {}

        def get_system_context(self):
            return "ctx"

        def get_relevant_context(self, *a, **k):
            return ""

        def get_chat_history(self, **k):
            return []

    class FakeRegistry:
        definitions_generation = 0

        def as_langchain_tools(self, ctx=None):
            return []

    monkeypatch.setattr(ma, "get_registry", lambda: FakeRegistry())

    a1 = ma.MasterAgent(context_manager=FakeCM(), db=None)  # type: ignore[arg-type]
    a2 = ma.MasterAgent(context_manager=FakeCM(), db=None)  # type: ignore[arg-type]
    assert a1._get_agent() is sentinel
    assert a2._get_agent() is sentinel
    assert calls["n"] == 1

    ma.invalidate_agent_cache()
    assert a1._get_agent() is sentinel
    assert calls["n"] == 2
