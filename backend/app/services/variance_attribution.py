"""Variance attribution ladder.

Implements:
- Phase 6b: identity_qp (with convention + honest residual; mix may be null)
- Phase 6a: fx_only and override_reason_text
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

import pandas as pd

from sqlalchemy.orm import Session

from app.models.actuals import ActualsRecord
from app.models.driver import DriverLink
from app.models.forecast import ForecastLineResult
from app.models.line_item import LineItemDependency
from app.models.override import Override
from app.services.driver_series import derive_price_from_volume, materialize_driver_series, qp_coherence
from app.services.fx import MissingFxRateError, get_reporting_currency, lookup_rate


def _bridge_waterfall(
    *,
    title: str,
    start_label: str,
    start_value: float,
    buckets: list[tuple[str, float]],
    end_label: str,
    end_value: float,
) -> dict[str, Any]:
    """Payload compatible with ChartRenderer ``bridge`` chart type."""
    data: list[dict[str, Any]] = [
        {"name": start_label, "value": round(start_value, 2), "invisible": 0, "is_total": True}
    ]
    running = start_value
    for label, delta in buckets:
        if abs(delta) < 1e-9:
            continue
        data.append({
            "name": label,
            "value": round(delta, 2),
            "invisible": round(min(running, running + delta), 2),
            "is_total": False,
        })
        running += delta
    data.append({
        "name": end_label,
        "value": round(end_value, 2),
        "invisible": 0,
        "is_total": True,
    })
    return {
        "chart_type": "bridge",
        "title": title,
        "data": data,
        "x_key": "name",
        "y_keys": ["value"],
        "format": "currency",
    }


def _fx_decompose_actuals(
    db: Session,
    *,
    line_item_id: int,
    period_from: str,
    period_to: str,
    reporting_currency: str | None = None,
) -> dict[str, Any] | None:
    """FX plug between two actuals periods when currency differs from reporting.

    Constant-currency Δ = v_to_local × r_from − v_from_local × r_from
    FX plug            = v_to_local × (r_to − r_from)
    """
    reporting = (reporting_currency or get_reporting_currency(db)).upper()
    rows = (
        db.query(ActualsRecord)
        .filter(
            ActualsRecord.line_item_id == line_item_id,
            ActualsRecord.period.in_([period_from, period_to]),
        )
        .all()
    )
    by_period: dict[str, list[ActualsRecord]] = {}
    for r in rows:
        by_period.setdefault(r.period, []).append(r)
    if period_from not in by_period or period_to not in by_period:
        return None

    # Aggregate local by currency (usually one)
    def _agg(period: str) -> tuple[float, str]:
        items = by_period[period]
        # Prefer dominant currency by abs value
        by_ccy: dict[str, float] = {}
        for r in items:
            ccy = (r.currency or reporting).upper()
            by_ccy[ccy] = by_ccy.get(ccy, 0.0) + float(r.value)
        ccy = max(by_ccy, key=lambda c: abs(by_ccy[c]))
        return by_ccy[ccy], ccy

    v0, ccy0 = _agg(period_from)
    v1, ccy1 = _agg(period_to)
    if ccy0 != ccy1:
        # Mixed — cannot form a clean FX plug without a cross-rate path
        return None
    ccy = ccy0
    if ccy == reporting:
        return None  # no FX to attribute

    r0 = lookup_rate(db, ccy, reporting, period_from)
    r1 = lookup_rate(db, ccy, reporting, period_to)
    if r0 is None or r1 is None:
        missing = []
        if r0 is None:
            missing.append((ccy, reporting, period_from))
        if r1 is None:
            missing.append((ccy, reporting, period_to))
        raise MissingFxRateError(missing)

    reporting_from = v0 * r0
    reporting_to = v1 * r1
    total_delta = reporting_to - reporting_from
    fx = v1 * (r1 - r0)
    constant_currency = v1 * r0 - v0 * r0
    residual = total_delta - fx - constant_currency  # should be ~0

    explained = abs(fx) + abs(constant_currency)
    explained_pct = (
        float(explained / abs(total_delta) * 100) if abs(total_delta) > 1e-9 else 100.0
    )
    # Residual always its own bucket — do not force 100%
    buckets = {
        "constant_currency": round(constant_currency, 4),
        "fx": round(fx, 4),
        "unattributed": round(residual, 4),
    }
    return {
        "method": "fx_only",
        "convention": None,
        "period_from": period_from,
        "period_to": period_to,
        "currency_local": ccy,
        "reporting_currency": reporting,
        "rate_from": r0,
        "rate_to": r1,
        "value_local_from": v0,
        "value_local_to": v1,
        "value_reporting_from": round(reporting_from, 4),
        "value_reporting_to": round(reporting_to, 4),
        "total_delta": round(total_delta, 4),
        "buckets": buckets,
        "explained_pct": round(min(explained_pct, 100.0), 2),
        "waterfall": _bridge_waterfall(
            title=f"FX attribution {period_from} → {period_to}",
            start_label=period_from,
            start_value=reporting_from,
            buckets=[
                ("Constant currency", constant_currency),
                ("FX", fx),
                ("Unattributed", residual),
            ],
            end_label=period_to,
            end_value=reporting_to,
        ),
    }


class BridgeAttributionContext:
    """Preloaded overrides + forecast periods for budget-bridge attribution."""

    def __init__(self, db: Session, version_id: str) -> None:
        self.version_id = version_id
        self.overrides_by_li: dict[int, list[Override]] = defaultdict(list)
        for ov in (
            db.query(Override)
            .filter(Override.version_id == version_id, Override.status == "active")
            .order_by(Override.period)
            .all()
        ):
            self.overrides_by_li[int(ov.line_item_id)].append(ov)

        self.periods_by_li: dict[int, list[str]] = defaultdict(list)
        for lid, period in (
            db.query(ForecastLineResult.line_item_id, ForecastLineResult.period)
            .filter(ForecastLineResult.version_id == version_id)
            .all()
        ):
            self.periods_by_li[int(lid)].append(str(period))
        for lid in self.periods_by_li:
            self.periods_by_li[lid] = sorted(set(self.periods_by_li[lid]))


def _override_reason_attribution(
    db: Session,
    *,
    version_id: str,
    line_item_id: int,
    period_from: str | None = None,
    period_to: str | None = None,
    overrides: list[Override] | None = None,
) -> dict[str, Any]:
    """Honest rung: delta + free text under unattributed; explained_pct = 0."""
    if overrides is None:
        q = db.query(Override).filter(
            Override.version_id == version_id,
            Override.line_item_id == line_item_id,
            Override.status == "active",
        )
        if period_from:
            q = q.filter(Override.period >= period_from)
        if period_to:
            q = q.filter(Override.period <= period_to)
        overrides = q.order_by(Override.period).all()
    else:
        overrides = list(overrides)
        if period_from:
            overrides = [o for o in overrides if o.period >= period_from]
        if period_to:
            overrides = [o for o in overrides if o.period <= period_to]

    items = []
    total_delta = 0.0
    for o in overrides:
        delta = float(o.override_value) - float(o.original_model_value)
        total_delta += delta
        items.append({
            "period": o.period,
            "model_value": float(o.original_model_value),
            "override_value": float(o.override_value),
            "delta": delta,
            "reason": o.reason,
        })

    return {
        "method": "override_reason_text",
        "convention": None,
        "total_delta": round(total_delta, 4),
        "buckets": {
            "unattributed": round(total_delta, 4),
        },
        "explained_pct": 0.0,
        "overrides": items,
        "note": (
            "Override deltas are not causally decomposed from free-text reasons. "
            "All variance is reported as unattributed."
        ),
        "waterfall": _bridge_waterfall(
            title="Override variance (unattributed)",
            start_label="Model",
            start_value=sum(i["model_value"] for i in items) if items else 0.0,
            buckets=[("Unattributed", total_delta)],
            end_label="Override",
            end_value=sum(i["override_value"] for i in items) if items else total_delta,
        ) if items else None,
    }


def _line_series(
    db: Session,
    *,
    line_item_id: int,
    version_id: str | None,
) -> pd.Series:
    """Return period-indexed line series from forecast version or actuals."""
    if version_id:
        rows = (
            db.query(ForecastLineResult)
            .filter(
                ForecastLineResult.version_id == version_id,
                ForecastLineResult.line_item_id == line_item_id,
            )
            .order_by(ForecastLineResult.period)
            .all()
        )
        if not rows:
            return pd.Series(dtype=float)
        vals = {}
        for r in rows:
            vals[r.period] = float(r.override_value if r.is_overridden and r.override_value is not None else r.p50)
        return pd.Series(vals, dtype=float).sort_index()

    rows = (
        db.query(ActualsRecord.period, ActualsRecord.value)
        .filter(ActualsRecord.line_item_id == line_item_id)
        .order_by(ActualsRecord.period)
        .all()
    )
    if not rows:
        return pd.Series(dtype=float)
    out: dict[str, float] = {}
    for p, v in rows:
        out[p] = out.get(p, 0.0) + float(v)
    return pd.Series(out, dtype=float).sort_index()


def _line_qp_points(
    db: Session,
    *,
    line_item_id: int,
    period_from: str,
    period_to: str,
    version_id: str | None,
) -> tuple[float, float, float, float, float, float, dict[str, Any]] | None:
    """Return (q0, q1, p0, p1, l0, l1, price_meta) for one line at two periods."""
    links = (
        db.query(DriverLink)
        .filter(
            DriverLink.line_item_id == line_item_id,
            DriverLink.status == "active",
            DriverLink.relation.in_(["quantity", "unit_price"]),
        )
        .all()
    )
    qty_link = next((l for l in links if l.relation == "quantity"), None)
    if qty_link is None:
        return None

    grp = qty_link.composition_group
    price_link = next(
        (
            l for l in links
            if l.relation == "unit_price" and ((grp and l.composition_group == grp) or (not grp))
        ),
        None,
    )

    line = _line_series(db, line_item_id=line_item_id, version_id=version_id)
    if period_from not in line.index or period_to not in line.index:
        return None

    if version_id:
        q = materialize_driver_series(
            db,
            driver_id=qty_link.driver_id,
            value_type="scenario",
            version_id=version_id,
        )
        if q.empty:
            q = materialize_driver_series(db, driver_id=qty_link.driver_id, value_type="actual")
    else:
        q = materialize_driver_series(db, driver_id=qty_link.driver_id, value_type="actual")
    if q.empty or period_from not in q.index or period_to not in q.index:
        return None

    if price_link is not None:
        if version_id:
            p = materialize_driver_series(
                db,
                driver_id=price_link.driver_id,
                value_type="scenario",
                version_id=version_id,
            )
            if p.empty:
                p = materialize_driver_series(db, driver_id=price_link.driver_id, value_type="actual")
        else:
            p = materialize_driver_series(db, driver_id=price_link.driver_id, value_type="actual")
        if p.empty or period_from not in p.index or period_to not in p.index:
            return None
        coherence = qp_coherence(q, p, line)
        if not coherence["coherent"]:
            return None
        price_meta: dict[str, Any] = {"price_source": "stored_driver"}
    else:
        p, price_meta = derive_price_from_volume(line, q)

    return (
        float(q.loc[period_from]),
        float(q.loc[period_to]),
        float(p.loc[period_from]),
        float(p.loc[period_to]),
        float(line.loc[period_from]),
        float(line.loc[period_to]),
        price_meta,
    )


def _identity_qp_mix_for_parent(
    db: Session,
    *,
    line_item_id: int,
    period_from: str,
    period_to: str,
    convention: str,
    version_id: str | None,
    children: list[LineItemDependency],
) -> dict[str, Any] | None:
    """Parent rollup: Volume/Mix/Price bridge across child Q×P components."""
    child_points: list[tuple[float, float, float, float, float, float, dict[str, Any]]] = []
    for dep in children:
        pts = _line_qp_points(
            db,
            line_item_id=int(dep.source_item_id),
            period_from=period_from,
            period_to=period_to,
            version_id=version_id,
        )
        if pts is None:
            return None
        child_points.append(pts)

    sum_q0 = sum(p[0] for p in child_points)
    sum_q1 = sum(p[1] for p in child_points)
    l0_children = sum(p[4] for p in child_points)
    l1_children = sum(p[5] for p in child_points)

    parent_line = _line_series(db, line_item_id=line_item_id, version_id=version_id)
    if period_from in parent_line.index and period_to in parent_line.index:
        l0 = float(parent_line.loc[period_from])
        l1 = float(parent_line.loc[period_to])
    else:
        l0, l1 = l0_children, l1_children

    total_delta = l1 - l0
    if abs(sum_q0) < 1e-12 or abs(sum_q1) < 1e-12:
        return None

    p_bar_0 = l0 / sum_q0
    p_bar_1 = l1 / sum_q1
    p_bar_0_mix = sum(p[1] * p[2] for p in child_points) / sum_q1

    volume = (sum_q1 - sum_q0) * p_bar_0
    mix = sum_q1 * (p_bar_0_mix - p_bar_0)
    price = sum_q1 * (p_bar_1 - p_bar_0_mix)
    residual = total_delta - volume - mix - price

    explained = abs(volume) + abs(mix) + abs(price)
    explained_pct = float(explained / abs(total_delta) * 100) if abs(total_delta) > 1e-9 else 100.0

    conv = convention if convention in {"volume_first", "price_first"} else "volume_first"
    buckets: dict[str, float | None] = {
        "volume": round(volume, 4),
        "mix": round(mix, 4),
        "price": round(price, 4),
        "unattributed": round(residual, 4),
    }
    return {
        "method": "identity_qp_mix",
        "convention": conv,
        "period_from": period_from,
        "period_to": period_to,
        "total_delta": round(total_delta, 4),
        "buckets": buckets,
        "explained_pct": round(min(explained_pct, 100.0), 2),
        "child_line_item_ids": [int(d.source_item_id) for d in children],
        "mix_available": True,
        "waterfall": _bridge_waterfall(
            title=f"Mix decomposition {period_from} → {period_to}",
            start_label=period_from,
            start_value=l0,
            buckets=[
                ("Volume", volume),
                ("Mix", mix),
                ("Price", price),
                ("Unattributed", residual),
            ],
            end_label=period_to,
            end_value=l1,
        ),
    }


def _identity_qp_attribution(
    db: Session,
    *,
    line_item_id: int,
    period_from: str,
    period_to: str,
    convention: str,
    version_id: str | None = None,
) -> dict[str, Any] | None:
    """Identity Q×P decomposition for a line item when quantity link exists."""
    children = (
        db.query(LineItemDependency)
        .filter(LineItemDependency.dependent_item_id == line_item_id)
        .all()
    )
    if len(children) >= 2:
        mix_attr = _identity_qp_mix_for_parent(
            db,
            line_item_id=line_item_id,
            period_from=period_from,
            period_to=period_to,
            convention=convention,
            version_id=version_id,
            children=children,
        )
        if mix_attr is not None:
            return mix_attr

    pts = _line_qp_points(
        db,
        line_item_id=line_item_id,
        period_from=period_from,
        period_to=period_to,
        version_id=version_id,
    )
    if pts is None:
        return None

    q0, q1, p0, p1, l0, l1, price_meta = pts
    total_delta = l1 - l0

    conv = convention if convention in {"volume_first", "price_first"} else "volume_first"
    if conv == "price_first":
        price = (p1 - p0) * q0
        volume = (q1 - q0) * p1
    else:
        volume = (q1 - q0) * p0
        price = (p1 - p0) * q1

    residual = total_delta - volume - price
    explained = abs(volume) + abs(price)
    explained_pct = float(explained / abs(total_delta) * 100) if abs(total_delta) > 1e-9 else 100.0

    buckets: dict[str, float | None] = {
        "volume": round(volume, 4),
        "price": round(price, 4),
        "mix": None,
        "unattributed": round(residual, 4),
    }
    return {
        "method": "identity_qp",
        "convention": conv,
        "period_from": period_from,
        "period_to": period_to,
        "total_delta": round(total_delta, 4),
        "buckets": buckets,
        "explained_pct": round(min(explained_pct, 100.0), 2),
        "price_meta": price_meta,
        "mix_available": len(children) > 0,
        "waterfall": _bridge_waterfall(
            title=f"Identity decomposition {period_from} → {period_to}",
            start_label=period_from,
            start_value=l0,
            buckets=[
                ("Volume", volume),
                ("Price", price),
                ("Unattributed", residual),
            ],
            end_label=period_to,
            end_value=l1,
        ),
    }


def attribute_variance(
    db: Session,
    *,
    line_item_id: int,
    period_from: str | None = None,
    period_to: str | None = None,
    basis: str = "auto",
    convention: str = "volume_first",
    version_id: str | None = None,
) -> dict[str, Any]:
    """Attribute variance for a line item.

    Phase 6a ladder (no drivers): ``fx_only`` → ``override_reason_text``.
    ``convention`` is accepted and echoed for API stability (used in 6b).
    """
    basis_l = (basis or "auto").lower()
    result: dict[str, Any] = {
        "line_item_id": line_item_id,
        "period_from": period_from,
        "period_to": period_to,
        "basis": basis_l,
        "convention": convention,
    }

    # Phase 6b: identity Q×P first (when requested or auto)
    if basis_l in {"auto", "identity", "identity_qp"} and period_from and period_to:
        ident = _identity_qp_attribution(
            db,
            line_item_id=line_item_id,
            period_from=period_from,
            period_to=period_to,
            convention=convention,
            version_id=version_id,
        )
        if ident is not None:
            return {**result, **ident}

    # Phase 6a: FX when two periods given and rates available
    if basis_l in {"auto", "fx", "fx_only"} and period_from and period_to:
        try:
            fx = _fx_decompose_actuals(
                db,
                line_item_id=line_item_id,
                period_from=period_from,
                period_to=period_to,
            )
            if fx is not None:
                return {**result, **fx}
        except MissingFxRateError as e:
            result["fx_error"] = str(e)

    if basis_l in {"auto", "override", "override_reason_text"} and version_id:
        ov = _override_reason_attribution(
            db,
            version_id=version_id,
            line_item_id=line_item_id,
            period_from=period_from,
            period_to=period_to,
        )
        if ov["overrides"] or basis_l != "auto":
            return {**result, **ov}

    # Nothing to attribute
    return {
        **result,
        "method": "none",
        "total_delta": 0.0,
        "buckets": {"unattributed": 0.0},
        "explained_pct": 0.0,
        "note": "No FX differential or active overrides available for attribution.",
        "waterfall": None,
    }


def attribute_bridge_row(
    db: Session,
    *,
    line_item_id: int,
    version_id: str,
    variance_vs_prior: float,
    convention: str = "volume_first",
    ctx: BridgeAttributionContext | None = None,
) -> dict[str, Any]:
    """Per-row attribution for budget_bridge (?attribute=true).

    Uses override_reason_text when overrides exist; otherwise reports the
    whole variance_vs_prior as unattributed (explained_pct=0).
    """
    preloaded = ctx.overrides_by_li.get(line_item_id) if ctx is not None else None
    ov = _override_reason_attribution(
        db,
        version_id=version_id,
        line_item_id=line_item_id,
        overrides=preloaded,
    )
    if ov["overrides"]:
        return {**ov, "convention": convention, "line_item_id": line_item_id}

    # Try identity on first/last period in this version.
    if ctx is not None:
        periods = ctx.periods_by_li.get(line_item_id, [])
    else:
        flrs = (
            db.query(ForecastLineResult)
            .filter(
                ForecastLineResult.version_id == version_id,
                ForecastLineResult.line_item_id == line_item_id,
            )
            .all()
        )
        periods = sorted({r.period for r in flrs})
    if len(periods) >= 2:
        ident = _identity_qp_attribution(
            db,
            line_item_id=line_item_id,
            period_from=periods[0],
            period_to=periods[-1],
            convention=convention,
            version_id=version_id,
        )
        if ident is not None:
            return {**ident, "line_item_id": line_item_id}

    # Forecast-line residual without causal decomposition
    return {
        "line_item_id": line_item_id,
        "method": "override_reason_text",
        "convention": convention,
        "total_delta": round(float(variance_vs_prior), 4),
        "buckets": {"unattributed": round(float(variance_vs_prior), 4)},
        "explained_pct": 0.0,
        "periods": periods,
        "note": (
            "No driver-based decomposition available; variance reported as unattributed."
        ),
        "waterfall": _bridge_waterfall(
            title="Variance (unattributed)",
            start_label="Prior",
            start_value=0.0,
            buckets=[("Unattributed", float(variance_vs_prior))],
            end_label="Current",
            end_value=float(variance_vs_prior),
        ),
    }
