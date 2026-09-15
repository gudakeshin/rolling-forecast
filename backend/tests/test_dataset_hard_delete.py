"""Regression: manage_actuals_dataset's `delete` action must actually delete --
the dataset row, its actuals records (cascade), and the uploaded file on
disk -- while refusing when a forecast version still depends on it or the
caller belongs to a different company.
"""

from __future__ import annotations

import pandas as pd
import pytest

from app.models.actuals import ActualsDataset, ActualsRecord


def _write_csv(path, seed_line_items, base: float) -> None:
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
    pd.DataFrame(rows).to_csv(path, index=False)


@pytest.mark.asyncio
async def test_delete_removes_dataset_records_and_file(
    db_session, seed_line_items, skill_context, tmp_path
):
    from app.domain.skills.ingest_actuals import IngestActualsSkill
    from app.domain.skills.manage_actuals_dataset import ManageActualsDatasetSkill

    csv_path = tmp_path / "manual.csv"
    _write_csv(csv_path, seed_line_items, base=100000.0)
    result = await IngestActualsSkill().execute({"file_path": str(csv_path)}, skill_context)
    assert result.success, result.message
    dataset_id = result.data["dataset_id"]

    dataset = db_session.get(ActualsDataset, dataset_id)
    assert dataset.storage_path == str(csv_path)
    assert csv_path.exists()
    record_count = (
        db_session.query(ActualsRecord).filter(ActualsRecord.dataset_id == dataset_id).count()
    )
    assert record_count > 0

    outcome = await ManageActualsDatasetSkill().execute(
        {"action": "delete", "dataset_id": dataset_id}, skill_context
    )
    assert outcome.success, outcome.message

    assert db_session.get(ActualsDataset, dataset_id) is None
    assert (
        db_session.query(ActualsRecord).filter(ActualsRecord.dataset_id == dataset_id).count() == 0
    )
    assert not csv_path.exists()


@pytest.mark.asyncio
async def test_delete_blocked_when_forecast_version_depends_on_it(
    db_session, seed_line_items, skill_context, tmp_path
):
    from app.domain.skills.generate_baseline import GenerateBaselineSkill
    from app.domain.skills.ingest_actuals import IngestActualsSkill
    from app.domain.skills.manage_actuals_dataset import ManageActualsDatasetSkill

    csv_path = tmp_path / "manual.csv"
    _write_csv(csv_path, seed_line_items, base=100000.0)
    ingest_result = await IngestActualsSkill().execute({"file_path": str(csv_path)}, skill_context)
    dataset_id = ingest_result.data["dataset_id"]

    gen_result = await GenerateBaselineSkill().execute(
        {"horizon_months": 3, "model_type": "linear", "random_seed": 42, "skip_plan_check": True},
        skill_context,
    )
    assert gen_result.success, gen_result.message

    outcome = await ManageActualsDatasetSkill().execute(
        {"action": "delete", "dataset_id": dataset_id}, skill_context
    )
    assert not outcome.success
    assert db_session.get(ActualsDataset, dataset_id) is not None, "blocked delete must not touch the row"


@pytest.mark.asyncio
async def test_delete_tolerates_missing_storage_path(db_session, seed_line_items, skill_context, tmp_path):
    """api/warehouse-sourced datasets (and pre-storage_path legacy rows) have
    no file to remove -- delete must still succeed."""
    from app.domain.skills.manage_actuals_dataset import ManageActualsDatasetSkill

    li = next(iter(seed_line_items.values()))
    dataset = ActualsDataset(
        source_type="api",
        source_name="erp-nightly",
        file_hash="no-file-hash",
        business_unit_id=li.business_unit_id,
        row_count=0,
        period_start="2024-01",
        period_end="2024-01",
        periods_count=1,
        storage_path=None,
    )
    db_session.add(dataset)
    db_session.commit()

    outcome = await ManageActualsDatasetSkill().execute(
        {"action": "delete", "dataset_id": dataset.id}, skill_context
    )
    assert outcome.success, outcome.message
    assert db_session.get(ActualsDataset, dataset.id) is None
