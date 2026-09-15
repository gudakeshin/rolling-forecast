"""Shared "which actuals dataset is current" resolution.

Used by every skill that needs "the dataset" when the caller didn't pin one
explicitly (generate_baseline, plan_forecast, run_ensemble's fallback).
Consolidates what used to be three independent copies of
``order_by(ingested_at.desc()).first()`` into one place, and gives manually
uploaded data precedence over unattended scheduled/API pulls: a pinned
dataset (see ``ActualsDataset.is_pinned``, set only by manual ingestion)
always wins over anything ingested after it, until a human unpins it.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.actuals import ActualsDataset


def resolve_current_dataset(
    db: Session,
    explicit_dataset_id: str | None = None,
    business_unit_id: str | None = None,
) -> ActualsDataset | None:
    """The dataset a forecast should use when the caller didn't ask for one
    in particular.

    An explicit id always wins (the caller knows exactly what it wants).
    Otherwise: the most recently ingested *pinned* (manually uploaded)
    dataset for this company, if any; failing that, the most recently
    ingested dataset overall for this company — preserving today's plain
    "latest wins" behavior when nothing has ever been pinned.

    `business_unit_id` scopes both queries to one company. Omitting it
    resolves across ALL companies, which is almost never what you want once
    more than one company has data — a global resolution would let whichever
    company happens to have the newer pin or the newer dataset silently
    "win" and be used for every other company's forecasts too. Every call
    site should pass the caller's/version's business_unit_id.
    """
    if explicit_dataset_id:
        return db.get(ActualsDataset, explicit_dataset_id)

    q = db.query(ActualsDataset)
    if business_unit_id:
        q = q.filter(ActualsDataset.business_unit_id == business_unit_id)

    pinned = q.filter(ActualsDataset.is_pinned.is_(True)).order_by(
        ActualsDataset.ingested_at.desc()
    ).first()
    if pinned:
        return pinned

    return q.order_by(ActualsDataset.ingested_at.desc()).first()


def dataset_is_current(
    db: Session, dataset_id: str, business_unit_id: str | None = None
) -> bool:
    """Whether ``dataset_id`` is the one `resolve_current_dataset` would pick."""
    current = resolve_current_dataset(db, business_unit_id=business_unit_id)
    return current is not None and current.id == dataset_id
