"""Causal driver series materialization and link lifecycle."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable

import pandas as pd
from sqlalchemy.orm import Session

from app.config import settings
from app.models.driver import (
    AGGREGATIONS,
    DRIVER_TYPES,
    LINK_RELATIONS,
    LINK_STATUSES,
    NO_VERSION_ID,
    TRANSFORMS,
    VALUE_TYPES,
    Driver,
    DriverLink,
    DriverValue,
)
from app.models.user import User
from app.services.audit import record_audit
from app.services.upsert import bulk_upsert


def validate_driver_type(driver_type: str) -> str:
    if driver_type not in DRIVER_TYPES:
        raise ValueError(f"Invalid driver_type '{driver_type}'. Allowed: {sorted(DRIVER_TYPES)}")
    return driver_type


def validate_aggregation(aggregation: str) -> str:
    if aggregation not in AGGREGATIONS:
        raise ValueError(f"Invalid aggregation '{aggregation}'. Allowed: {sorted(AGGREGATIONS)}")
    return aggregation


def validate_value_type(value_type: str) -> str:
    if value_type not in VALUE_TYPES:
        raise ValueError(f"Invalid value_type '{value_type}'. Allowed: {sorted(VALUE_TYPES)}")
    return value_type


def validate_relation(relation: str) -> str:
    if relation not in LINK_RELATIONS:
        raise ValueError(f"Invalid relation '{relation}'. Allowed: {sorted(LINK_RELATIONS)}")
    return relation


def validate_transform(transform: str) -> str:
    if transform not in TRANSFORMS:
        raise ValueError(f"Invalid transform '{transform}'. Allowed: {sorted(TRANSFORMS)}")
    return transform


def materialize_driver_series(
    db: Session,
    *,
    driver_id: int,
    value_type: str = "actual",
    version_id: str | None = None,
    period_from: str | None = None,
    period_to: str | None = None,
) -> pd.Series:
    """Return a period-indexed Series for a driver (shared by forecast + attribution).

    Prefer ``value_type='actual'`` with empty ``version_id`` for history.
    Forward/scenario paths pass a concrete ``version_id``.
    """
    validate_value_type(value_type)
    vid = NO_VERSION_ID if version_id is None else version_id
    q = db.query(DriverValue).filter(
        DriverValue.driver_id == driver_id,
        DriverValue.value_type == value_type,
        DriverValue.version_id == vid,
    )
    if period_from:
        q = q.filter(DriverValue.period >= period_from)
    if period_to:
        q = q.filter(DriverValue.period <= period_to)
    rows = q.order_by(DriverValue.period).all()
    if not rows:
        return pd.Series(dtype=float)
    return pd.Series(
        {r.period: float(r.value) for r in rows},
        dtype=float,
    ).sort_index()


def upsert_driver_values(
    db: Session,
    *,
    driver_id: int,
    rows: Iterable[dict[str, Any]],
    value_type: str = "actual",
    version_id: str | None = None,
    actor: User | None = None,
) -> int:
    """Bulk upsert driver values. ``version_id`` defaults to empty sentinel."""
    validate_value_type(value_type)
    vid = NO_VERSION_ID if version_id is None else version_id
    now = datetime.now(timezone.utc)
    payload: list[dict[str, Any]] = []
    for row in rows:
        payload.append(
            {
                "driver_id": driver_id,
                "period": str(row["period"]),
                "value": float(row["value"]),
                "p10": row.get("p10"),
                "p90": row.get("p90"),
                "value_type": value_type,
                "version_id": vid,
                "currency": row.get("currency"),
                "ingested_at": now,
            }
        )
    n = bulk_upsert(
        db,
        DriverValue,
        payload,
        conflict_cols=("driver_id", "period", "value_type", "version_id"),
        update_cols=("value", "p10", "p90", "currency", "ingested_at"),
    )
    if actor is not None:
        record_audit(
            db,
            action="driver.values.ingest",
            entity_type="driver",
            entity_id=str(driver_id),
            actor_id=actor.id,
            actor_username=actor.username,
            details={"n_rows": n, "value_type": value_type, "version_id": vid or None},
        )
    return n


def create_driver(
    db: Session,
    *,
    key: str,
    name: str,
    driver_type: str = "other",
    unit: str | None = None,
    currency: str | None = None,
    aggregation: str = "sum",
    business_unit: str | None = None,
    geography: str | None = None,
    product_line: str | None = None,
    description: str | None = None,
    source: str | None = "manual",
    actor: User | None = None,
) -> Driver:
    validate_driver_type(driver_type)
    validate_aggregation(aggregation)
    driver = Driver(
        key=key.strip(),
        name=name.strip(),
        driver_type=driver_type,
        unit=unit,
        currency=currency,
        aggregation=aggregation,
        business_unit=business_unit,
        geography=geography,
        product_line=product_line,
        description=description,
        source=source,
    )
    db.add(driver)
    db.flush()
    if actor is not None:
        record_audit(
            db,
            action="driver.create",
            entity_type="driver",
            entity_id=str(driver.id),
            actor_id=actor.id,
            actor_username=actor.username,
            details={
                "key": driver.key,
                "driver_type": driver.driver_type,
                "business_unit": driver.business_unit,
            },
        )
    return driver


def assert_link(
    db: Session,
    *,
    driver_id: int,
    line_item_id: int,
    relation: str = "level",
    transform: str = "level",
    lag: int = 0,
    coefficient: float | None = None,
    composition_group: str | None = None,
    status: str = "candidate",
    notes: str | None = None,
    actor: User | None = None,
) -> DriverLink:
    """Manually assert a driver↔line link (Phase 4 — no discovery)."""
    validate_relation(relation)
    validate_transform(transform)
    if status not in LINK_STATUSES:
        raise ValueError(f"Invalid status '{status}'. Allowed: {sorted(LINK_STATUSES)}")
    link = DriverLink(
        driver_id=driver_id,
        line_item_id=line_item_id,
        link_type="manual",
        relation=relation,
        transform=transform,
        lag=int(lag),
        coefficient=coefficient,
        composition_group=composition_group,
        status=status,
        notes=notes,
        created_by=actor.id if actor else None,
    )
    db.add(link)
    db.flush()
    if actor is not None:
        record_audit(
            db,
            action="driver.link.create",
            entity_type="driver_link",
            entity_id=str(link.id),
            actor_id=actor.id,
            actor_username=actor.username,
            details=_link_audit_payload(link),
        )
    return link


def promote_link(
    db: Session,
    *,
    link: DriverLink,
    actor: User,
) -> DriverLink:
    """Promote candidate → active. Requires can_override (caller enforces)."""
    if link.status == "active":
        return link
    if link.status not in {"candidate", "rejected"}:
        raise ValueError(f"Cannot promote link in status '{link.status}'")
    before = link.status
    link.status = "active"
    link.updated_at = datetime.now(timezone.utc)
    db.flush()
    record_audit(
        db,
        action="driver.link.promote",
        entity_type="driver_link",
        entity_id=str(link.id),
        actor_id=actor.id,
        actor_username=actor.username,
        details={**_link_audit_payload(link), "from_status": before},
    )
    return link


def _link_audit_payload(link: DriverLink) -> dict[str, Any]:
    return {
        "driver_id": link.driver_id,
        "line_item_id": link.line_item_id,
        "relation": link.relation,
        "transform": link.transform,
        "lag": link.lag,
        "coefficient": link.coefficient,
        "coefficient_se": link.coefficient_se,
        "elasticity": link.elasticity,
        "t_stat": link.t_stat,
        "p_value": link.p_value,
        "p_value_adj": link.p_value_adj,
        "r2": link.r2,
        "n_obs": link.n_obs,
        "status": link.status,
        "composition_group": link.composition_group,
        "fit_method": link.fit_method,
    }


def qp_coherence(
    q: pd.Series,
    p: pd.Series,
    line: pd.Series,
    *,
    tolerance: float | None = None,
) -> dict[str, Any]:
    """Check |Q·P − L| / |L| against settings.qp_coherence_tolerance.

    Returns whether the identity rung is admissible and max relative error.
    """
    tol = settings.qp_coherence_tolerance if tolerance is None else tolerance
    idx = q.index.intersection(p.index).intersection(line.index)
    if len(idx) == 0:
        return {
            "coherent": False,
            "max_rel_error": None,
            "tolerance": tol,
            "n_periods": 0,
            "reason": "no_overlapping_periods",
        }
    product = q.loc[idx] * p.loc[idx]
    denom = line.loc[idx].abs().clip(lower=1e-12)
    rel = (product - line.loc[idx]).abs() / denom
    max_err = float(rel.max())
    return {
        "coherent": max_err <= tol,
        "max_rel_error": max_err,
        "tolerance": tol,
        "n_periods": int(len(idx)),
        "reason": None if max_err <= tol else "exceeds_tolerance",
    }


def derive_price_from_volume(
    line: pd.Series,
    quantity: pd.Series,
) -> tuple[pd.Series, dict[str, Any]]:
    """Derived-price convention: P := L / Q. Returns (price, meta).

    Meta flags that 'price' is residual of volume — not measured ASP.
    """
    idx = line.index.intersection(quantity.index)
    q = quantity.loc[idx].astype(float)
    l = line.loc[idx].astype(float)
    # Avoid div-by-zero — leave NaN where Q≈0
    q_safe = q.replace(0.0, float("nan"))
    price = l / q_safe
    price = price.replace([float("inf"), float("-inf")], float("nan"))
    meta = {
        "price_source": "derived_l_over_q",
        "price_meaning": (
            "Everything that isn't volume (rate, mix, discount/rebate blended) — "
            "not a measured ASP"
        ),
        "n_periods": int(len(idx)),
        "n_missing_q": int((q.abs() < 1e-12).sum()),
    }
    return price, meta
