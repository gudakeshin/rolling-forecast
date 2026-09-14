"""MinT coherence property tests — random hierarchies: children sum to parents."""

from __future__ import annotations

import networkx as nx
import numpy as np
import pytest

from app.services.reconciliation import build_summing_matrix, mint_project


def _random_hierarchy(rng: np.random.Generator, n_leaves: int = 4):
    """Build a random additive hierarchy: leaves + one total + optional mid parents."""
    G = nx.DiGraph()
    leaves = list(range(n_leaves))
    for lid in leaves:
        G.add_node(lid)

    # Mid parents that each sum a random subset of leaves
    mids = []
    next_id = n_leaves
    n_mids = max(1, n_leaves // 2)
    for _ in range(n_mids):
        mid = next_id
        next_id += 1
        mids.append(mid)
        G.add_node(mid)
        subset = rng.choice(leaves, size=rng.integers(1, n_leaves + 1), replace=False)
        for lid in subset:
            G.add_edge(lid, mid)  # source → dependent (child → parent)

    total = next_id
    G.add_node(total)
    for mid in mids:
        G.add_edge(mid, total)
    # Also attach any leaf not covered by a mid
    covered = {src for mid in mids for src, _ in G.in_edges(mid)}
    for lid in leaves:
        if lid not in covered:
            G.add_edge(lid, total)

    leaf_ids = leaves
    node_ids = leaves + mids + [total]
    return G, leaf_ids, node_ids


@pytest.mark.parametrize("seed", [0, 1, 2, 3, 4, 7, 11, 13])
def test_mint_projection_coherent_on_random_hierarchy(seed: int):
    rng = np.random.default_rng(seed)
    G, leaf_ids, node_ids = _random_hierarchy(rng, n_leaves=int(rng.integers(3, 6)))
    S = build_summing_matrix(G, leaf_ids, node_ids)
    n = len(node_ids)
    n_leaves = len(leaf_ids)

    # Coherent base: parents already = S @ leaves
    y_leaf = rng.uniform(10, 1000, size=n_leaves)
    y_hat = S @ y_leaf

    # Positive-definite W (diagonal leaf noise expanded)
    sigma = rng.uniform(1, 20, size=n_leaves)
    W_leaf = np.diag(sigma ** 2)
    W = S @ W_leaf @ S.T + np.eye(n) * 1e-6

    y_tilde = mint_project(y_hat, S, W)

    # Structural coherence: ỹ ≈ S @ ỹ_leaves
    leaf_idx = list(range(n_leaves))
    reconstructed = S @ y_tilde[leaf_idx]
    assert y_tilde == pytest.approx(reconstructed, rel=1e-6, abs=1e-4)


@pytest.mark.parametrize("seed", [21, 22, 23])
def test_mint_blends_incoherent_parent_then_restores_coherence(seed: int):
    """Incoherent parent base is projected back onto the summing space."""
    rng = np.random.default_rng(seed)
    # Simple: two leaves → one parent
    G = nx.DiGraph()
    G.add_edges_from([(0, 2), (1, 2)])
    leaf_ids = [0, 1]
    node_ids = [0, 1, 2]
    S = build_summing_matrix(G, leaf_ids, node_ids)

    y_hat = np.array([100.0, 50.0, 200.0])  # parent should be 150
    W = np.diag([4.0, 4.0, 9.0])
    y_tilde = mint_project(y_hat, S, W)
    assert y_tilde[2] == pytest.approx(y_tilde[0] + y_tilde[1], rel=1e-6, abs=1e-6)


# --------------------------------------------------------- scale invariance ---
#
# MinT's inputs are not scale-free: W holds variances (currency squared) and
# G = S'W^-1 S holds inverse variances. Any ridge added for numerical stability
# must therefore be relative to the matrix it stabilises. A constant ridge on G
# behaves completely differently on a line forecasting 900k than on one
# forecasting 9 — and since MinT is the default reconciliation method, that
# reaches every published number.


@pytest.mark.parametrize("sigma", [1.0, 1e2, 1e3, 3.5e4, 1e5, 1e7])
def test_flat_hierarchy_projection_is_the_identity_at_any_scale(sigma):
    """With no parents, S = I and the projection has nothing to reconcile.

    Regression: a constant 1e-10 ridge on G, whose diagonal is ~1/sigma^2, was
    11% of that diagonal at sigma=35k and 50% at sigma=100k, shrinking every
    forecast toward zero. A flat chart of accounts is the cleanest witness --
    any movement at all is pure artefact.
    """
    G = nx.DiGraph()
    leaves = [1, 2, 3]
    for i in leaves:
        G.add_node(i)

    S = build_summing_matrix(G, leaves, leaves)
    assert np.allclose(S, np.eye(3))

    y_hat = np.array([875376.0, 500000.0, 250000.0])
    W = np.diag([sigma**2] * 3)
    y_tilde = mint_project(y_hat, S, W)

    rel = np.max(np.abs(y_tilde - y_hat) / np.abs(y_hat))
    assert rel < 1e-6, f"projection moved a coherent forecast by {rel:.1%} at sigma={sigma}"


def test_projection_is_equivariant_under_rescaling():
    """Scaling every forecast and sigma by k must scale the result by k.

    This is the property a scale-dependent ridge breaks, and it holds for the
    real hierarchy rather than only the degenerate flat one.
    """
    rng = np.random.default_rng(4)
    G, leaf_ids, node_ids = _random_hierarchy(rng, n_leaves=4)
    S = build_summing_matrix(G, leaf_ids, node_ids)

    n = len(node_ids)
    y_hat = rng.uniform(50, 150, n)
    sigma = rng.uniform(5, 15, n)
    W = np.diag(sigma**2)

    base = mint_project(y_hat, S, W)
    k = 10_000.0
    scaled = mint_project(y_hat * k, S, np.diag((sigma * k) ** 2))

    assert np.allclose(scaled, base * k, rtol=1e-6), "MinT is not scale-equivariant"
