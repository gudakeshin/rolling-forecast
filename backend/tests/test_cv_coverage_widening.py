"""Model bands are widened by how often they covered in rolling-origin CV."""

from __future__ import annotations

import numpy as np
import pytest

from app.services.interval_calibration import widen_for_cv_coverage

POINT = np.array([100.0, 100.0, 100.0])
LOWER = np.array([90.0, 85.0, 80.0])
UPPER = np.array([110.0, 115.0, 120.0])


def test_under_covering_band_widens_around_the_point():
    out = widen_for_cv_coverage(POINT, LOWER, UPPER, 0.60, 18)
    assert out is not None
    lower, upper, meta = out
    # Gaussian width ratio z(0.90) / z(0.80) ~= 1.52.
    assert meta["scale"] == pytest.approx(1.2816 / 0.8416, rel=1e-3)
    assert np.allclose(POINT - lower, (POINT - LOWER) * meta["scale"], rtol=1e-3)
    assert np.allclose(upper - POINT, (UPPER - POINT) * meta["scale"], rtol=1e-3)


def test_never_narrows_and_needs_evidence():
    assert widen_for_cv_coverage(POINT, LOWER, UPPER, 0.95, 18) is None  # over-covered
    assert widen_for_cv_coverage(POINT, LOWER, UPPER, 0.40, 6) is None  # too few CV points
    assert widen_for_cv_coverage(POINT, LOWER, UPPER, None, 18) is None


def test_scale_is_capped_and_floor_respected():
    lower, upper, meta = widen_for_cv_coverage(POINT, LOWER, UPPER, 0.0, 18, non_negative=True)
    assert meta["scale"] == 2.0
    big = widen_for_cv_coverage(POINT, np.array([10.0, 10.0, 10.0]), UPPER, 0.2, 18, non_negative=True)
    assert big is not None and np.all(big[0] >= 0.0)
