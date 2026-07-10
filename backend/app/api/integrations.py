"""ERP / warehouse / budget import integrations — connection-registry based."""

from __future__ import annotations

import csv
import io
import logging

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.actuals import ActualsDataset, ActualsRecord
from app.models.budget import BudgetLineItem, BudgetVersion
from app.models.integration import IntegrationConnection
from app.models.line_item import LineItem
from app.models.user import User
from app.services.accuracy_snapshot import AccuracySnapshotService
from app.services.audit import record_audit
from app.services.coa_dependencies import ensure_standard_dependencies
from app.services.ingestion.erp_adapter import get_actuals_provider
from app.services.permissions import require_permission
from app.services.secret_box import decrypt_secret

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/integrations", tags=["integrations"])


class WarehousePullRequest(BaseModel):
    connection_id: str
    query: str
    source_name: str = "warehouse"


class ERPPullRequest(BaseModel):
    connection_id: str
    relative_path: str = Field(..., description="Path relative to the registered ERP base URL")
    source_name: str = "erp"


def _load_connection(db: Session, connection_id: str, kind: str) -> IntegrationConnection:
    conn = (
        db.query(IntegrationConnection)
        .filter(IntegrationConnection.id == connection_id)
        .first()
    )
    if not conn:
        raise HTTPException(404, "Integration connection not found")
    if not conn.enabled:
        raise HTTPException(400, "Integration connection is disabled")
    if conn.kind != kind:
        raise HTTPException(400, f"Connection kind must be '{kind}', got '{conn.kind}'")
    return conn


async def _persist_actuals_df(db: Session, result, source_type: str, source_name: str, user: User):
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
        li = line_map.get(code)
        if li:
            records.append(ActualsRecord(
                dataset_id=dataset.id,
                line_item_id=li.id,
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
        actor_id=user.id,
        actor_username=user.username,
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
    return {
        "success": True,
        "dataset_id": dataset.id,
        "row_count": result.row_count,
        "dependencies_created": deps,
        "accuracy_records": accuracy_rows,
    }


@router.post("/warehouse/pull")
async def pull_warehouse(
    body: WarehousePullRequest,
    current_user: User = Depends(require_permission("generate")),
    db: Session = Depends(get_db),
):
    conn = _load_connection(db, body.connection_id, "warehouse")
    try:
        url = decrypt_secret(conn.encrypted_url)
    except ValueError as e:
        raise HTTPException(500, str(e)) from e

    provider = get_actuals_provider("warehouse", connection_url=url)
    result = await provider.pull_actuals({
        "query": body.query,
        "source_name": body.source_name,
    })
    if not result.success and result.error and "Only SELECT" in result.error:
        raise HTTPException(400, result.error)
    if not result.success and result.error and "single SELECT" in result.error:
        raise HTTPException(400, result.error)
    if not result.success and result.error and "Forbidden SQL" in result.error:
        raise HTTPException(400, result.error)

    record_audit(
        db,
        action="integrations.warehouse_pull",
        entity_type="integration_connection",
        entity_id=conn.id,
        actor_id=current_user.id,
        actor_username=current_user.username,
        details={"connection_name": conn.name, "success": result.success},
        commit=False,
    )
    return await _persist_actuals_df(db, result, "warehouse", body.source_name, current_user)


@router.post("/erp/pull")
async def pull_erp(
    body: ERPPullRequest,
    current_user: User = Depends(require_permission("generate")),
    db: Session = Depends(get_db),
):
    conn = _load_connection(db, body.connection_id, "erp")
    try:
        base_url = decrypt_secret(conn.encrypted_url)
        token = decrypt_secret(conn.encrypted_token) if conn.encrypted_token else None
    except ValueError as e:
        raise HTTPException(500, str(e)) from e

    provider = get_actuals_provider("erp", base_url=base_url, token=token)
    result = await provider.pull_actuals({
        "relative_path": body.relative_path,
        "source_name": body.source_name,
    })
    record_audit(
        db,
        action="integrations.erp_pull",
        entity_type="integration_connection",
        entity_id=conn.id,
        actor_id=current_user.id,
        actor_username=current_user.username,
        details={"connection_name": conn.name, "path": body.relative_path, "success": result.success},
        commit=False,
    )
    return await _persist_actuals_df(db, result, "erp", body.source_name, current_user)


@router.post("/budget/import")
async def import_budget(
    fiscal_year: int,
    name: str | None = None,
    file: UploadFile = File(...),
    current_user: User = Depends(require_permission("generate")),
    db: Session = Depends(get_db),
):
    """Import budget CSV with columns: account_code, period, value [, account_name, category]."""
    from app.services.upload_safety import save_upload

    saved = await save_upload(file, allowed_extensions={".csv"})
    with open(saved["stored_path"], "rb") as fh:
        raw = fh.read()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("latin-1")
    reader = csv.DictReader(io.StringIO(text))
    rows = list(reader)
    if not rows:
        raise HTTPException(400, "Empty budget file")

    required = {"account_code", "period", "value"}
    cols = {c.lower() for c in (reader.fieldnames or [])}
    if not required.issubset(cols):
        raise HTTPException(400, f"Budget CSV requires columns: {sorted(required)}")

    budget = BudgetVersion(
        name=name or f"Budget FY{fiscal_year}",
        fiscal_year=fiscal_year,
        status="active",
        source_name=saved["original_name"],
        created_by=current_user.id,
    )
    db.add(budget)
    db.flush()

    existing = {li.account_code: li for li in db.query(LineItem).all()}
    created = 0
    for row in rows:
        row = {k.lower(): v for k, v in row.items()}
        code = str(row["account_code"])
        if code not in existing:
            li = LineItem(
                account_code=code,
                name=str(row.get("account_name", code)),
                category=str(row.get("category", "Other")),
            )
            db.add(li)
            db.flush()
            existing[code] = li
        db.add(BudgetLineItem(
            budget_version_id=budget.id,
            line_item_id=existing[code].id,
            period=str(row["period"]),
            value=float(row["value"]),
        ))
        created += 1

    record_audit(
        db,
        action="integrations.budget_import",
        entity_type="budget_version",
        entity_id=budget.id,
        actor_id=current_user.id,
        actor_username=current_user.username,
        details={"rows": created, "fiscal_year": fiscal_year},
    )
    db.commit()
    return {"success": True, "budget_version_id": budget.id, "rows": created}


@router.get("/budgets")
async def list_budgets(
    current_user: User = Depends(require_permission("generate")),
    db: Session = Depends(get_db),
):
    budgets = db.query(BudgetVersion).order_by(BudgetVersion.fiscal_year.desc()).all()
    return [
        {
            "id": b.id,
            "name": b.name,
            "fiscal_year": b.fiscal_year,
            "status": b.status,
            "source_name": b.source_name,
        }
        for b in budgets
    ]
