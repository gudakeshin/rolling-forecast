"""MinT's W from forecast-error correlations, Schäfer–Strimmer shrinkage."""

from __future__ import annotations

import numpy as np

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
