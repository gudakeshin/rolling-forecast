"""The backtest's paired comparison resamples series, not (series, origin) rows."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from backtest_roster import DEFAULT, paired_change  # noqa: E402


def _frame(base: list[float], arm: list[float], origins: int = 3) -> pd.DataFrame:
    rows = []
    for i, (b, a) in enumerate(zip(base, arm)):
        for o in range(origins):
            rows.append({"config": DEFAULT, "series": f"s{i}", "origin": o, "mase": b})
            rows.append({"config": "arm", "series": f"s{i}", "origin": o, "mase": a})
    return pd.DataFrame(rows)


def test_consistent_improvement_has_a_ci_below_zero():
    rng = np.random.default_rng(0)
    base = list(rng.uniform(0.5, 1.5, 40))
    arm = [b * 0.9 for b in base]
    point, lo, hi, n = paired_change(_frame(base, arm), "arm", "mase")
    assert n == 40
    assert point == pytest_approx(-0.10)
    assert lo < hi < 0


def test_noise_straddles_zero_and_origins_do_not_add_evidence():
    rng = np.random.default_rng(1)
    base = list(rng.uniform(0.5, 1.5, 30))
    arm = [b * rng.choice([0.8, 1.2]) for b in base]
    _, lo1, hi1, _ = paired_change(_frame(base, arm, origins=1), "arm", "mase")
    _, lo5, hi5, n = paired_change(_frame(base, arm, origins=5), "arm", "mase")
    assert lo1 < 0 < hi1
    assert n == 30
    # Same series repeated across origins: the interval must not shrink.
    assert np.isclose(hi5 - lo5, hi1 - lo1)


def pytest_approx(x: float):
    import pytest

    return pytest.approx(x, abs=1e-9)
