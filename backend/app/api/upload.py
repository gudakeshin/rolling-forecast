"""File upload endpoint for actuals and driver data."""

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from sqlalchemy.orm import Session

from app.api.auth import get_current_user
from app.database import get_db
from app.models.actuals import ActualsDataset
from app.models.user import User
from app.rate_limit import limiter
from app.services.permissions import can_access_business_unit, can_view_all_bus, require_permission
from app.services.upload_safety import save_upload

router = APIRouter(prefix="/upload", tags=["upload"])


class _StubContextManager:
    """Minimal context manager for non-chat (REST-triggered) skill execution.

    Mirrors app.workers.arq_worker._StubContextManager / app.services.model_presets._StubContextManager
    — duplicated rather than imported to avoid pulling the arq worker module
    (and its `arq` dependency) into the API process for a plain REST request.
    """

    def __init__(self, user_id: str):
        self._mem: dict = {}
        self._user_id = user_id

    def get_memory(self, key: str, default=None):
        return self._mem.get(key, default)

    def set_memory(self, key: str, value) -> None:
        self._mem[key] = value

    def get_active_version_id(self) -> str | None:
        return self._mem.get("active_version_id")


@router.post("/actuals")
@limiter.limit("20/minute")
async def upload_actuals(
    request: Request,
    file: UploadFile = File(...),
    ingest: bool = Query(
        True, description="Parse and ingest into the forecast system immediately"
    ),
    business_unit: str | None = Form(
        None,
        description=(
            "Company this data belongs to (name, or id of an existing one); required unless the "
            "caller belongs to exactly one company. A cross-company caller (can_view_all_bus) "
            "naming a company that doesn't exist yet gets it created on the fly -- ingestion "
            "itself never creates one, to stop an ordinary upload from spinning up new companies "
            "via a typo (see app/services/business_units.py)."
        ),
    ),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Upload a CSV/Excel file containing actuals data.

    Client filename is metadata only — storage uses a UUID path. When
    ``ingest=true`` (default), the file is parsed and loaded immediately —
    mirroring ``/upload/drivers`` — instead of leaving ingestion to a
    separate chat round-trip a caller might never make (a document-library
    style upload UI has no chat turn to hang that on at all).
    """
    _ = request  # rate-limit dep
    saved = await save_upload(file, allowed_extensions={".csv", ".xlsx", ".xls"})
    payload: dict = {
        "filename": saved["original_name"],
        "file_path": saved["stored_path"],
        "size_bytes": saved["size_bytes"],
    }
    if not ingest:
        payload["message"] = (
            f"File '{saved['original_name']}' uploaded successfully. "
            "Use the chat to ingest it into the forecast system."
        )
        return payload

    from app.domain.base_skill import SkillContext
    from app.domain.skills.ingest_actuals import IngestActualsSkill
    from app.services.permissions import can_view_all_bus

    if business_unit and can_view_all_bus(current_user):
        from app.models.business_unit import BusinessUnit

        existing = (
            db.query(BusinessUnit)
            .filter((BusinessUnit.id == business_unit) | (BusinessUnit.name == business_unit))
            .first()
        )
        if existing is None:
            from app.services.business_units import get_or_create_business_unit

            get_or_create_business_unit(db, business_unit)
            db.commit()

    context = SkillContext(
        db=db,
        context_manager=_StubContextManager(current_user.id),  # type: ignore[arg-type]
        user_id=current_user.id,
        user_role="input",
        conversation_id=f"upload-actuals-{saved['stored_path']}",
        user=current_user,
    )
    params: dict = {"file_path": saved["stored_path"]}
    if business_unit:
        params["business_unit"] = business_unit
    result = await IngestActualsSkill().execute(params, context)
    if not result.success:
        raise HTTPException(400, result.message)
    payload["ingest"] = result.data
    payload["message"] = result.message
    return payload


@router.get("/datasets")
async def list_datasets(
    business_unit_id: str | None = Query(
        None, description="Filter to one company's datasets (cross-company callers only)"
    ),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """List ingested actuals datasets for the caller's workspace, newest first.

    A regular (single-company) caller only ever sees their own company's
    datasets. A cross-company caller sees everything unless they narrow it
    with `business_unit_id` -- same boundary rules as line_item_scope_filter.
    """
    if business_unit_id and not can_access_business_unit(current_user, business_unit_id):
        raise HTTPException(404, "Business unit not found")

    q = db.query(ActualsDataset)
    if business_unit_id:
        q = q.filter(ActualsDataset.business_unit_id == business_unit_id)
    elif not can_view_all_bus(current_user):
        if not current_user.business_unit_id:
            return []
        q = q.filter(ActualsDataset.business_unit_id == current_user.business_unit_id)

    datasets = q.order_by(ActualsDataset.ingested_at.desc()).all()
    return [
        {
            "id": d.id,
            "source_type": d.source_type,
            "source_name": d.source_name,
            "row_count": d.row_count,
            "period_start": d.period_start,
            "period_end": d.period_end,
            "completeness_pct": d.completeness_pct,
            "is_pinned": d.is_pinned,
            "ingested_at": d.ingested_at.isoformat() if d.ingested_at else None,
            "business_unit_id": d.business_unit_id,
        }
        for d in datasets
    ]


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
