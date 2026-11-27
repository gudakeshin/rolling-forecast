"""MinT's W from forecast-error correlations, Schäfer–Strimmer shrinkage."""

from __future__ import annotations

import numpy as np
import pytest

from app.models.actuals import ActualsDataset, ActualsRecord
from app.models.line_item import LineItem
from app.services.reconciliation import estimate_leaf_correlation, shrink_correlation


def test_shared_trend_is_not_correlation(db_session):
    """Two lines that both grow, with independent surprises: their levels
    correlate ~1, their forecast errors do not."""
    rng = np.random.default_rng(0)
    a = LineItem(account_code="MC-A", name="A", category="Revenue")
    b = LineItem(account_code="MC-B", name="B", category="Opex")
    db_session.add_all([a, b])
    ds = ActualsDataset(source_type="t", source_name="mc", file_hash="mc", row_count=0,
                        period_start="2022-01", period_end="2025-12", periods_count=48)
    db_session.add(ds)
    db_session.flush()
    t = np.arange(48)
    for i in t:
        p = f"{2022 + i // 12}-{i % 12 + 1:02d}"
        db_session.add(ActualsRecord(dataset_id=ds.id, line_item_id=a.id, period=p,
                                     value=1000 + 20 * i + rng.normal(0, 30)))
        db_session.add(ActualsRecord(dataset_id=ds.id, line_item_id=b.id, period=p,
                                     value=500 + 10 * i + rng.normal(0, 15)))
    db_session.commit()

    levels = np.corrcoef(1000 + 20 * t, 500 + 10 * t)[0, 1]
    assert levels > 0.99
    R = estimate_leaf_correlation(
        db_session, [a.id, b.id], dataset_id=ds.id, categories={a.id: "Revenue", b.id: "Opex"}
    )
    assert abs(R[0, 1]) < 0.3


def test_shrinkage_intensity_follows_the_evidence():
    rng = np.random.default_rng(1)
    cov = np.array([[1.0, 0.8], [0.8, 1.0]])
    target = np.eye(2)

    long = rng.multivariate_normal([0, 0], cov, size=400)
    R_long, lam_long = shrink_correlation(long, target)
    assert lam_long < 0.05 and abs(R_long[0, 1] - 0.8) < 0.08

    short = rng.multivariate_normal([0, 0], np.eye(2), size=6)
    _, lam_short = shrink_correlation(short, target)
    assert lam_short > lam_long

    noise = rng.normal(size=(200, 3))
    R_noise, _ = shrink_correlation(noise, np.eye(3))
    assert np.all(np.abs(R_noise[~np.eye(3, dtype=bool)]) < 0.2)


def test_degenerate_inputs():
    target = np.eye(2)
    assert shrink_correlation(np.zeros((3, 2)), target)[0] is None  # too few periods
    flat = np.column_stack([np.zeros(10), np.arange(10.0)])
    R, _ = shrink_correlation(flat, target)
    assert R is not None and R[0, 1] == 0.0  # no-variance column stays at the target


def _two_lines_and_one_without_actuals(db_session):
    a = LineItem(account_code="MX-A", name="A", category="Revenue")
    b = LineItem(account_code="MX-B", name="B", category="Revenue")
    new = LineItem(account_code="MX-NEW", name="New line", category="Revenue")
    db_session.add_all([a, b, new])
    ds = ActualsDataset(source_type="t", source_name="mx", file_hash="mx", row_count=0,
                        period_start="2024-01", period_end="2025-12", periods_count=24)
    db_session.add(ds)
    db_session.flush()
    rng = np.random.default_rng(7)
    shared = rng.normal(0, 10, 24)
    for i in range(24):
        p = f"{2024 + i // 12}-{i % 12 + 1:02d}"
        db_session.add(ActualsRecord(dataset_id=ds.id, line_item_id=a.id, period=p, value=1000 + i + shared[i]))
        db_session.add(ActualsRecord(dataset_id=ds.id, line_item_id=b.id, period=p, value=500 + i + shared[i]))
    db_session.commit()
    return a, b, new, ds


def test_a_line_without_actuals_keeps_the_prior(db_session):
    """A leaf with no actuals in the dataset (a new line, or one missing from
    the upload) used to raise KeyError, which broke what-ifs, overrides and
    branches for the whole company."""
    a, b, new, ds = _two_lines_and_one_without_actuals(db_session)
    ids = [a.id, new.id, b.id]
    cats = {a.id: "Revenue", b.id: "Revenue", new.id: "Revenue"}
    R = estimate_leaf_correlation(db_session, ids, dataset_id=ds.id, categories=cats)
    assert R.shape == (3, 3)
    assert np.allclose(np.diag(R), 1.0)
    # The two lines with history get their estimated (strong, shared-shock) correlation.
    assert R[0, 2] > 0.5
    # The new line has no evidence, so it keeps the same-category prior.
    assert R[0, 1] == pytest.approx(0.35, abs=0.05) and R[1, 2] == pytest.approx(0.35, abs=0.05)
    assert np.all(np.linalg.eigvalsh(R) > 0)


def test_only_one_line_with_actuals_falls_back_to_the_prior(db_session):
    a, _b, new, ds = _two_lines_and_one_without_actuals(db_session)
    other = LineItem(account_code="MX-OTHER", name="Other", category="Opex")
    db_session.add(other)
    db_session.commit()
    R = estimate_leaf_correlation(
        db_session, [a.id, new.id, other.id], dataset_id=ds.id,
        categories={a.id: "Revenue", new.id: "Revenue", other.id: "Opex"},
    )
    assert R.shape == (3, 3) and R[0, 2] == 0.0  # different categories, no evidence
