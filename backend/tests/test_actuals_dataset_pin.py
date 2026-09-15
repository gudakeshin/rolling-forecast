"""Manual actuals uploads must outrank unattended scheduled/API pulls.

ActualsDataset used to have no concept of precedence: "current dataset" was
resolved purely by ingested_at, so a nightly scheduled sync (see
app.workers.arq_worker.scheduled_integration_sync, a real cron job registered
to run at 02:00 for every IntegrationConnection with auto_pull_enabled=True)
landing after a user's manual upload would silently become the new "latest"
dataset and drive the next forecast instead of what the user just uploaded —
with no warning. These tests cover the fix: manual ingestion pins its
dataset (ActualsDataset.is_pinned), resolve_current_dataset prefers the
latest pinned dataset over anything ingested since, and there's a skill-driven
way to hand control back (manage_actuals_dataset action=unpin).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from app.models.actuals import ActualsDataset
from app.models.forecast import ForecastVersion
from app.services.actuals_resolution import dataset_is_current, resolve_current_dataset


def _write_actuals_csv(path, seed_line_items, base: float) -> None:
    rows = []
    for code, li in seed_line_items.items():
        if li.is_calculated:
            continue
        for month in range(24):
            year = 2024 + month // 12
            m = (month % 12) + 1
            rows.append({
                "account_code": code,
                "account_name": li.name,
                "category": li.category,
                "period": f"{year}-{m:02d}",
                "value": base + month * 100,
                "currency": "USD",
            })
    pd.DataFrame(rows).to_csv(path, index=False)


async def _pull(db_session, seed_line_items, base: float, *, connection_id: str | None = None):
    """Simulate a scheduled/API pull through the real persistence path."""
    from app.services.actuals_ingest import persist_pulled_actuals
    from app.services.ingestion.base import IngestionResult

    rows = []
    for code, li in seed_line_items.items():
        if li.is_calculated:
            continue
        for month in range(24):
            year = 2024 + month // 12
            m = (month % 12) + 1
            rows.append({
                "account_code": code, "account_name": li.name, "category": li.category,
                "period": f"{year}-{m:02d}", "value": base + month * 100, "currency": "USD",
            })
    df = pd.DataFrame(rows)
    result = IngestionResult(
        success=True, dataframe=df, row_count=len(df),
        period_start="2024-01", period_end="2025-12", periods_count=24,
        completeness_pct=100.0, file_hash=f"pull-hash-{base}",
    )
    return await persist_pulled_actuals(
        db_session, result, "warehouse", "erp-nightly",
        actor_id=None, actor_username="system:scheduler",
        integration_connection_id=connection_id,
    )


@pytest.mark.asyncio
async def test_manual_ingest_pins_dataset(db_session, seed_line_items, skill_context, tmp_path):
    from app.domain.skills.ingest_actuals import IngestActualsSkill

    csv_path = tmp_path / "manual.csv"
    _write_actuals_csv(csv_path, seed_line_items, base=100000.0)
    result = await IngestActualsSkill().execute({"file_path": str(csv_path)}, skill_context)
    assert result.success, result.message

    dataset = db_session.get(ActualsDataset, result.data["dataset_id"])
    assert dataset.is_pinned is True


@pytest.mark.asyncio
async def test_scheduled_pull_does_not_pin_and_records_connection(db_session, seed_line_items):
    outcome = await _pull(db_session, seed_line_items, base=200000.0, connection_id="conn-abc")
    dataset = db_session.get(ActualsDataset, outcome["dataset_id"])
    assert dataset.is_pinned is False
    assert dataset.integration_connection_id == "conn-abc"


@pytest.mark.asyncio
async def test_resolve_current_dataset_prefers_pinned_over_later_unpinned(
    db_session, seed_line_items, skill_context, tmp_path
):
    from app.domain.skills.ingest_actuals import IngestActualsSkill

    csv_path = tmp_path / "manual.csv"
    _write_actuals_csv(csv_path, seed_line_items, base=100000.0)
    manual = await IngestActualsSkill().execute({"file_path": str(csv_path)}, skill_context)
    assert manual.success
    manual_id = manual.data["dataset_id"]

    # A scheduled pull lands strictly after the manual upload.
    later_pull = await _pull(db_session, seed_line_items, base=200000.0)
    pulled_id = later_pull["dataset_id"]
    assert pulled_id != manual_id

    current = resolve_current_dataset(db_session)
    assert current.id == manual_id, "a later unpinned pull must not outrank a manual upload"
    assert dataset_is_current(db_session, manual_id) is True
    assert dataset_is_current(db_session, pulled_id) is False


@pytest.mark.asyncio
async def test_resolve_current_dataset_falls_back_to_latest_when_nothing_pinned(
    db_session, seed_line_items
):
    first = await _pull(db_session, seed_line_items, base=100000.0)
    # Force a distinguishable ordering rather than relying on wall-clock gaps.
    db_session.get(ActualsDataset, first["dataset_id"]).ingested_at = (
        datetime.now(timezone.utc) - timedelta(hours=1)
    )
    db_session.commit()
    second = await _pull(db_session, seed_line_items, base=200000.0)

    current = resolve_current_dataset(db_session)
    assert current.id == second["dataset_id"], (
        "with nothing pinned, resolution must still fall back to plain latest-overall"
    )


@pytest.mark.asyncio
async def test_generate_baseline_resolves_to_pinned_dataset(
    db_session, seed_line_items, skill_context, tmp_path
):
    from app.domain.skills.generate_baseline import GenerateBaselineSkill
    from app.domain.skills.ingest_actuals import IngestActualsSkill

    csv_path = tmp_path / "manual.csv"
    _write_actuals_csv(csv_path, seed_line_items, base=100000.0)
    manual = await IngestActualsSkill().execute({"file_path": str(csv_path)}, skill_context)
    assert manual.success
    manual_id = manual.data["dataset_id"]

    await _pull(db_session, seed_line_items, base=200000.0)  # lands after, unpinned

    skill = GenerateBaselineSkill()
    result = await skill.execute(
        {"horizon_months": 3, "model_type": "linear", "random_seed": 42, "skip_plan_check": True},
        skill_context,
    )
    assert result.success, result.message
    version = db_session.query(ForecastVersion).filter(
        ForecastVersion.id == result.data["version_id"]
    ).first()
    assert version.actuals_dataset_id == manual_id


@pytest.mark.asyncio
async def test_unpin_releases_control_to_latest_overall(
    db_session, seed_line_items, skill_context, tmp_path
):
    from app.domain.skills.ingest_actuals import IngestActualsSkill
    from app.domain.skills.manage_actuals_dataset import (
        ListActualsDatasetsSkill,
        ManageActualsDatasetSkill,
    )

    csv_path = tmp_path / "manual.csv"
    _write_actuals_csv(csv_path, seed_line_items, base=100000.0)
    manual = await IngestActualsSkill().execute({"file_path": str(csv_path)}, skill_context)
    manual_id = manual.data["dataset_id"]
    pulled = await _pull(db_session, seed_line_items, base=200000.0)
    pulled_id = pulled["dataset_id"]

    listed = await ListActualsDatasetsSkill().execute({}, skill_context)
    assert listed.data["current_dataset_id"] == manual_id

    unpin = await ManageActualsDatasetSkill().execute({"action": "unpin"}, skill_context)
    assert unpin.success, unpin.message
    assert unpin.data["current_dataset_id"] == pulled_id

    assert resolve_current_dataset(db_session).id == pulled_id
    assert db_session.get(ActualsDataset, manual_id).is_pinned is False

    # Unpinning again is a no-op, not an error.
    again = await ManageActualsDatasetSkill().execute(
        {"action": "unpin", "dataset_id": manual_id}, skill_context
    )
    assert again.success
