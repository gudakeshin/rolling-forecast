"""ERP / warehouse / budget import integrations."""

from __future__ import annotations

import csv
import io
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models.user import User
from app.models.line_item import LineItem
from app.models.actuals import ActualsDataset, ActualsRecord
from app.models.budget import BudgetVersion, BudgetLineItem
from app.services.permissions import require_permission
from app.services.ingestion.erp_adapter import get_actuals_provider
from app.services.coa_dependencies import ensure_standard_dependencies
from app.services.audit import record_audit

router = APIRouter(prefix="/integrations", tags=["integrations"])


class WarehousePullRequest(BaseModel):
    connection_url: str | None = None
    query: str
    source_name: str = "warehouse"


class ERPPullRequest(BaseModel):
    url: str | None = None
    token: str | None = None
    source_name: str = "erp"


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
    return {
        "success": True,
        "dataset_id": dataset.id,
        "row_count": result.row_count,
        "dependencies_created": deps,
    }


@router.post("/warehouse/pull")
async def pull_warehouse(
    body: WarehousePullRequest,
    current_user: User = Depends(require_permission("generate")),
    db: Session = Depends(get_db),
):
    provider = get_actuals_provider("warehouse", connection_url=body.connection_url or settings.warehouse_connection_url)
    result = await provider.pull_actuals({
        "connection_url": body.connection_url or settings.warehouse_connection_url,
        "query": body.query,
        "source_name": body.source_name,
    })
    return await _persist_actuals_df(db, result, "warehouse", body.source_name, current_user)


@router.post("/erp/pull")
async def pull_erp(
    body: ERPPullRequest,
    current_user: User = Depends(require_permission("generate")),
    db: Session = Depends(get_db),
):
    provider = get_actuals_provider("erp")
    result = await provider.pull_actuals({
        "url": body.url or settings.erp_api_url,
        "token": body.token or settings.erp_api_token,
        "source_name": body.source_name,
    })
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
    raw = await file.read()
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
        source_name=file.filename,
        created_by=current_user.id,
    )
    db.add(budget)
    db.flush()

    existing = {li.account_code: li for li in db.query(LineItem).all()}
    created = 0
    for row in rows:
        # normalize keys
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
