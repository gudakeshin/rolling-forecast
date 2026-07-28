"""Causal driver CRUD API — separate from BU assumption driver forms."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.driver import Driver, DriverDiscoveryRun, DriverLink
from app.models.line_item import LineItem
from app.models.user import User
from app.services.audit import record_audit
from app.services.driver_discovery import (
    discover_drivers_for_line,
    discovery_run_dict,
)
from app.services.driver_series import (
    assert_link,
    create_driver,
    materialize_driver_series,
    promote_link,
    upsert_driver_values,
    validate_aggregation,
    validate_driver_type,
)
from app.services.permissions import (
    driver_scope_filter,
    require_permission,
    scoped_drivers,
    user_can_view_driver,
    user_can_view_line_item,
    user_has_permission,
)

router = APIRouter(prefix="/drivers", tags=["drivers"])


class DriverCreate(BaseModel):
    key: str = Field(..., min_length=1, max_length=100)
    name: str = Field(..., min_length=1, max_length=255)
    driver_type: str = "other"
    unit: str | None = None
    currency: str | None = None
    aggregation: str = "sum"
    business_unit: str | None = None
    geography: str | None = None
    product_line: str | None = None
    description: str | None = None
    source: str | None = "manual"


class DriverUpdate(BaseModel):
    name: str | None = None
    driver_type: str | None = None
    unit: str | None = None
    currency: str | None = None
    aggregation: str | None = None
    business_unit: str | None = None
    geography: str | None = None
    product_line: str | None = None
    description: str | None = None


class DriverValueRow(BaseModel):
    period: str
    value: float
    p10: float | None = None
    p90: float | None = None
    currency: str | None = None


class DriverValuesIngest(BaseModel):
    rows: list[DriverValueRow]
    value_type: str = "actual"
    version_id: str | None = None


class DriverLinkCreate(BaseModel):
    driver_id: int
    line_item_id: int
    relation: str = "level"
    transform: str = "level"
    lag: int = 0
    coefficient: float | None = None
    composition_group: str | None = None
    status: str = "candidate"
    notes: str | None = None


class DiscoveryRunRequest(BaseModel):
    """Phase 9 discovery request — omitted knobs fall back to DiscoveryConfig."""

    line_item_id: int
    min_overlap: int | None = Field(None, ge=18)
    max_lag: int | None = Field(None, ge=0, le=12)
    alpha: float | None = Field(None, gt=0.0, lt=1.0)
    min_abs_elasticity: float | None = Field(None, ge=0.0)
    max_survivors: int | None = Field(None, ge=1, le=10)
    max_candidates: int | None = Field(None, ge=1, le=500)
    enable_placebo: bool | None = None
    placebo_draws: int | None = Field(None, ge=10, le=2000)
    driver_ids: list[int] | None = None
    random_seed: int | None = None


def _driver_dict(d: Driver, freshness: dict | None = None) -> dict:
    out = {
        "id": d.id,
        "key": d.key,
        "name": d.name,
        "driver_type": d.driver_type,
        "unit": d.unit,
        "currency": d.currency,
        "aggregation": d.aggregation,
        "business_unit": d.business_unit,
        "geography": d.geography,
        "product_line": d.product_line,
        "description": d.description,
        "source": d.source,
        "created_at": d.created_at.isoformat() if d.created_at else None,
        "updated_at": d.updated_at.isoformat() if d.updated_at else None,
    }
    if freshness is not None:
        out["freshness"] = freshness
    return out



def _link_dict(link: DriverLink) -> dict:
    return {
        "id": link.id,
        "driver_id": link.driver_id,
        "line_item_id": link.line_item_id,
        "link_type": link.link_type,
        "relation": link.relation,
        "lag": link.lag,
        "coefficient": link.coefficient,
        "coefficient_se": link.coefficient_se,
        "elasticity": link.elasticity,
        "t_stat": link.t_stat,
        "p_value": link.p_value,
        "p_value_adj": link.p_value_adj,
        "r2": link.r2,
        "n_obs": link.n_obs,
        "transform": link.transform,
        "fit_method": link.fit_method,
        "hac_lags": link.hac_lags,
        "diagnostics": link.diagnostics,
        "discovery_run_id": link.discovery_run_id,
        "status": link.status,
        "composition_group": link.composition_group,
        "notes": link.notes,
        "created_by": link.created_by,
        "created_at": link.created_at.isoformat() if link.created_at else None,
        "updated_at": link.updated_at.isoformat() if link.updated_at else None,
    }


def _discovery_response(db: Session, run: DriverDiscoveryRun) -> dict:
    links = (
        db.query(DriverLink)
        .filter(DriverLink.discovery_run_id == run.id)
        .order_by(DriverLink.id.asc())
        .all()
    )
    return {**discovery_run_dict(run), "links": [_link_dict(l) for l in links]}


def _get_scoped_driver(db: Session, user: User, driver_id: int) -> Driver:
    d = db.query(Driver).filter(Driver.id == driver_id).first()
    if not d or not user_can_view_driver(user, d):
        raise HTTPException(404, "Driver not found")
    return d


@router.get("")
async def list_drivers(
    driver_type: str | None = None,
    include_freshness: bool = Query(False),
    current_user: User = Depends(require_permission("manage_drivers")),
    db: Session = Depends(get_db),
):
    from app.services.driver_ingest import drivers_freshness_batch

    filters = []
    if driver_type:
        filters.append(Driver.driver_type == driver_type)
    q = scoped_drivers(db, current_user, *filters).order_by(Driver.key)
    drivers = q.all()
    fresh_map: dict[int, dict] = {}
    if include_freshness and drivers:
        fresh_map = drivers_freshness_batch(db, [d.id for d in drivers])
    return [
        _driver_dict(d, freshness=fresh_map.get(d.id) if include_freshness else None)
        for d in drivers
    ]


@router.get("/{driver_id}/freshness")
async def get_driver_freshness(
    driver_id: int,
    current_user: User = Depends(require_permission("manage_drivers")),
    db: Session = Depends(get_db),
):
    from app.services.driver_ingest import driver_freshness

    _get_scoped_driver(db, current_user, driver_id)
    return driver_freshness(db, driver_id)


@router.post("", status_code=201)
async def create_driver_endpoint(
    body: DriverCreate,
    current_user: User = Depends(require_permission("manage_drivers")),
    db: Session = Depends(get_db),
):
    existing = db.query(Driver).filter(Driver.key == body.key.strip()).first()
    if existing:
        raise HTTPException(409, f"Driver key '{body.key}' already exists")
    bu = body.business_unit
    if not user_has_permission(current_user, "admin") and current_user.business_unit:
        if bu and bu != current_user.business_unit:
            raise HTTPException(403, "Cannot create driver for another business unit")
        if bu is None:
            bu = current_user.business_unit
    try:
        driver = create_driver(
            db,
            key=body.key,
            name=body.name,
            driver_type=body.driver_type,
            unit=body.unit,
            currency=body.currency,
            aggregation=body.aggregation,
            business_unit=bu,
            geography=body.geography,
            product_line=body.product_line,
            description=body.description,
            source=body.source,
            actor=current_user,
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    db.commit()
    db.refresh(driver)
    return _driver_dict(driver)


# Static paths before /{driver_id} so "links" / "by-line" are not parsed as ints
@router.post("/links", status_code=201)
async def create_link_endpoint(
    body: DriverLinkCreate,
    current_user: User = Depends(require_permission("manage_drivers")),
    db: Session = Depends(get_db),
):
    _get_scoped_driver(db, current_user, body.driver_id)
    li = db.query(LineItem).filter(LineItem.id == body.line_item_id).first()
    if not li or not user_can_view_line_item(current_user, li):
        raise HTTPException(404, "Line item not found")
    try:
        link = assert_link(
            db,
            driver_id=body.driver_id,
            line_item_id=body.line_item_id,
            relation=body.relation,
            transform=body.transform,
            lag=body.lag,
            coefficient=body.coefficient,
            composition_group=body.composition_group,
            status=body.status,
            notes=body.notes,
            actor=current_user,
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    db.commit()
    db.refresh(link)
    return _link_dict(link)


@router.post("/links/{link_id}/promote")
async def promote_link_endpoint(
    link_id: int,
    current_user: User = Depends(require_permission("override")),
    db: Session = Depends(get_db),
):
    """Promote candidate → active. Gated at can_override minimum."""
    link = db.query(DriverLink).filter(DriverLink.id == link_id).first()
    if not link:
        raise HTTPException(404, "Link not found")
    driver = db.query(Driver).filter(Driver.id == link.driver_id).first()
    if not driver or not user_can_view_driver(current_user, driver):
        raise HTTPException(404, "Link not found")
    try:
        promote_link(db, link=link, actor=current_user)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    db.commit()
    db.refresh(link)
    return _link_dict(link)


@router.get("/by-line/{line_item_id}/links")
async def links_for_line_item(
    line_item_id: int,
    status: str | None = Query(None),
    current_user: User = Depends(require_permission("manage_drivers")),
    db: Session = Depends(get_db),
):
    li = db.query(LineItem).filter(LineItem.id == line_item_id).first()
    if not li or not user_can_view_line_item(current_user, li):
        raise HTTPException(404, "Line item not found")
    q = (
        db.query(DriverLink)
        .join(Driver, Driver.id == DriverLink.driver_id)
        .filter(DriverLink.line_item_id == line_item_id)
    )
    q = driver_scope_filter(q, current_user, Driver)
    if status:
        q = q.filter(DriverLink.status == status)
    return [_link_dict(l) for l in q.all()]


@router.post("/discovery/run", status_code=201)
async def run_driver_discovery(
    body: DiscoveryRunRequest,
    current_user: User = Depends(require_permission("manage_drivers")),
    db: Session = Depends(get_db),
):
    """Scan drivers for a line item. Only ever writes candidate links."""
    li = db.query(LineItem).filter(LineItem.id == body.line_item_id).first()
    if not li or not user_can_view_line_item(current_user, li):
        raise HTTPException(404, "Line item not found")
    config = body.model_dump(exclude={"line_item_id"}, exclude_none=True)
    try:
        run = discover_drivers_for_line(
            db,
            body.line_item_id,
            actor=current_user,
            config=config,
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    db.commit()
    db.refresh(run)
    return _discovery_response(db, run)


@router.get("/discovery/{run_id}")
async def get_driver_discovery(
    run_id: str,
    current_user: User = Depends(require_permission("manage_drivers")),
    db: Session = Depends(get_db),
):
    run = db.query(DriverDiscoveryRun).filter(DriverDiscoveryRun.id == run_id).first()
    if not run:
        raise HTTPException(404, "Discovery run not found")
    if run.line_item_id is not None:
        li = db.query(LineItem).filter(LineItem.id == run.line_item_id).first()
        if li and not user_can_view_line_item(current_user, li):
            raise HTTPException(404, "Discovery run not found")
    return _discovery_response(db, run)


@router.get("/{driver_id}")
async def get_driver(
    driver_id: int,
    current_user: User = Depends(require_permission("manage_drivers")),
    db: Session = Depends(get_db),
):
    return _driver_dict(_get_scoped_driver(db, current_user, driver_id))


@router.patch("/{driver_id}")
async def update_driver(
    driver_id: int,
    body: DriverUpdate,
    current_user: User = Depends(require_permission("manage_drivers")),
    db: Session = Depends(get_db),
):
    driver = _get_scoped_driver(db, current_user, driver_id)
    data = body.model_dump(exclude_unset=True)
    if "driver_type" in data and data["driver_type"] is not None:
        try:
            validate_driver_type(data["driver_type"])
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
    if "aggregation" in data and data["aggregation"] is not None:
        try:
            validate_aggregation(data["aggregation"])
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
    for k, v in data.items():
        setattr(driver, k, v)
    record_audit(
        db,
        action="driver.update",
        entity_type="driver",
        entity_id=str(driver.id),
        actor_id=current_user.id,
        actor_username=current_user.username,
        details=data,
    )
    db.commit()
    db.refresh(driver)
    return _driver_dict(driver)


@router.get("/{driver_id}/values")
async def get_driver_values(
    driver_id: int,
    value_type: str = Query("actual"),
    version_id: str | None = None,
    period_from: str | None = None,
    period_to: str | None = None,
    current_user: User = Depends(require_permission("manage_drivers")),
    db: Session = Depends(get_db),
):
    _get_scoped_driver(db, current_user, driver_id)
    try:
        series = materialize_driver_series(
            db,
            driver_id=driver_id,
            value_type=value_type,
            version_id=version_id,
            period_from=period_from,
            period_to=period_to,
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return {
        "driver_id": driver_id,
        "value_type": value_type,
        "version_id": version_id or "",
        "periods": list(series.index.astype(str)),
        "values": [float(v) for v in series.values],
    }


@router.post("/{driver_id}/values")
async def ingest_driver_values(
    driver_id: int,
    body: DriverValuesIngest,
    current_user: User = Depends(require_permission("manage_drivers")),
    db: Session = Depends(get_db),
):
    _get_scoped_driver(db, current_user, driver_id)
    try:
        n = upsert_driver_values(
            db,
            driver_id=driver_id,
            rows=[r.model_dump() for r in body.rows],
            value_type=body.value_type,
            version_id=body.version_id,
            actor=current_user,
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    db.commit()
    return {"ingested": n, "driver_id": driver_id}


@router.get("/{driver_id}/links")
async def list_driver_links(
    driver_id: int,
    current_user: User = Depends(require_permission("manage_drivers")),
    db: Session = Depends(get_db),
):
    _get_scoped_driver(db, current_user, driver_id)
    links = db.query(DriverLink).filter(DriverLink.driver_id == driver_id).all()
    return [_link_dict(l) for l in links]
