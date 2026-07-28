"""File upload endpoint for actuals and driver data."""

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from sqlalchemy.orm import Session

from app.api.auth import get_current_user
from app.database import get_db
from app.models.user import User
from app.rate_limit import limiter
from app.services.permissions import require_permission
from app.services.upload_safety import save_upload

router = APIRouter(prefix="/upload", tags=["upload"])


@router.post("/actuals")
@limiter.limit("20/minute")
async def upload_actuals(
    request: Request,
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Upload a CSV/Excel file containing actuals data.

    Client filename is metadata only — storage uses a UUID path.
    """
    _ = (request, current_user, db)  # auth + rate-limit deps
    saved = await save_upload(file, allowed_extensions={".csv", ".xlsx", ".xls"})
    return {
        "filename": saved["original_name"],
        "file_path": saved["stored_path"],
        "size_bytes": saved["size_bytes"],
        "message": (
            f"File '{saved['original_name']}' uploaded successfully. "
            "Use the chat to ingest it into the forecast system."
        ),
    }


@router.post("/drivers")
@limiter.limit("20/minute")
async def upload_drivers(
    request: Request,
    file: UploadFile = File(...),
    ingest: bool = Query(True, description="Parse and upsert into drivers immediately"),
    current_user: User = Depends(require_permission("manage_drivers")),
    db: Session = Depends(get_db),
):
    """Upload a CSV/Excel of driver series (driver_key, period, value[, currency]).

    Periods are normalized via the fiscal calendar (same as actuals). When
    ``ingest=true`` (default), rows are upserted into ``drivers`` /
    ``driver_values`` with lineage hashing and audit.
    """
    _ = request
    saved = await save_upload(file, allowed_extensions={".csv", ".xlsx", ".xls"})
    payload: dict = {
        "filename": saved["original_name"],
        "file_path": saved["stored_path"],
        "size_bytes": saved["size_bytes"],
    }
    if not ingest:
        payload["message"] = (
            f"File '{saved['original_name']}' uploaded. "
            "Re-post with ingest=true to load drivers."
        )
        return payload

    from app.services.driver_ingest import ingest_drivers_file

    result = await ingest_drivers_file(
        db,
        file_path=saved["stored_path"],
        actor=current_user,
        source_name=saved["original_name"],
    )
    if not result.get("success"):
        raise HTTPException(400, result.get("error") or "Driver ingest failed")
    payload["ingest"] = result
    payload["message"] = (
        f"Ingested {result.get('row_count', 0)} rows across "
        f"{result.get('drivers_touched', 0)} drivers "
        f"({result.get('drivers_created', 0)} new)."
    )
    return payload
