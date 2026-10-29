"""Driver-based what-if scenarios (Phase 7)."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.models.driver import DriverLink
from app.models.forecast import ForecastLineResult, ForecastVersion, ModelMetadata
from app.models.line_item import LineItem
from app.models.user import User
from app.services.audit import record_audit
from app.services.driver_exog import _materialize_with_precedence
from app.services.driver_series import materialize_driver_series, upsert_driver_values
from app.services.period_calendar import add_periods, get_calendar_config
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


def _base_driver_path(
    db: Session, *, driver_id: int, periods: list[str], version_id: str
) -> dict[str, float]:
    """Driver values the baseline version actually consumed, per period.

    Same composition the baseline's exog matrix used (actuals overlaid with the
    version's plan/scenario/forecast driver values), forward-filled the same
    way, so a shock's delta is measured against the path the model saw. Using
    actuals alone left every forecast period without a base value, which made
    shocks silently skip the future — i.e. what-ifs did nothing.
    """
    wanted = set(periods)
    # Include actual history so the last actual can carry into a future with
    # no stored driver forecast/plan.
    history = materialize_driver_series(db, driver_id=driver_id, value_type="actual")
    series = _materialize_with_precedence(
        db,
        driver_id=driver_id,
        periods=sorted(wanted | {str(p) for p in history.index}),
        version_id=version_id,
    )
    series = series.ffill()
    return {
        str(p): float(v) for p, v in series.items() if str(p) in wanted and v == v  # drop NaN
    }


def _fitted_exog_betas(db: Session, *, version_id: str, line_item_ids: list[int]) -> dict[int, dict[str, float]]:
    """line_item_id -> {exog column -> beta} from the baseline's admitted exog fit.

    Only lines whose exog was admitted carry betas that actually shaped their
    published P50, so only those are returned.
    """
    if not line_item_ids:
        return {}
    rows = (
        db.query(ForecastLineResult.line_item_id, ModelMetadata.parameters)
        .join(ModelMetadata, ModelMetadata.line_result_id == ForecastLineResult.id)
        .filter(
            ForecastLineResult.version_id == version_id,
            ForecastLineResult.line_item_id.in_(line_item_ids),
        )
        .all()
    )
    out: dict[int, dict[str, float]] = {}
    for line_item_id, params in rows:
        if line_item_id in out or not isinstance(params, dict):
            continue
        spec = params.get("exog_spec") or {}
        betas = params.get("exog_betas") or {}
        if not spec.get("exog_admitted") or not betas:
            continue
        out[line_item_id] = {
            str(k): float(v) for k, v in betas.items() if v is not None and math.isfinite(float(v))
        }
    return out


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
    cal_cfg = get_calendar_config(db, business_unit_id=source.business_unit_id)

    links = (
        db.query(DriverLink)
        .filter(
            DriverLink.driver_id.in_(shock_driver_ids),
            DriverLink.status == "active",
        )
        .all()
    )
    lags_by_driver: dict[int, set[int]] = {}
    for link in links:
        lags_by_driver.setdefault(link.driver_id, set()).add(int(link.lag or 0))

    # Build shocked scenario series and persist as driver_values(value_type=scenario).
    # The base is the driver path the baseline consumed (plan/forecast for the
    # future, actuals for history), covering the lagged periods links read.
    shocked_by_driver: dict[int, dict[str, float]] = {}
    base_by_driver: dict[int, dict[str, float]] = {}
    for s in shocks:
        lagged = {
            add_periods(p, -lag, cal_cfg)
            for lag in lags_by_driver.get(s.driver_id, {0})
            for p in periods
        }
        base_full = _base_driver_path(
            db,
            driver_id=s.driver_id,
            periods=sorted(set(periods) | lagged),
            version_id=source.id,
        )
        base_by_driver[s.driver_id] = base_full
        # A shock is a statement about the forecast window; history is fixed.
        base_map = {p: v for p, v in base_full.items() if p in periods}
        if not base_map and not (s.mode == "replace" and s.series):
            continue
        shocked = _apply_shock(base_map, s)
        if not shocked:
            continue
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
    fitted_betas = _fitted_exog_betas(db, version_id=source.id, line_item_ids=li_ids)
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
    beta_source: dict[int, str] = {}

    # Apply link-based effect (fast approximation; no model refit). The line at
    # period t responds to the driver at t - lag, matching how the baseline
    # built its exog matrix (driver(t-lag) predicts line(t)).
    for link in links:
        link_shocks = shocked_by_driver.get(link.driver_id)
        if not link_shocks:
            continue
        base_map = base_by_driver.get(link.driver_id, {})
        lag = int(link.lag or 0)
        fitted_beta = fitted_betas.get(link.line_item_id, {}).get(f"d{link.driver_id}_lag{lag}")
        for period in periods:
            row = by_key.get((link.line_item_id, period))
            if row is None:
                continue
            driver_period = add_periods(period, -lag, cal_cfg) if lag else period
            new_driver = link_shocks.get(driver_period)
            old_driver = base_map.get(driver_period)
            if new_driver is None or old_driver is None:
                continue
            d_driver = float(new_driver) - float(old_driver)
            if abs(d_driver) < 1e-12:
                continue
            if row.id not in base_intervals:
                base_intervals[row.id] = (row.p10, row.p50, row.p90)

            if fitted_beta is not None:
                # The baseline's published P50 was produced by this beta on
                # this driver column, so pushing the shock through it is the
                # exact counterfactual of the fitted model (linear in exog).
                delta = fitted_beta * d_driver
                sensitivity = d_driver
                se = None  # fit-time beta SE is not persisted; don't invent one
                beta_source[link.line_item_id] = "fitted_exog_beta"
            else:
                coef = (
                    float(link.elasticity)
                    if link.elasticity is not None
                    else float(link.coefficient if link.coefficient is not None else 1.0)
                )
                # Pre-shock point, so several links on one row don't compound.
                pre = base_intervals[row.id][1]
                base_line = float(pre if pre is not None else row.p50)
                if link.relation == "elasticity" or link.elasticity is not None:
                    if abs(old_driver) < 1e-12:
                        continue
                    pct = d_driver / abs(float(old_driver))
                    delta = base_line * coef * pct
                    # d(delta)/d(elasticity). Discovery stores an SE for the raw
                    # coefficient, not the elasticity; since elasticity = beta*x/y
                    # the two scale together, so carry the SE across by that ratio.
                    sensitivity = base_line * pct
                    se = _elasticity_se(link)
                else:
                    delta = coef * d_driver
                    sensitivity = d_driver
                    se = link.coefficient_se
                beta_source.setdefault(link.line_item_id, "discovery_link")

            # A shock is an assumption, so it carries no error of its own — but
            # the elasticity we push it through was *estimated*, and that
            # uncertainty is usually the largest term in a what-if answer.
            # Omitting it is what made scenario bands narrower than baseline ones.
            if se is not None and math.isfinite(float(se)):
                shock_variance[row.id] = (
                    shock_variance.get(row.id, 0.0) + (sensitivity * float(se)) ** 2
                )

            # Only the published point moves. model_p50 is the immutable model
            # output that FVA measures overrides against; shifting it made the
            # scenario look like model skill (or error) that never happened.
            row.p50 = float(row.p50 + delta)
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
            {
                "line_item_id": lid,
                "line_item": li_map[lid].name if lid in li_map else str(lid),
                "effect_source": beta_source.get(lid),
            }
            for lid in sorted(affected_items)
        ],
    }
