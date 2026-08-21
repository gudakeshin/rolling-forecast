"""Tests for FX conversion, vintage accuracy, and numeric grounding."""

from __future__ import annotations

import pytest

from app.models.fx import FxRate, ForecastAccuracyRecord, SystemSetting
from app.models.forecast import ForecastVersion, ForecastLineResult
from app.models.actuals import ActualsRecord, ActualsDataset
from app.models.line_item import LineItem
from app.services.fx import (
    MissingFxRateError,
    convert_series_values,
    get_reporting_currency,
    set_reporting_currency,
)
from app.services.accuracy_snapshot import AccuracySnapshotService
from app.services.numeric_grounding import (
    facts_from_context,
    render_fact_placeholders,
    validate_numeric_claims,
)
from app.services.ingestion.base import IActualsProvider, IngestionResult
from app.services.ingestion.erp_adapter import WarehouseSQLAdapter, ActualsPullResult


def test_missing_fx_rate_raises(db_session):
    set_reporting_currency(db_session, "USD")
    db_session.commit()
    with pytest.raises(MissingFxRateError) as exc:
        convert_series_values(
            db_session,
            values=[100.0, 200.0],
            currencies=["EUR", "EUR"],
            periods=["2024-01", "2024-02"],
            reporting_currency="USD",
        )
    assert ("EUR", "USD", "2024-01") in exc.value.pairs


def test_fx_convert_and_hash(db_session):
    set_reporting_currency(db_session, "USD")
    db_session.add(
        FxRate(from_currency="EUR", to_currency="USD", period="2024-01", rate=1.1, rate_type="average")
    )
    db_session.add(
        FxRate(from_currency="EUR", to_currency="USD", period="2024-02", rate=1.2, rate_type="average")
    )
    db_session.commit()

    out, digest = convert_series_values(
        db_session,
        values=[100.0, 200.0],
        currencies=["EUR", "EUR"],
        periods=["2024-01", "2024-02"],
        reporting_currency="USD",
    )
    assert out == pytest.approx([110.0, 240.0])
    assert digest != "identity"
    assert len(digest) == 64


def test_same_currency_identity_hash(db_session):
    out, digest = convert_series_values(
        db_session,
        values=[10.0],
        currencies=["USD"],
        periods=["2024-01"],
        reporting_currency="USD",
    )
    assert out == [10.0]
    assert digest == "identity"


def test_reporting_currency_setting(db_session):
    assert get_reporting_currency(db_session) == "USD"
    set_reporting_currency(db_session, "eur")
    db_session.commit()
    assert get_reporting_currency(db_session) == "EUR"


def test_accuracy_snapshot_horizon(db_session, seed_line_items):
    li = db_session.query(LineItem).first()
    assert li is not None

    version = ForecastVersion(
        name="FC-test-v1",
        status="draft",
        version_type="scheduled",
        base_period="2024-01",
        horizon_months=6,
    )
    db_session.add(version)
    db_session.flush()

    db_session.add(
        ForecastLineResult(
            version_id=version.id,
            line_item_id=li.id,
            period="2024-03",
            p10=90.0,
            p50=100.0,
            p90=110.0,
            model_type="linear",
        )
    )
    ds = ActualsDataset(
        source_type="csv",
        source_name="ds-acc.csv",
        file_hash="abc",
        period_start="2024-03",
        period_end="2024-03",
        periods_count=1,
        row_count=1,
    )
    db_session.add(ds)
    db_session.flush()
    db_session.add(
        ActualsRecord(
            dataset_id=ds.id,
            line_item_id=li.id,
            period="2024-03",
            value=105.0,
            currency="USD",
        )
    )
    db_session.commit()

    n = AccuracySnapshotService(db_session).on_actuals_ingested(ds.id)
    db_session.commit()
    assert n >= 1
    rec = db_session.query(ForecastAccuracyRecord).first()
    assert rec is not None
    assert rec.horizon_offset == 2
    assert rec.actual == 105.0
    assert rec.within_p10_p90 is True


@pytest.mark.asyncio
async def test_bias_analysis_uses_vintage_records(db_session, seed_users, skill_context):
    """Bias report must read ForecastAccuracyRecord, not live-join all history."""
    from app.domain.skills.auto_accuracy_report import AutoAccuracyReportSkill

    li = LineItem(
        name="Revenue Vintage Bias",
        account_code="RV-BIAS",
        category="Revenue",
        is_calculated=False,
    )
    db_session.add(li)
    db_session.flush()

    version = ForecastVersion(
        name="FC-Vintage-Bias",
        status="draft",
        version_type="scheduled",
        horizon_months=3,
        base_period="2024-01",
        created_by=seed_users["analyst"].id,
    )
    db_session.add(version)
    db_session.flush()

    db_session.add(
        ForecastAccuracyRecord(
            version_id=version.id,
            line_item_id=li.id,
            period="2024-02",
            horizon_offset=1,
            predicted_p10=90.0,
            predicted_p50=120.0,
            predicted_p90=140.0,
            actual=100.0,
            absolute_error=20.0,
            pct_error=20.0,
            within_p10_p90=True,
            model_type="ets",
        )
    )
    db_session.commit()

    skill_context.context_manager.set_active_version_id(version.id)
    result = await AutoAccuracyReportSkill().execute(
        {"report_type": "bias_analysis"},
        skill_context,
    )
    assert result.success
    assert result.data.get("source") == "forecast_accuracy_records"
    assert result.data.get("over_forecasting", 0) >= 1


def test_fact_placeholders_and_validator():
    text = "Revenue is {fact:revenue_total} vs plan."
    rendered = render_fact_placeholders(text, {"revenue_total": 1_250_000.0})
    assert "1,250,000.0" in rendered
    cleaned, verified, total = validate_numeric_claims(
        rendered, {"1250000.0", "1,250,000.0"}
    )
    assert total >= 1
    assert verified >= 1
    assert "[" not in cleaned  # verified numbers not bracketed

    bad, v2, t2 = validate_numeric_claims("Secret number 99999 slipped in", {"1"})
    assert t2 >= 1
    assert v2 == 0
    assert "[99999]" in bad


def test_facts_from_context():
    facts = facts_from_context({"revenue_total": 10, "summary": {"ebitda": 3}})
    assert facts["revenue_total"] == 10
    assert facts["ebitda"] == 3


def test_erp_adapter_uses_base_provider():
    assert issubclass(WarehouseSQLAdapter, IActualsProvider)
    assert ActualsPullResult is IngestionResult
    adapter = WarehouseSQLAdapter()
    assert adapter.source_type == "warehouse"
