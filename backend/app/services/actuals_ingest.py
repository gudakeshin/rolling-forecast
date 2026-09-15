"""Shared actuals-persistence path for warehouse/ERP pulls.

Used by both the interactive `/integrations/*/pull` routes and the unattended
nightly sync cron job (`app.workers.arq_worker.scheduled_integration_sync`), so
a scheduled pull and a manual one create identical datasets/records/audit
trails and both trigger the same accuracy-snapshot matching.
"""

from __future__ import annotations

import logging

from fastapi import HTTPException
from sqlalchemy.orm import Session

from sqlalchemy import func

from app.models.actuals import ActualsDataset, ActualsRecord
from app.models.fx import ForecastAccuracyRecord
from app.models.line_item import LineItem
from app.services.accuracy_snapshot import AccuracySnapshotService
from app.services.audit import record_audit
from app.services.coa_dependencies import ensure_standard_dependencies
from app.services.notifications import notify_users, users_with_permission

logger = logging.getLogger(__name__)


def _notify_actuals_ingested(db: Session, dataset, source_name: str, line_map: dict, df) -> None:
    """Notify FP&A (can_generate) users, highlighting the biggest movers if the
    freshly-computed ForecastAccuracyRecord rows give us one cheaply."""
    li_ids = [li.id for li in line_map.values()]
    periods = sorted({str(p) for p in df["period"].unique()})
    top_movers = (
        db.query(ForecastAccuracyRecord)
        .filter(
            ForecastAccuracyRecord.line_item_id.in_(li_ids),
            ForecastAccuracyRecord.period.in_(periods),
            ForecastAccuracyRecord.pct_error.isnot(None),
        )
        .order_by(func.abs(ForecastAccuracyRecord.pct_error).desc())
        .limit(3)
        .all()
    )
    if top_movers:
        li_names = {li.id: li.name for li in line_map.values()}
        body = "; ".join(
            f"{li_names.get(r.line_item_id, 'A line item')} ({r.period}): "
            f"{r.pct_error:+.1%} vs forecast"
            for r in top_movers
        )
    else:
        body = f"{len(df)} new actuals row(s) landed — no prior forecast to compare against yet."

    recipients = users_with_permission(db, "can_generate")
    notify_users(
        db,
        recipients,
        kind="actuals_ingested",
        title=f"New actuals: {source_name}",
        body=body,
        entity_type="actuals_dataset",
        entity_id=dataset.id,
        link_panel="accuracy_tracking",
        link_params={},
        dedup_key_prefix=f"actuals_ingested:{dataset.id}",
    )
    db.commit()


async def persist_pulled_actuals(
    db: Session,
    result,
    source_type: str,
    source_name: str,
    *,
    actor_id: str | None,
    actor_username: str,
    integration_connection_id: str | None = None,
) -> dict:
    """Persist a successful `IngestionResult` and run accuracy-snapshot matching.

    Raises HTTPException(400) if `result` reports failure — callers running
    outside a request cycle (the cron job) should catch that themselves.

    Never pinned (`is_pinned` stays False) -- this is always an automated
    pull (interactive `/integrations/*/pull` or the nightly cron), never the
    manual chat/UI upload path (`app.domain.skills.ingest_actuals`), so it
    never outranks a manually uploaded dataset. See
    app/services/actuals_resolution.py.
    """
    if not result.success:
        raise HTTPException(400, result.error or "Pull failed")
    df = result.dataframe
    dataset = ActualsDataset(
        source_type=source_type,
        source_name=source_name,
        file_hash=result.file_hash,
        row_count=result.row_count,
        period_start=result.period_start,
        period_end=result.period_end,
        periods_count=result.periods_count,
        completeness_pct=result.completeness_pct,
        integration_connection_id=integration_connection_id,
    )
    db.add(dataset)
    db.flush()

    existing = {li.account_code: li for li in db.query(LineItem).all()}
    line_map = {}
    for _, row in df.groupby("account_code").first().reset_index().iterrows():
        code = str(row["account_code"])
        if code in existing:
            line_map[code] = existing[code]
        else:
            li = LineItem(
                account_code=code,
                name=str(row.get("account_name", code)),
                category=str(row.get("category", "Other")),
                business_unit=str(row["business_unit"]) if "business_unit" in row.index else None,
            )
            db.add(li)
            db.flush()
            line_map[code] = li
            existing[code] = li

    records = []
    for _, row in df.iterrows():
        code = str(row["account_code"])
        mapped_li = line_map.get(code)
        if mapped_li:
            records.append(ActualsRecord(
                dataset_id=dataset.id,
                line_item_id=mapped_li.id,
                period=str(row["period"]),
                value=float(row["value"]),
                currency=str(row.get("currency", "USD")),
            ))
    db.bulk_save_objects(records)
    deps = ensure_standard_dependencies(db, list(line_map.values()))
    record_audit(
        db,
        action="integrations.pull_actuals",
        entity_type="actuals_dataset",
        entity_id=dataset.id,
        actor_id=actor_id,
        actor_username=actor_username,
        details={"source_type": source_type, "rows": result.row_count, "deps": deps},
    )
    db.commit()
    accuracy_rows = 0
    try:
        accuracy_rows = AccuracySnapshotService(db).on_actuals_ingested(dataset.id)
        if accuracy_rows:
            db.commit()
    except Exception:
        logger.exception("Accuracy snapshot failed after %s pull", source_type)
        db.rollback()

    try:
        _notify_actuals_ingested(db, dataset, source_name, line_map, df)
    except Exception:
        logger.exception("Actuals-ingested notification failed for dataset %s", dataset.id)
        db.rollback()

    return {
        "success": True,
        "dataset_id": dataset.id,
        "row_count": result.row_count,
        "dependencies_created": deps,
        "accuracy_records": accuracy_rows,
    }
