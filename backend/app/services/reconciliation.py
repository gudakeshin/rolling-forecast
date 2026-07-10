"""Hierarchical forecast reconciliation (MinT-style) for CoA and cross-dim rollups.

After leaf forecasts are produced, calculated parents are reconciled so that
parent p50 equals the structural sum of children, and interval bounds use a
covariance-aware aggregation (diagonal MinT / OLS) instead of naively summing
percentiles.

Also reconciles cross-dimensional totals: line items with NULL business_unit /
geography / product_line that share account_code (or name+category) with
dimensioned siblings are treated as rollups and get MinT-diagonal bounds.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any, Sequence

import numpy as np
from sqlalchemy.orm import Session

from app.models.forecast import ForecastLineResult
from app.models.line_item import LineItem
from app.services.dependency_graph import DependencyGraphManager

logger = logging.getLogger(__name__)

BOUNDS_METHOD_MODEL = "model"
BOUNDS_METHOD_LINEAR = "linear_aggregation"
BOUNDS_METHOD_MINT = "mint_diagonal"
BOUNDS_METHOD_MINT_CROSS = "mint_cross_dimensional"

# Nominal 80% central interval z-score
_Z80 = 1.2815515655446004

DEFAULT_CROSS_DIMS: tuple[str, ...] = ("business_unit", "geography", "product_line")


def _leaf_ids(db: Session) -> set[int]:
    return {
        li.id
        for li in db.query(LineItem).filter(LineItem.is_calculated == False).all()  # noqa: E712
    }


def reconcile_version(
    db: Session,
    version_id: str,
    *,
    method: str = BOUNDS_METHOD_MINT,
    cross_dimensions: Sequence[str] | None = DEFAULT_CROSS_DIMS,
) -> dict[str, Any]:
    """Reconcile calculated lines for a forecast version in-place.

    1. Run structural CoA recompute (p50 identities via DependencyGraphManager).
    2. For bounds: diagonal MinT — treat each leaf's (p90-p10)/2 as σ, propagate
       parent σ = sqrt(sum(w_i^2 σ_i^2)) for sum edges, then p10/p90 = p50 ± z*σ
       with z≈1.28 for nominal 80% intervals.
    3. Optionally reconcile cross-dimensional rollups (BU × geo × product).
    """
    graph = DependencyGraphManager(db)
    n_recalc = graph.recalculate_all(version_id)

    if method == BOUNDS_METHOD_LINEAR:
        _tag_bounds(db, version_id, BOUNDS_METHOD_LINEAR)
        db.flush()
        cross = (
            reconcile_cross_dimensional(db, version_id, dimensions=cross_dimensions, method=BOUNDS_METHOD_LINEAR)
            if cross_dimensions
            else {"rollup_updated": 0}
        )
        return {
            "recalculated": n_recalc,
            "bounds_method": BOUNDS_METHOD_LINEAR,
            "cross_dimensional": cross,
        }

    # Diagonal MinT-style bound propagation along CoA DAG
    G = graph._load_graph()
    import networkx as nx

    try:
        order = list(nx.topological_sort(G))
    except Exception:
        logger.exception("MinT: graph not sortable")
        return {"recalculated": n_recalc, "bounds_method": BOUNDS_METHOD_LINEAR, "error": "cycle"}

    rows = (
        db.query(ForecastLineResult)
        .filter(ForecastLineResult.version_id == version_id)
        .all()
    )
    by_key: dict[tuple[int, str], ForecastLineResult] = {
        (r.line_item_id, r.period): r for r in rows
    }
    periods = sorted({r.period for r in rows})
    z = _Z80
    tagged = 0

    sigma: dict[tuple[int, str], float] = {}
    for (lid, period), r in by_key.items():
        if r.p10 is not None and r.p90 is not None:
            sigma[(lid, period)] = max((r.p90 - r.p10) / (2 * z), 0.0)
        else:
            sigma[(lid, period)] = 0.0

    for node_id in order:
        node_data = G.nodes.get(node_id, {})
        if not node_data.get("is_calculated"):
            continue
        predecessors = list(G.predecessors(node_id))
        for period in periods:
            result = by_key.get((node_id, period))
            if not result:
                continue
            var = 0.0
            for pred_id in predecessors:
                edge = G.edges[pred_id, node_id]
                w = float(edge.get("weight", 1.0) or 1.0)
                rel = edge.get("relationship_type", "sum")
                s = sigma.get((pred_id, period), 0.0)
                if rel in ("sum", "add", "subtract"):
                    var += (w * s) ** 2
                elif rel == "multiply":
                    pred = by_key.get((pred_id, period))
                    base = abs(pred.p50) if pred and pred.p50 else 1.0
                    var += (w * s / base * abs(result.p50 or 0.0)) ** 2
                else:
                    var += (w * s) ** 2
            parent_sigma = float(np.sqrt(var)) if var > 0 else 0.0
            sigma[(node_id, period)] = parent_sigma
            if parent_sigma > 0 and result.p50 is not None:
                result.p10 = result.p50 - z * parent_sigma
                result.p90 = result.p50 + z * parent_sigma
                if hasattr(result, "bounds_method"):
                    result.bounds_method = BOUNDS_METHOD_MINT
                tagged += 1
            elif hasattr(result, "bounds_method"):
                result.bounds_method = BOUNDS_METHOD_LINEAR

    cross: dict[str, Any] = {"rollup_updated": 0}
    if cross_dimensions:
        cross = reconcile_cross_dimensional(
            db, version_id, dimensions=cross_dimensions, method=method
        )

    db.flush()
    return {
        "recalculated": n_recalc,
        "bounds_method": method,
        "bounds_tagged": tagged,
        "cross_dimensional": cross,
    }


def reconcile_cross_dimensional(
    db: Session,
    version_id: str,
    *,
    dimensions: Sequence[str] = DEFAULT_CROSS_DIMS,
    method: str = BOUNDS_METHOD_MINT,
) -> dict[str, Any]:
    """Reconcile dimension-null rollup line items from dimensioned siblings.

    A rollup is a line item with the same name+category as dimensioned siblings
    (account_code is unique per row). The rollup itself is null on all
    ``dimensions``. Updates rollup p50 = sum(children) with MinT/linear bounds.
    """
    dims = [d for d in dimensions if d in ("business_unit", "geography", "product_line")]
    if not dims:
        return {"rollup_updated": 0, "dimensions": []}

    items = db.query(LineItem).all()

    def dim_values(li: LineItem) -> dict[str, str | None]:
        return {d: getattr(li, d, None) for d in dims}

    def is_rollup(li: LineItem) -> bool:
        vals = dim_values(li)
        return all(v is None or v == "" for v in vals.values())

    def is_dimensioned(li: LineItem) -> bool:
        vals = dim_values(li)
        return any(v is not None and v != "" for v in vals.values())

    # Group by name+category (account_code is unique per row; dims differentiate siblings)
    def group_key(li: LineItem) -> str:
        return f"nc:{li.name}|{li.category}"

    groups: dict[str, list[LineItem]] = defaultdict(list)
    for li in items:
        groups[group_key(li)].append(li)

    rows = (
        db.query(ForecastLineResult)
        .filter(ForecastLineResult.version_id == version_id)
        .all()
    )
    by_key: dict[tuple[int, str], ForecastLineResult] = {
        (r.line_item_id, r.period): r for r in rows
    }
    periods = sorted({r.period for r in rows})
    z = _Z80
    updated = 0
    groups_touched = 0

    for _key, members in groups.items():
        rollups = [li for li in members if is_rollup(li)]
        children = [li for li in members if is_dimensioned(li) and not li.is_calculated]
        if not rollups or len(children) < 2:
            continue
        # Prefer a single rollup; if several, update each identically from children
        groups_touched += 1
        for rollup in rollups:
            for period in periods:
                child_rows = [
                    by_key[(c.id, period)]
                    for c in children
                    if (c.id, period) in by_key
                ]
                if len(child_rows) < 2:
                    continue
                p50 = float(sum(r.p50 or 0.0 for r in child_rows))
                if method == BOUNDS_METHOD_LINEAR:
                    p10 = float(sum(r.p10 or 0.0 for r in child_rows))
                    p90 = float(sum(r.p90 or 0.0 for r in child_rows))
                    bounds_tag = BOUNDS_METHOD_LINEAR
                else:
                    var = 0.0
                    for r in child_rows:
                        if r.p10 is not None and r.p90 is not None:
                            s = max((r.p90 - r.p10) / (2 * z), 0.0)
                            var += s ** 2
                    parent_sigma = float(np.sqrt(var)) if var > 0 else 0.0
                    p10 = p50 - z * parent_sigma
                    p90 = p50 + z * parent_sigma
                    bounds_tag = BOUNDS_METHOD_MINT_CROSS

                result = by_key.get((rollup.id, period))
                if result is None:
                    result = ForecastLineResult(
                        version_id=version_id,
                        line_item_id=rollup.id,
                        period=period,
                        p10=p10,
                        p50=p50,
                        p90=p90,
                        is_calculated=True,
                        bounds_method=bounds_tag,
                        model_type="cross_dimensional_rollup",
                    )
                    db.add(result)
                    by_key[(rollup.id, period)] = result
                else:
                    result.p50 = p50
                    result.p10 = p10
                    result.p90 = p90
                    result.is_calculated = True
                    if hasattr(result, "bounds_method"):
                        result.bounds_method = bounds_tag
                updated += 1

    db.flush()
    return {
        "rollup_updated": updated,
        "groups_touched": groups_touched,
        "dimensions": list(dims),
        "bounds_method": method if method == BOUNDS_METHOD_LINEAR else BOUNDS_METHOD_MINT_CROSS,
    }


def _tag_bounds(db: Session, version_id: str, method: str) -> None:
    rows = (
        db.query(ForecastLineResult)
        .filter(
            ForecastLineResult.version_id == version_id,
            ForecastLineResult.is_calculated == True,  # noqa: E712
        )
        .all()
    )
    for r in rows:
        if hasattr(r, "bounds_method"):
            r.bounds_method = method
