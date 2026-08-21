"""Ingest causal driver series from CSV/XLSX or macro providers."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.driver import Driver, DriverValue
from app.models.user import User
from app.services.audit import record_audit
from app.services.driver_series import create_driver, upsert_driver_values
from app.services.ingestion.driver_csv_adapter import CSVDriverProvider

logger = logging.getLogger(__name__)

# Days since last ingest before a driver is flagged stale
DEFAULT_STALE_DAYS = 90


def driver_freshness(
    db: Session,
    driver_id: int,
    *,
    stale_days: int = DEFAULT_STALE_DAYS,
) -> dict[str, Any]:
    """Surface last ingest time and staleness for a driver series."""
    return drivers_freshness_batch(db, [driver_id], stale_days=stale_days)[driver_id]


def drivers_freshness_batch(
    db: Session,
    driver_ids: list[int],
    *,
    stale_days: int = DEFAULT_STALE_DAYS,
) -> dict[int, dict[str, Any]]:
    """Batch freshness for many drivers (2 queries total, not 2×N)."""
    ids = sorted({int(i) for i in driver_ids})
    empty = {
        "last_ingested_at": None,
        "last_period": None,
        "age_days": None,
        "stale": True,
        "stale_days_threshold": stale_days,
    }
    out: dict[int, dict[str, Any]] = {i: {"driver_id": i, **empty} for i in ids}
    if not ids:
        return out

    # Latest ingest per driver
    latest_rows = (
        db.query(
            DriverValue.driver_id,
            func.max(DriverValue.ingested_at).label("last_ingested_at"),
        )
        .filter(DriverValue.driver_id.in_(ids))
        .group_by(DriverValue.driver_id)
        .all()
    )
    # Latest actual period per driver
    period_rows = (
        db.query(
            DriverValue.driver_id,
            func.max(DriverValue.period).label("last_period"),
        )
        .filter(DriverValue.driver_id.in_(ids), DriverValue.value_type == "actual")
        .group_by(DriverValue.driver_id)
        .all()
    )
    last_period_by = {int(r.driver_id): r.last_period for r in period_rows}
    now = datetime.now(timezone.utc)
    for r in latest_rows:
        did = int(r.driver_id)
        ingested_at = r.last_ingested_at
        age_days: float | None = None
        stale = True
        if ingested_at is not None:
            ts = ingested_at if ingested_at.tzinfo else ingested_at.replace(tzinfo=timezone.utc)
            age_days = (now - ts).total_seconds() / 86400.0
            stale = age_days > stale_days
        out[did] = {
            "driver_id": did,
            "last_ingested_at": ingested_at.isoformat() if ingested_at else None,
            "last_period": last_period_by.get(did),
            "age_days": round(age_days, 1) if age_days is not None else None,
            "stale": stale,
            "stale_days_threshold": stale_days,
        }
    return out


async def ingest_drivers_file(
    db: Session,
    *,
    file_path: str,
    actor: User | None = None,
    source_name: str | None = None,
) -> dict[str, Any]:
    """Parse a driver CSV/XLSX and upsert into drivers + driver_values."""
    provider = CSVDriverProvider()
    result = await provider.pull_drivers({"file_path": file_path})
    if not result.get("success"):
        return result

    df = result["dataframe"]
    created = 0
    updated_keys: list[str] = []
    total_rows = 0

    for key, group in df.groupby("driver_key"):
        key_s = str(key).strip()
        first = group.iloc[0]
        existing = db.query(Driver).filter(Driver.key == key_s).first()
        if existing is None:
            name = str(first.get("name") or key_s)
            dtype = str(first.get("driver_type") or "other")
            if dtype not in {
                "volume", "price", "rate", "headcount", "macro", "index", "other"
            }:
                dtype = "other"
            agg = first.get("aggregation")
            aggregation = str(agg) if agg and str(agg) != "nan" else "sum"
            if aggregation not in {"sum", "average", "end_of_period"}:
                aggregation = "sum"
            bu = first.get("business_unit")
            bu = None if bu is None or str(bu) == "nan" else str(bu)
            existing = create_driver(
                db,
                key=key_s,
                name=name,
                driver_type=dtype,
                unit=None if first.get("unit") is None or str(first.get("unit")) == "nan" else str(first.get("unit")),
                currency=None if first.get("currency") is None or str(first.get("currency")) == "nan" else str(first.get("currency")),
                aggregation=aggregation,
                business_unit=bu,
                geography=None if first.get("geography") is None or str(first.get("geography")) == "nan" else str(first.get("geography")),
                product_line=None if first.get("product_line") is None or str(first.get("product_line")) == "nan" else str(first.get("product_line")),
                source="csv",
                actor=actor,
            )
            created += 1
        else:
            updated_keys.append(key_s)

        rows = []
        for _, row in group.iterrows():
            ccy = row.get("currency")
            rows.append({
                "period": str(row["period"]),
                "value": float(row["value"]),
                "currency": None if ccy is None or str(ccy) == "nan" else str(ccy),
            })
        n = upsert_driver_values(
            db,
            driver_id=existing.id,
            rows=rows,
            value_type="actual",
            actor=None,  # single audit below
        )
        total_rows += n

    if actor is not None:
        record_audit(
            db,
            action="driver.values.ingest",
            entity_type="driver_ingest",
            entity_id=result.get("file_hash"),
            actor_id=actor.id,
            actor_username=actor.username,
            details={
                "source": "csv",
                "source_name": source_name or file_path,
                "file_hash": result.get("file_hash"),
                "drivers_created": created,
                "drivers_updated": len(set(updated_keys)),
                "n_rows": total_rows,
                "period_start": result.get("period_start"),
                "period_end": result.get("period_end"),
                "completeness_pct": result.get("completeness_pct"),
            },
        )
    db.commit()

    return {
        "success": True,
        "file_hash": result.get("file_hash"),
        "row_count": total_rows,
        "drivers_created": created,
        "drivers_touched": created + len(set(updated_keys)),
        "period_start": result.get("period_start"),
        "period_end": result.get("period_end"),
        "periods_count": result.get("periods_count"),
        "missing_periods": result.get("missing_periods"),
        "completeness_pct": result.get("completeness_pct"),
        "warnings": result.get("warnings") or [],
        "unique_drivers": result.get("unique_drivers"),
        "source_name": source_name,
    }


def persist_macro_as_driver(
    db: Session,
    *,
    key: str,
    name: str,
    source: str,
    rows: list[dict[str, Any]],
    driver_type: str = "macro",
    unit: str | None = None,
    actor: User | None = None,
) -> dict[str, Any]:
    """Upsert a FRED/yfinance series into drivers + driver_values(actual)."""
    if source not in {"fred", "yfinance"}:
        raise ValueError(f"source must be fred|yfinance, got {source!r}")
    existing = db.query(Driver).filter(Driver.key == key).first()
    created = False
    if existing is None:
        existing = create_driver(
            db,
            key=key,
            name=name,
            driver_type=driver_type,
            unit=unit,
            aggregation="average",
            source=source,
            actor=actor,
        )
        created = True
    else:
        existing.source = source
        existing.name = name or existing.name

    n = upsert_driver_values(
        db,
        driver_id=existing.id,
        rows=rows,
        value_type="actual",
        actor=actor,
    )
    db.flush()
    return {
        "driver_id": existing.id,
        "key": existing.key,
        "created": created,
        "n_rows": n,
        "source": source,
        "freshness": driver_freshness(db, existing.id),
    }
