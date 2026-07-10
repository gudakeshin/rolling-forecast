"""Hierarchical forecast reconciliation (MinT-style) for CoA rollups.

After leaf forecasts are produced, calculated parents are reconciled so that
parent p50 equals the structural sum of children, and interval bounds use a
covariance-aware aggregation (diagonal MinT / OLS) instead of naively summing
percentiles.

Full cross-dimensional MinT (BU × product × geo) remains future work; this
module reconciles the chart-of-accounts dependency DAG.
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np
from sqlalchemy.orm import Session

from app.models.forecast import ForecastLineResult
from app.models.line_item import LineItem
from app.services.dependency_graph import DependencyGraphManager

logger = logging.getLogger(__name__)

BOUNDS_METHOD_MODEL = "model"
BOUNDS_METHOD_LINEAR = "linear_aggregation"
BOUNDS_METHOD_MINT = "mint_diagonal"


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
) -> dict[str, Any]:
    """Reconcile calculated lines for a forecast version in-place.

    1. Run structural CoA recompute (p50 identities via DependencyGraphManager).
    2. For bounds: diagonal MinT — treat each leaf's (p90-p10)/2 as σ, propagate
       parent σ = sqrt(sum(w_i^2 σ_i^2)) for sum edges, then p10/p90 = p50 ± z*σ
       with z≈1.28 for nominal 80% intervals.
    """
    graph = DependencyGraphManager(db)
    n_recalc = graph.recalculate_all(version_id)

    if method == BOUNDS_METHOD_LINEAR:
        # recalculate_all already applied linear sum of percentiles
        _tag_bounds(db, version_id, BOUNDS_METHOD_LINEAR)
        db.flush()
        return {"recalculated": n_recalc, "bounds_method": BOUNDS_METHOD_LINEAR}

    # Diagonal MinT-style bound propagation
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
    z = 1.2815515655446004  # norm.ppf(0.9) for 80% central interval
    tagged = 0

    # Precompute leaf sigmas
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
            # Variance additivity for sum/subtract; scale for multiply weights
            var = 0.0
            for pred_id in predecessors:
                edge = G.edges[pred_id, node_id]
                w = float(edge.get("weight", 1.0) or 1.0)
                rel = edge.get("relationship_type", "sum")
                s = sigma.get((pred_id, period), 0.0)
                if rel in ("sum", "add", "subtract"):
                    var += (w * s) ** 2
                elif rel == "multiply":
                    # First-order: treat as weighted sum of relative uncertainty
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

    db.flush()
    return {
        "recalculated": n_recalc,
        "bounds_method": method,
        "bounds_tagged": tagged,
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
