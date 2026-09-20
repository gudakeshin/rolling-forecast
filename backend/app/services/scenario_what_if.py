"""Driver-based what-if scenarios (Phase 7)."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.models.driver import DriverLink
from app.models.forecast import ForecastLineResult, ForecastVersion
from app.models.line_item import LineItem
from app.models.user import User
from app.services.audit import record_audit
from app.services.driver_series import materialize_driver_series, upsert_driver_values
from app.services.reconciliation import reconcile_version
from app.services.versioning import clone_version_for_edit
from app.services.permissions import can_access_business_unit

# Normal quantile for an 80% two-sided band — the same constant the engines
# and MinT use, kept here so scenario bands stay comparable to baseline ones.
_Z80 = 1.2816


@dataclass
class DriverShock:
    driver_id: int
    mode: str  # pct | absolute | replace
    value: float | None = None
    series: dict[str, float] | None = None



def _elasticity_se(link: DriverLink) -> float | None:
    """SE of the elasticity, carried over from the coefficient's HAC SE.

    ``driver_discovery`` fits in coefficient space and stores ``coefficient_se``
    there. Elasticity is ``beta * xbar / ybar``, a fixed positive rescaling of
    beta at the sample means, so the SE rescales by the same factor. Returns
    ``None`` when the ratio is not recoverable — no opinion beats a fabricated
    error bar.
    """
    if link.coefficient_se is None or link.elasticity is None:
        return None
    if link.coefficient in (None, 0.0):
        return None
    ratio = abs(float(link.elasticity) / float(link.coefficient))
    if not math.isfinite(ratio):
        return None
    return abs(float(link.coefficient_se)) * ratio


def _apply_shock(base: dict[str, float], shock: DriverShock) -> dict[str, float]:
    out = dict(base)
    if shock.mode == "replace":
        repl = shock.series or {}
        for p, v in repl.items():
            out[str(p)] = float(v)
        return out
    if shock.value is None:
        return out
    if shock.mode == "absolute":
        return {p: float(v + shock.value) for p, v in out.items()}
    # pct
    factor = 1.0 + float(shock.value) / 100.0
    return {p: float(v * factor) for p, v in out.items()}


def create_what_if_scenario(
    db: Session,
    *,
    base_version_id: str,
    scenario_label: str,
    shocks: list[DriverShock],
    actor: User | None,
) -> dict[str, Any]:
    """Create scenario version by perturbing driver-linked line items only."""
    source = db.query(ForecastVersion).filter(ForecastVersion.id == base_version_id).first()
    if not source or not can_access_business_unit(actor, source.business_unit_id):
        raise ValueError(f"Base version '{base_version_id}' not found")
    if not shocks:
        raise ValueError("At least one driver shock is required")

    child = clone_version_for_edit(
        db,
        source,
        created_by=actor.id if actor else None,
        label_suffix=f"scenario-{scenario_label}",
    )
    child.version_type = "scenario"
    child.scenario = scenario_label
    child.notes = f"What-if scenario from {source.id}: {scenario_label}"
    db.flush()

    periods = sorted(
        {
            p
            for (p,) in db.query(ForecastLineResult.period)
            .filter(ForecastLineResult.version_id == child.id)
            .distinct()
            .all()
        }
    )
    shock_driver_ids = [s.driver_id for s in shocks]

    # Build shocked scenario series and persist as driver_values(value_type=scenario)
    shocked_by_driver: dict[int, dict[str, float]] = {}
    for s in shocks:
        base_series = materialize_driver_series(db, driver_id=s.driver_id, value_type="actual")
        if base_series.empty:
            continue
        base_map = {str(p): float(v) for p, v in base_series.items() if str(p) in periods}
        if not base_map:
            continue
        shocked = _apply_shock(base_map, s)
        driver_rows = [{"period": p, "value": v} for p, v in sorted(shocked.items())]
        upsert_driver_values(
            db,
            driver_id=s.driver_id,
            rows=driver_rows,
            value_type="scenario",
            version_id=child.id,
            actor=None,
        )
        shocked_by_driver[s.driver_id] = shocked

    links = (
        db.query(DriverLink)
        .filter(
            DriverLink.driver_id.in_(shock_driver_ids),
            DriverLink.status == "active",
        )
        .all()
    )
    if not links:
        db.commit()
        return {
            "scenario_version_id": child.id,
            "scenario_label": scenario_label,
            "affected_line_items": 0,
            "affected_line_periods": 0,
            "shocked_drivers": len(shocked_by_driver),
            "note": "No active links for shocked drivers; version cloned unchanged.",
        }

    li_ids = sorted({l.line_item_id for l in links})
    li_map = {
        li.id: li
        for li in db.query(LineItem).filter(LineItem.id.in_(li_ids)).all()
    }
    rows = (
        db.query(ForecastLineResult)
        .filter(
            ForecastLineResult.version_id == child.id,
            ForecastLineResult.line_item_id.in_(li_ids),
        )
        .all()
    )
    by_key = {(r.line_item_id, r.period): r for r in rows}
    affected = 0
    affected_items: set[int] = set()
    affected_row_ids: set[str] = set()
    # row_id -> (p10, p50, p90) captured before any perturbation, so the band's
    # own half-widths survive and can be re-centred on the shocked point.
    base_intervals: dict[str, tuple[float | None, float | None, float | None]] = {}
    # row_id -> variance contributed by uncertainty in the link coefficients.
    shock_variance: dict[str, float] = {}

    # Apply link-based effect (fast approximation; no model refit)
    for link in links:
        link_shocks = shocked_by_driver.get(link.driver_id)
        if not link_shocks:
            continue
        base_series = materialize_driver_series(db, driver_id=link.driver_id, value_type="actual")
        base_map = {str(p): float(v) for p, v in base_series.items()}
        for period, new_driver in link_shocks.items():
            row = by_key.get((link.line_item_id, period))
            if row is None:
                continue
            if row.id not in base_intervals:
                base_intervals[row.id] = (row.p10, row.p50, row.p90)
            old_driver = base_map.get(period)
            if old_driver is None:
                continue
            coef = (
                float(link.elasticity)
                if link.elasticity is not None
                else float(link.coefficient if link.coefficient is not None else 1.0)
            )
            base_line = float(row.model_p50 if row.model_p50 is not None else row.p50)
            if link.relation == "elasticity" or link.elasticity is not None:
                if abs(old_driver) < 1e-12:
                    continue
                pct = (float(new_driver) - float(old_driver)) / abs(float(old_driver))
                delta = base_line * coef * pct
                # d(delta)/d(elasticity). Discovery stores an SE for the raw
                # coefficient, not the elasticity; since elasticity = beta*x/y
                # the two scale together, so carry the SE across by that ratio.
                sensitivity = base_line * pct
                se = _elasticity_se(link)
            else:
                delta = coef * (float(new_driver) - float(old_driver))
                sensitivity = float(new_driver) - float(old_driver)
                se = link.coefficient_se

            # A shock is an assumption, so it carries no error of its own — but
            # the elasticity we push it through was *estimated*, and that
            # uncertainty is usually the largest term in a what-if answer.
            # Omitting it is what made scenario bands narrower than baseline ones.
            if se is not None and math.isfinite(float(se)):
                shock_variance[row.id] = (
                    shock_variance.get(row.id, 0.0) + (sensitivity * float(se)) ** 2
                )

            row.p50 = float(row.p50 + delta)
            row.model_p50 = float((row.model_p50 if row.model_p50 is not None else base_line) + delta)
            affected += 1
            affected_items.add(link.line_item_id)
            affected_row_ids.add(row.id)

    # Reconcile calculated parents after perturbing leaves.
    reconcile_version(db, child.id)
    # Reconciliation may stamp MinT bounds_method; keep scenario marker on touched rows.
    if affected_row_ids:
        for row in (
            db.query(ForecastLineResult)
            .filter(ForecastLineResult.id.in_(list(affected_row_ids)))
            .all()
        ):
            row.bounds_method = "scenario"
            p10, p50, p90 = base_intervals.get(row.id, (row.p10, row.p50, row.p90))
            if p10 is None or p50 is None or p90 is None or row.p50 is None:
                continue
            # Keep the model's own (possibly asymmetric) half-widths, re-centre
            # them on the shocked point, then add the coefficient uncertainty.
            # Previously the base band was written back verbatim while p50 had
            # moved, which could leave the point outside its own interval.
            extra = _Z80 * math.sqrt(shock_variance.get(row.id, 0.0))
            half_lo = max(float(p50) - float(p10), 0.0)
            half_hi = max(float(p90) - float(p50), 0.0)
            row.p10 = float(row.p50) - half_lo - extra
            row.p90 = float(row.p50) + half_hi + extra

    if actor is not None:
        record_audit(
            db,
            action="scenario.what_if.create",
            entity_type="forecast_version",
            entity_id=child.id,
            actor_id=actor.id,
            actor_username=actor.username,
            details={
                "base_version_id": base_version_id,
                "scenario_label": scenario_label,
                "shock_driver_ids": shock_driver_ids,
                "affected_line_items": len(affected_items),
                "affected_line_periods": affected,
            },
        )
    db.commit()
    return {
        "scenario_version_id": child.id,
        "scenario_label": scenario_label,
        "base_version_id": base_version_id,
        "affected_line_items": len(affected_items),
        "affected_line_periods": affected,
        "shocked_drivers": len(shocked_by_driver),
        "line_items": [
            {"line_item_id": lid, "line_item": li_map[lid].name if lid in li_map else str(lid)}
            for lid in sorted(affected_items)
        ],
    }
