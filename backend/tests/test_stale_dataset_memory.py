"""Regression: generate_baseline (and plan_forecast) must not trust stale
"last_dataset_id" conversation memory over the truly latest ingested actuals.

Both skills used to resolve the dataset via
``params.get("dataset_id") or context.context_manager.get_memory("last_dataset_id")``,
falling back to "most recent by ingested_at" only when that memory was empty.
Working memory is conversation-scoped and only updated by the ingest_actuals
skill, so a fresh actuals upload ingested from a different conversation (or a
session where the chat never explicitly re-ingested) left every future
baseline silently sourced from the old dataset the conversation last knew
about — "the base file sourced once and reused every time."
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from app.models.actuals import ActualsDataset, ActualsRecord
from app.models.forecast import ForecastVersion


@pytest.mark.asyncio
async def test_generate_baseline_ignores_stale_last_dataset_id_memory(
    db_session, seed_actuals, seed_line_items, skill_context
):
    from app.domain.skills.generate_baseline import GenerateBaselineSkill

    # Working memory still points at the older dataset, as it would after an
    # earlier ingest in this same conversation.
    skill_context.context_manager.set_memory("last_dataset_id", seed_actuals.id)

    # A newer dataset lands afterward — e.g. a re-upload ingested from a
    # different conversation, or the analyst's own follow-up ingest that this
    # conversation's memory was never updated with.
    newer = ActualsDataset(
        source_type="csv",
        source_name="fresh_actuals.csv",
        file_hash="test_hash_fresh_456",
        row_count=0,
        period_start="2024-01",
        period_end="2025-12",
        periods_count=24,
        completeness_pct=100.0,
        ingested_at=datetime.now(timezone.utc) + timedelta(minutes=5),
    )
    db_session.add(newer)
    db_session.flush()

    total = 0
    for code, li in seed_line_items.items():
        if li.is_calculated:
            continue
        for month in range(24):
            year = 2024 + month // 12
            m = (month % 12) + 1
            db_session.add(
                ActualsRecord(
                    dataset_id=newer.id,
                    line_item_id=li.id,
                    period=f"{year}-{m:02d}",
                    value=100000.0 + month * 1000,
                    currency="USD",
                )
            )
            total += 1
    newer.row_count = total
    db_session.commit()

    skill = GenerateBaselineSkill()
    result = await skill.execute(
        {"horizon_months": 3, "model_type": "linear", "random_seed": 42, "skip_plan_check": True},
        skill_context,
    )
    assert result.success, result.message

    version = (
        db_session.query(ForecastVersion)
        .filter(ForecastVersion.id == result.data["version_id"])
        .first()
    )
    assert version is not None
    assert version.actuals_dataset_id == newer.id, (
        "generate_baseline used stale last_dataset_id memory instead of the "
        "newest ingested dataset"
    )


def _write_actuals_csv(path, seed_line_items, base: float) -> None:
    """24 months of actuals for the non-calculated seed line items, at a
    given base magnitude so two calls with different `base` hash differently
    while a repeated call with the same `base` reproduces the exact same
    file content (and therefore file_hash)."""
    rows = []
    for code, li in seed_line_items.items():
        if li.is_calculated:
            continue
        for month in range(24):
            year = 2024 + month // 12
            m = (month % 12) + 1
            rows.append(
                {
                    "account_code": code,
                    "account_name": li.name,
                    "category": li.category,
                    "period": f"{year}-{m:02d}",
                    "value": base + month * 100,
                    "currency": "USD",
                }
            )
    pd.DataFrame(rows).to_csv(path, index=False)


@pytest.mark.asyncio
async def test_reingesting_identical_content_becomes_the_latest_dataset(
    db_session, seed_line_items, skill_context, tmp_path
):
    """Regression for the gap left by the fix above: ingest_actuals used to be
    idempotent on file_hash but never bumped ingested_at on the update-in-place
    branch, so re-uploading a dataset's exact original content after some other
    dataset had landed in between silently stayed "not latest" -- reproducing
    the same "forecast uses old data" symptom via a different code path.
    """
    from app.domain.skills.generate_baseline import GenerateBaselineSkill
    from app.domain.skills.ingest_actuals import IngestActualsSkill

    ingest = IngestActualsSkill()

    csv_a = tmp_path / "dataset_a.csv"
    _write_actuals_csv(csv_a, seed_line_items, base=100000.0)
    result_a1 = await ingest.execute({"file_path": str(csv_a)}, skill_context)
    assert result_a1.success, result_a1.message
    dataset_a_id = result_a1.data["dataset_id"]

    # Simulate A having been ingested a while ago, well before B below.
    dataset_a = db_session.get(ActualsDataset, dataset_a_id)
    dataset_a.ingested_at = datetime.now(timezone.utc) - timedelta(hours=2)
    db_session.commit()

    csv_b = tmp_path / "dataset_b.csv"
    _write_actuals_csv(csv_b, seed_line_items, base=200000.0)  # different content -> different hash
    result_b = await ingest.execute({"file_path": str(csv_b)}, skill_context)
    assert result_b.success, result_b.message
    dataset_b_id = result_b.data["dataset_id"]
    assert dataset_b_id != dataset_a_id

    latest = (
        db_session.query(ActualsDataset).order_by(ActualsDataset.ingested_at.desc()).first()
    )
    assert latest.id == dataset_b_id, "sanity check: B should be latest before the re-ingest"

    # Re-ingest A's exact original content (same file_hash) -- this must hit
    # the update-in-place branch and be recognized as "just given to us now".
    result_a2 = await ingest.execute({"file_path": str(csv_a)}, skill_context)
    assert result_a2.success, result_a2.message
    assert result_a2.data["dataset_id"] == dataset_a_id, (
        "expected the hash-matched re-ingest to update the existing dataset row"
    )

    latest = (
        db_session.query(ActualsDataset).order_by(ActualsDataset.ingested_at.desc()).first()
    )
    assert latest.id == dataset_a_id, (
        "re-ingesting identical content did not bump ingested_at, so it lost "
        "'latest dataset' to a dataset ingested earlier"
    )

    skill = GenerateBaselineSkill()
    result = await skill.execute(
        {"horizon_months": 3, "model_type": "linear", "random_seed": 42, "skip_plan_check": True},
        skill_context,
    )
    assert result.success, result.message
    version = (
        db_session.query(ForecastVersion)
        .filter(ForecastVersion.id == result.data["version_id"])
        .first()
    )
    assert version is not None
    assert version.actuals_dataset_id == dataset_a_id, (
        "generate_baseline picked the wrong dataset after a re-ingest of "
        "identical content"
    )
