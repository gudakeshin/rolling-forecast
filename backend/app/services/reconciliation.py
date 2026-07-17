"""Hierarchical forecast reconciliation — diagonal and full MinT.

After leaf forecasts are produced, calculated parents are reconciled so that
structural CoA identities hold, and interval bounds reflect forecast-error
covariance.

Methods
-------
- ``linear_aggregation``: sum/subtract child p10/p50/p90 directly (naive).
- ``mint_diagonal``: independent errors — parent σ = sqrt(Σ (w_i σ_i)²).
- ``mint_full`` (default): Wickramasuriya-style MinT with non-diagonal W —
  residual correlations estimated from actuals (shrinkage) or category blocks,
  point forecasts projected via S(S'W⁻¹S)⁻¹S'W⁻¹ŷ, intervals from s'Ws.

Cross-dimensional rollups (NULL BU/geo/product vs dimensioned siblings) are
reconciled with the same covariance machinery.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any, Sequence

import numpy as np
from sqlalchemy.orm import Session

from app.models.forecast import ForecastLineResult, ForecastVersion
from app.models.line_item import LineItem
from app.services.dependency_graph import DependencyGraphManager

logger = logging.getLogger(__name__)

BOUNDS_METHOD_MODEL = "model"
BOUNDS_METHOD_LINEAR = "linear_aggregation"
BOUNDS_METHOD_MINT = "mint_diagonal"
BOUNDS_METHOD_MINT_FULL = "mint_full"
BOUNDS_METHOD_MINT_CROSS = "mint_cross_dimensional"

# Nominal 80% central interval z-score
_Z80 = 1.2815515655446004

# Same-category residual correlation prior when sample is thin
_CATEGORY_RHO = 0.35
# Shrinkage of sample correlation toward prior / identity
_CORR_SHRINKAGE = 0.45

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
    method: str = BOUNDS_METHOD_MINT_FULL,
    cross_dimensions: Sequence[str] | None = DEFAULT_CROSS_DIMS,
) -> dict[str, Any]:
    """Reconcile calculated lines for a forecast version in-place.

    Default method is full MinT (correlation-aware). Pass ``mint_diagonal`` or
    ``linear_aggregation`` to force the simpler estimators.
    """
    if method == BOUNDS_METHOD_LINEAR:
        return _reconcile_linear(db, version_id, cross_dimensions)
    if method == BOUNDS_METHOD_MINT:
        return _reconcile_mint_diagonal(db, version_id, cross_dimensions)
    # mint_full and any unknown → full MinT
    return _reconcile_mint_full(db, version_id, cross_dimensions)


def _reconcile_linear(
    db: Session,
    version_id: str,
    cross_dimensions: Sequence[str] | None,
) -> dict[str, Any]:
    graph = DependencyGraphManager(db)
    n_recalc = graph.recalculate_all(version_id)
    _tag_bounds(db, version_id, BOUNDS_METHOD_LINEAR)
    db.flush()
    cross = (
        reconcile_cross_dimensional(
            db, version_id, dimensions=cross_dimensions, method=BOUNDS_METHOD_LINEAR
        )
        if cross_dimensions
        else {"rollup_updated": 0}
    )
    return {
        "recalculated": n_recalc,
        "bounds_method": BOUNDS_METHOD_LINEAR,
        "cross_dimensional": cross,
    }


def _reconcile_mint_diagonal(
    db: Session,
    version_id: str,
    cross_dimensions: Sequence[str] | None,
) -> dict[str, Any]:
    """Original diagonal-MinT path (independent leaf errors)."""
    graph = DependencyGraphManager(db)
    n_recalc = graph.recalculate_all(version_id)

    G = graph._load_graph()
    import networkx as nx

    try:
        order = list(nx.topological_sort(G))
    except Exception:
        logger.exception("MinT diagonal: graph not sortable")
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
            db, version_id, dimensions=cross_dimensions, method=BOUNDS_METHOD_MINT
        )

    db.flush()
    return {
        "recalculated": n_recalc,
        "bounds_method": BOUNDS_METHOD_MINT,
        "bounds_tagged": tagged,
        "cross_dimensional": cross,
    }


# ── Full MinT ───────────────────────────────────────────────────────────────


def build_summing_matrix(
    G,
    leaf_ids: list[int],
    node_ids: list[int],
) -> np.ndarray:
    """Build S (n_nodes × n_leaves) so that y_all ≈ S @ y_leaves for sum/subtract CoA.

    Each column is a leaf; each row expands a node into signed leaf weights by
    walking predecessors. Non-linear edges (multiply) are treated as weight 0
    for the linear MinT projection (those parents keep structural recompute only).
    """
    leaf_index = {lid: i for i, lid in enumerate(leaf_ids)}
    n_leaves = len(leaf_ids)
    n_nodes = len(node_ids)
    S = np.zeros((n_nodes, n_leaves), dtype=float)

    memo: dict[int, np.ndarray] = {}

    def expand(node_id: int) -> np.ndarray:
        if node_id in memo:
            return memo[node_id]
        vec = np.zeros(n_leaves, dtype=float)
        if node_id in leaf_index:
            vec[leaf_index[node_id]] = 1.0
            memo[node_id] = vec
            return vec
        if node_id not in G:
            memo[node_id] = vec
            return vec
        preds = list(G.predecessors(node_id))
        if not preds:
            # Calculated with no edges — leave zero row
            memo[node_id] = vec
            return vec
        for pred_id in preds:
            edge = G.edges[pred_id, node_id]
            w = float(edge.get("weight", 1.0) or 1.0)
            rel = edge.get("relationship_type", "sum")
            if rel in ("subtract", "sub"):
                sign = -1.0
            elif rel in ("sum", "add"):
                sign = 1.0
            else:
                # multiply / custom — skip in linear S
                continue
            updated = vec + sign * w * expand(pred_id)
            vec = updated.reshape(n_leaves)  # keep 1-d shape for mypy
        memo[node_id] = vec
        return vec

    for i, nid in enumerate(node_ids):
        S[i, :] = expand(nid)
    return S


def estimate_leaf_correlation(
    db: Session,
    leaf_ids: list[int],
    *,
    dataset_id: str | None,
    categories: dict[int, str],
) -> np.ndarray:
    """n×n correlation matrix for leaves.

    Prefers sample correlation of demeaned actuals (shrinkage toward a
    category-block prior). Falls back to the prior alone when history is thin.
    """
    n = len(leaf_ids)
    prior = _category_block_correlation(leaf_ids, categories)

    if not dataset_id or n == 0:
        return prior

    from app.models.actuals import ActualsRecord

    series: dict[int, dict[str, float]] = {lid: {} for lid in leaf_ids}
    records = (
        db.query(ActualsRecord)
        .filter(
            ActualsRecord.dataset_id == dataset_id,
            ActualsRecord.line_item_id.in_(leaf_ids),
        )
        .all()
    )
    for rec in records:
        series[rec.line_item_id][rec.period] = float(rec.value)

    # Align on common periods
    period_sets = [set(series[lid].keys()) for lid in leaf_ids if series[lid]]
    if not period_sets:
        return prior
    common = set.intersection(*period_sets) if period_sets else set()
    if len(common) < 4:
        return prior

    periods = sorted(common)
    X = np.column_stack(
        [[series[lid][p] for p in periods] for lid in leaf_ids]
    )  # T × n
    # Demean
    X = X - X.mean(axis=0, keepdims=True)
    # Drop near-zero variance columns for corr stability
    std = X.std(axis=0, ddof=1)
    std_safe = np.where(std < 1e-12, 1.0, std)
    Z = X / std_safe
    sample = np.corrcoef(Z, rowvar=False)
    if sample.shape != (n, n) or not np.isfinite(sample).all():
        return prior
    np.fill_diagonal(sample, 1.0)

    delta = _CORR_SHRINKAGE
    shrunk = (1.0 - delta) * sample + delta * prior
    # Project to nearest PSD via eigenvalue clip
    return _nearest_correlation(shrunk)


def _category_block_correlation(
    leaf_ids: list[int], categories: dict[int, str]
) -> np.ndarray:
    n = len(leaf_ids)
    R = np.eye(n, dtype=float)
    for i, a in enumerate(leaf_ids):
        for j, b in enumerate(leaf_ids):
            if i >= j:
                continue
            ca = (categories.get(a) or "").lower()
            cb = (categories.get(b) or "").lower()
            if ca and ca == cb:
                R[i, j] = R[j, i] = _CATEGORY_RHO
    return R


def _nearest_correlation(A: np.ndarray) -> np.ndarray:
    """Symmetrize and clip eigenvalues to keep a valid correlation matrix."""
    A = 0.5 * (A + A.T)
    np.fill_diagonal(A, 1.0)
    try:
        eigvals, eigvecs = np.linalg.eigh(A)
    except np.linalg.LinAlgError:
        return np.eye(A.shape[0])
    eigvals = np.clip(eigvals, 1e-8, None)
    B = eigvecs @ np.diag(eigvals) @ eigvecs.T
    # Rescale to unit diagonal
    d = np.sqrt(np.clip(np.diag(B), 1e-12, None))
    B = B / np.outer(d, d)
    np.fill_diagonal(B, 1.0)
    return B


def mint_project(y_hat: np.ndarray, S: np.ndarray, W: np.ndarray) -> np.ndarray:
    """Full MinT projection: ỹ = S (S' W⁻¹ S)⁻¹ S' W⁻¹ ŷ.

    ``y_hat`` and rows of ``S`` are aligned (n_nodes,). ``W`` is n_nodes×n_nodes
    base-forecast error covariance. Falls back to bottom-up ``S @ y_hat[leaves]``
    when W is singular beyond repair.

    Honesty note: when all non-leaf nodes are structurally calculated from leaves
    (parents already equal ``S @ y_leaf``), this projection is bottom-up-equivalent
    (``y_tilde ≈ S @ y_leaf``). Divergence only appears when incoherent parent
    base forecasts are present in ``y_hat``.
    """
    y_hat = np.asarray(y_hat, dtype=float).reshape(-1)
    n = y_hat.shape[0]
    if S.shape[0] != n:
        raise ValueError(f"S rows {S.shape[0]} != y_hat length {n}")
    W = np.asarray(W, dtype=float)
    if W.shape != (n, n):
        raise ValueError(f"W shape {W.shape} != ({n}, {n})")

    # Ridge for numerical stability
    ridge = 1e-8 * float(np.trace(W) / max(n, 1) + 1.0)
    W_reg = W + np.eye(n) * ridge
    try:
        W_inv = np.linalg.inv(W_reg)
    except np.linalg.LinAlgError:
        W_inv = np.linalg.pinv(W_reg)

    StW = S.T @ W_inv
    G = StW @ S  # n_leaves × n_leaves
    try:
        G_inv = np.linalg.inv(G + np.eye(G.shape[0]) * 1e-10)
    except np.linalg.LinAlgError:
        G_inv = np.linalg.pinv(G)

    # P = S (S'W⁻¹S)⁻¹ S'W⁻¹
    y_tilde = S @ (G_inv @ (StW @ y_hat))
    return y_tilde


def _leaf_sigma_from_intervals(
    by_key: dict[tuple[int, str], ForecastLineResult],
    leaf_ids: list[int],
    period: str,
    z: float = _Z80,
) -> np.ndarray:
    sig = np.zeros(len(leaf_ids), dtype=float)
    for i, lid in enumerate(leaf_ids):
        r = by_key.get((lid, period))
        if r is not None and r.p10 is not None and r.p90 is not None:
            sig[i] = max((r.p90 - r.p10) / (2 * z), 0.0)
        elif r is not None and r.p50:
            # 5% relative fallback when interval bounds are missing
            sig[i] = abs(float(r.p50)) * 0.05
    return sig


def _expand_cov_to_all_nodes(
    S: np.ndarray,
    W_leaf: np.ndarray,
) -> np.ndarray:
    """Map leaf covariance to all-node base-error cov via W_all ≈ S W_leaf S'.

    Used as the MinT W when we only trust leaf residual structure; parent base
    errors are implied by the summing matrix.
    """
    return S @ W_leaf @ S.T


def _reconcile_mint_full(
    db: Session,
    version_id: str,
    cross_dimensions: Sequence[str] | None,
) -> dict[str, Any]:
    """Full MinT: correlation-aware projection + s'Ws interval bounds."""
    graph = DependencyGraphManager(db)
    G = graph._load_graph()
    import networkx as nx

    try:
        order = list(nx.topological_sort(G))
    except Exception:
        logger.exception("MinT full: graph not sortable; falling back to diagonal")
        return _reconcile_mint_diagonal(db, version_id, cross_dimensions)

    # Snapshot base p50 before structural overwrite (captures any parent forecasts)
    rows_pre = (
        db.query(ForecastLineResult)
        .filter(ForecastLineResult.version_id == version_id)
        .all()
    )
    y_hat_snap: dict[tuple[int, str], float] = {
        (r.line_item_id, r.period): float(r.p50 or 0.0) for r in rows_pre
    }

    n_recalc = graph.recalculate_all(version_id)

    # Refresh rows after recompute
    rows = (
        db.query(ForecastLineResult)
        .filter(ForecastLineResult.version_id == version_id)
        .all()
    )
    by_key: dict[tuple[int, str], ForecastLineResult] = {
        (r.line_item_id, r.period): r for r in rows
    }
    periods = sorted({r.period for r in rows})

    items = {li.id: li for li in db.query(LineItem).all()}
    leaf_ids = sorted(
        lid for lid, li in items.items() if not li.is_calculated and lid in G
    )
    # All nodes that appear in this version's results (stable order: topo then leftovers)
    present = {r.line_item_id for r in rows}
    node_ids = [nid for nid in order if nid in present]
    for lid in present:
        if lid not in node_ids:
            node_ids.append(lid)

    if not leaf_ids or not node_ids:
        return {
            "recalculated": n_recalc,
            "bounds_method": BOUNDS_METHOD_MINT_FULL,
            "bounds_tagged": 0,
            "warning": "no leaves/nodes",
        }

    S = build_summing_matrix(G, leaf_ids, node_ids)
    node_index = {nid: i for i, nid in enumerate(node_ids)}
    leaf_set = set(leaf_ids)

    version = db.query(ForecastVersion).filter(ForecastVersion.id == version_id).first()
    dataset_id = version.actuals_dataset_id if version else None
    categories = {lid: (items[lid].category or "") for lid in leaf_ids if lid in items}
    R = estimate_leaf_correlation(
        db, leaf_ids, dataset_id=dataset_id, categories=categories
    )

    z = _Z80
    tagged = 0
    projected_periods = 0

    for period in periods:
        sigma_leaf = _leaf_sigma_from_intervals(by_key, leaf_ids, period, z=z)
        # W_leaf = D^{1/2} R D^{1/2}
        D_sqrt = np.diag(sigma_leaf)
        W_leaf = D_sqrt @ R @ D_sqrt
        W_all = _expand_cov_to_all_nodes(S, W_leaf)

        # Base forecast vector: leaves from model; parents prefer pre-recompute
        # snapshot when it differs from pure bottom-up (incoherent prior), else
        # use structural S @ leaves so MinT has a coherent target.
        y_leaf = np.array(
            [
                float(by_key[(lid, period)].p50)
                if (lid, period) in by_key
                else 0.0
                for lid in leaf_ids
            ],
            dtype=float,
        )
        y_struct = S @ y_leaf
        y_hat = y_struct.copy()
        for nid, i in node_index.items():
            if nid in leaf_set:
                continue
            snapped = y_hat_snap.get((nid, period))
            structural = float(y_struct[i])
            # If a prior parent forecast exists and diverges, include it so MinT
            # can blend leaf and aggregate information.
            if snapped is not None and abs(snapped - structural) > 1e-6 * (abs(structural) + 1.0):
                y_hat[i] = snapped

        try:
            y_tilde = mint_project(y_hat, S, W_all)
        except Exception:
            logger.exception("MinT projection failed for period %s; using structural", period)
            y_tilde = y_struct

        # Write reconciled p50 + correlation-aware intervals
        leaf_pos = {lid: i for i, lid in enumerate(leaf_ids)}
        for nid, i in node_index.items():
            result = by_key.get((nid, period))
            if result is None:
                continue
            result.p50 = float(y_tilde[i])

            # Interval from row variance: s_i' W_leaf s_i under bottom-error model
            s_row = S[i, :]
            var = float(s_row @ W_leaf @ s_row)
            parent_sigma = float(np.sqrt(max(var, 0.0)))
            if nid in leaf_set:
                li = leaf_pos[nid]
                moved = abs(y_tilde[i] - y_leaf[li]) > 1e-9
                if moved and parent_sigma > 0:
                    result.p10 = result.p50 - z * parent_sigma
                    result.p90 = result.p50 + z * parent_sigma
                    if hasattr(result, "bounds_method"):
                        result.bounds_method = BOUNDS_METHOD_MINT_FULL
                elif hasattr(result, "bounds_method") and result.bounds_method is None:
                    result.bounds_method = BOUNDS_METHOD_MODEL
            else:
                if parent_sigma > 0:
                    result.p10 = result.p50 - z * parent_sigma
                    result.p90 = result.p50 + z * parent_sigma
                if hasattr(result, "bounds_method"):
                    result.bounds_method = BOUNDS_METHOD_MINT_FULL
                tagged += 1
        projected_periods += 1

    cross: dict[str, Any] = {"rollup_updated": 0}
    if cross_dimensions:
        cross = reconcile_cross_dimensional(
            db,
            version_id,
            dimensions=cross_dimensions,
            method=BOUNDS_METHOD_MINT_FULL,
        )

    db.flush()
    return {
        "recalculated": n_recalc,
        "bounds_method": BOUNDS_METHOD_MINT_FULL,
        "bounds_tagged": tagged,
        "periods_projected": projected_periods,
        "n_leaves": len(leaf_ids),
        "n_nodes": len(node_ids),
        "correlation_source": "actuals_shrinkage" if dataset_id else "category_block",
        "cross_dimensional": cross,
    }


def reconcile_cross_dimensional(
    db: Session,
    version_id: str,
    *,
    dimensions: Sequence[str] = DEFAULT_CROSS_DIMS,
    method: str = BOUNDS_METHOD_MINT_FULL,
) -> dict[str, Any]:
    """Reconcile dimension-null rollup line items from dimensioned siblings.

    A rollup shares name+category with dimensioned siblings and is null on all
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
    use_full = method in (BOUNDS_METHOD_MINT_FULL, BOUNDS_METHOD_MINT, BOUNDS_METHOD_MINT_CROSS)
    use_linear = method == BOUNDS_METHOD_LINEAR

    for _key, members in groups.items():
        rollups = [li for li in members if is_rollup(li)]
        children = [li for li in members if is_dimensioned(li) and not li.is_calculated]
        if not rollups or len(children) < 2:
            continue
        groups_touched += 1

        # Correlation among cross-dim siblings (same name → high prior)
        child_ids = [c.id for c in children]
        n_c = len(child_ids)
        R = np.eye(n_c) * (1.0 - _CATEGORY_RHO) + np.full((n_c, n_c), _CATEGORY_RHO)
        np.fill_diagonal(R, 1.0)

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
                if use_linear:
                    p10 = float(sum(r.p10 or 0.0 for r in child_rows))
                    p90 = float(sum(r.p90 or 0.0 for r in child_rows))
                    bounds_tag = BOUNDS_METHOD_LINEAR
                elif use_full:
                    sig = np.array(
                        [
                            max((r.p90 - r.p10) / (2 * z), 0.0)
                            if r.p10 is not None and r.p90 is not None
                            else abs(float(r.p50 or 0.0)) * 0.05
                            for r in child_rows
                        ],
                        dtype=float,
                    )
                    # Align R to child_rows order
                    idx = [child_ids.index(r.line_item_id) for r in child_rows]
                    R_sub = R[np.ix_(idx, idx)]
                    W = np.diag(sig) @ R_sub @ np.diag(sig)
                    ones = np.ones(len(child_rows))
                    var = float(ones @ W @ ones)
                    parent_sigma = float(np.sqrt(max(var, 0.0)))
                    p10 = p50 - z * parent_sigma
                    p90 = p50 + z * parent_sigma
                    bounds_tag = BOUNDS_METHOD_MINT_CROSS
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
        "bounds_method": (
            BOUNDS_METHOD_LINEAR if use_linear else BOUNDS_METHOD_MINT_CROSS
        ),
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
