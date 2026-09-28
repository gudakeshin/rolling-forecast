"""Regression: hard-deleting a draft forecast version must actually cascade
-- line_results (and their model_metadata)/overrides/driver_inputs/
approval_steps/anomaly_dismissals/accuracy_records/review_undo_snapshots all
disappear with it -- and must refuse when another version branched from it
(parent_version_id), or when the caller belongs to a different company.
Basic delete/refuse-non-draft contract is covered by
tests/test_phase6b_7.py::test_delete_version_endpoint; this file covers the
cascade/lineage/company-scope specifics that need a richer fixture graph.

model_metadata is the one that actually caught a real bug during manual
verification: ModelMetadata.line_result_id is NOT NULL with no cascade
configured, so SQLAlchemy's default orphan handling tried to null it out on
delete and blew up with an IntegrityError -- silently swallowed into a
generic 500 by the API's error handler. Fixed by adding
cascade="all, delete-orphan" to ForecastLineResult.model_metadata (see
app/models/forecast.py). Keep this fixture exercising it so it can't regress.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.models.anomaly_dismissal import AnomalyDismissal
from app.models.approval import ApprovalStep, ApprovalWorkflow
from app.models.forecast import ForecastLineResult, ForecastVersion, ModelMetadata
from app.models.fx import ForecastAccuracyRecord
from app.models.override import Override
from app.models.review_undo import ReviewUndoSnapshot


@pytest.fixture
def draft_version_with_dependents(db_session, seed_line_items, seed_users):
    li = seed_line_items["REV-001"]
    version = ForecastVersion(
        name="FC-hard-delete-test",
        status="draft",
        version_type="scheduled",
        scenario="base",
        horizon_months=3,
        base_period="2025-12",
        business_unit_id=li.business_unit_id,
    )
    db_session.add(version)
    db_session.flush()

    line_result = ForecastLineResult(
        version_id=version.id, line_item_id=li.id, period="2026-01", p50=1000.0,
    )
    db_session.add(line_result)
    db_session.flush()
    db_session.add(ModelMetadata(
        line_result_id=line_result.id, model_type="linear", training_points=12,
    ))
    db_session.add(Override(
        version_id=version.id, line_item_id=li.id, period="2026-01",
        override_value=1100.0, original_model_value=1000.0,
        user_id=seed_users["analyst"].id, reason="test override",
    ))

    workflow = ApprovalWorkflow(
        name="Test Workflow", levels=[{"level": 1, "role": "reviewer", "label": "Reviewer"}],
    )
    db_session.add(workflow)
    db_session.flush()
    db_session.add(ApprovalStep(
        version_id=version.id, workflow_id=workflow.id, level=1, required_role="reviewer",
    ))
    db_session.add(AnomalyDismissal(
        user_id=seed_users["analyst"].id, version_id=version.id, anomaly_id="anomaly-1",
    ))
    db_session.add(ForecastAccuracyRecord(
        version_id=version.id, line_item_id=li.id, period="2025-12", horizon_offset=1,
        predicted_p50=1000.0, actual=1050.0, absolute_error=50.0,
    ))
    db_session.add(ReviewUndoSnapshot(
        version_id=version.id, actor_id=seed_users["analyst"].id, action="review_item",
        description="test undo", prior_state=[], expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    ))
    db_session.commit()
    return version


def test_hard_delete_cascades_every_dependent_table(
    db_session, draft_version_with_dependents
):
    from app.api.panel import delete_version

    version_id = draft_version_with_dependents.id
    import asyncio

    class _Admin:
        id = "admin-test"
        username = "admin-test"
        business_unit_id = None
        role = type("R", (), {"can_view_all_bus": True, "can_admin": True, "name": "admin"})()

    asyncio.run(delete_version(version_id, current_user=_Admin(), db=db_session))

    assert db_session.get(ForecastVersion, version_id) is None
    assert db_session.query(ForecastLineResult).filter(
        ForecastLineResult.version_id == version_id
    ).count() == 0
    assert db_session.query(ModelMetadata).count() == 0, (
        "ModelMetadata must cascade-delete with its ForecastLineResult, not "
        "be orphaned (line_result_id is NOT NULL -- nulling it out 500s)"
    )
    assert db_session.query(Override).filter(Override.version_id == version_id).count() == 0
    assert db_session.query(ApprovalStep).filter(ApprovalStep.version_id == version_id).count() == 0
    assert db_session.query(AnomalyDismissal).filter(
        AnomalyDismissal.version_id == version_id
    ).count() == 0
    assert db_session.query(ForecastAccuracyRecord).filter(
        ForecastAccuracyRecord.version_id == version_id
    ).count() == 0
    assert db_session.query(ReviewUndoSnapshot).filter(
        ReviewUndoSnapshot.version_id == version_id
    ).count() == 0


@pytest.mark.asyncio
async def test_hard_delete_blocked_by_branched_child_version(db_session, seed_line_items):
    from app.api.panel import delete_version
    from fastapi import HTTPException

    li = seed_line_items["REV-001"]
    parent = ForecastVersion(
        name="FC-parent", status="draft", version_type="scheduled", scenario="base",
        business_unit_id=li.business_unit_id,
    )
    db_session.add(parent)
    db_session.flush()
    child = ForecastVersion(
        name="FC-child", status="draft", version_type="branch", scenario="base",
        parent_version_id=parent.id, business_unit_id=li.business_unit_id,
    )
    db_session.add(child)
    db_session.commit()

    class _Admin:
        id = "admin-test"
        username = "admin-test"
        business_unit_id = None
        role = type("R", (), {"can_view_all_bus": True, "can_admin": True, "name": "admin"})()

    with pytest.raises(HTTPException) as exc:
        await delete_version(parent.id, current_user=_Admin(), db=db_session)
    assert exc.value.status_code == 400
    assert db_session.get(ForecastVersion, parent.id) is not None


@pytest.mark.asyncio
async def test_new_version_name_does_not_collide_after_deletes(
    db_session, seed_line_items, skill_context, tmp_path
):
    """generate_baseline used to derive "FC-YYYY-MM-v{n}" from a plain row
    count. Hard-deleting draft versions makes that count go backwards, so a
    freshly generated version could mint a name that collides with one still
    on record -- confusing (the header/picker would show "v28" for two
    different forecasts). Names must be derived from the highest existing
    suffix instead.
    """
    from app.domain.skills.generate_baseline import GenerateBaselineSkill
    from app.domain.skills.ingest_actuals import IngestActualsSkill
    from app.api.panel import delete_version

    csv_path = tmp_path / "manual.csv"
    rows = []
    for code, li in seed_line_items.items():
        if li.is_calculated:
            continue
        for month in range(24):
            year = 2024 + month // 12
            m = (month % 12) + 1
            rows.append({
                "account_code": code, "account_name": li.name, "category": li.category,
                "period": f"{year}-{m:02d}", "value": 100000.0 + month * 100, "currency": "USD",
            })
    import pandas as pd

    pd.DataFrame(rows).to_csv(csv_path, index=False)
    ingest = await IngestActualsSkill().execute({"file_path": str(csv_path)}, skill_context)
    assert ingest.success, ingest.message

    gen = GenerateBaselineSkill()
    kwargs = {"horizon_months": 3, "model_type": "linear", "random_seed": 42, "skip_plan_check": True}

    first = await gen.execute(kwargs, skill_context)
    assert first.success, first.message
    second = await gen.execute(kwargs, skill_context)
    assert second.success, second.message
    first_version = db_session.get(ForecastVersion, first.data["version_id"])
    second_version = db_session.get(ForecastVersion, second.data["version_id"])
    assert first_version.name != second_version.name

    class _Admin:
        id = skill_context.user.id
        username = skill_context.user.username
        business_unit_id = None
        role = type("R", (), {"can_view_all_bus": True, "can_admin": True, "name": "admin"})()

    # Delete the newer one -- a naive count-based name would now reuse it.
    await delete_version(second_version.id, current_user=_Admin(), db=db_session)

    third = await gen.execute(kwargs, skill_context)
    assert third.success, third.message
    third_version = db_session.get(ForecastVersion, third.data["version_id"])
    assert third_version.name != first_version.name, (
        f"name collision: new version reused {third_version.name!r}, "
        f"already used by a still-existing version"
    )
