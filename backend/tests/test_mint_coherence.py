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
