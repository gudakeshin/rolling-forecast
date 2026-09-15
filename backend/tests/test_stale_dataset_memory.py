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
