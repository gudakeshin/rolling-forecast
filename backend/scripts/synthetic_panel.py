"""Synthetic FP&A panel with deliberately varied series shapes, for stress-
testing model-roster changes beyond the seed datasets (see backtest_roster.py).

Shapes (10 series each, seeded): linear trend, multiplicative seasonality,
additive seasonality, level shift (structural break), intermittent demand,
random walk, short history (14 months), and heavy-tailed noise.

    ./venv/bin/python scripts/synthetic_panel.py > /tmp/synthetic.csv
"""

from __future__ import annotations

import sys

import numpy as np

N = 36


def series(shape: str, rng: np.random.Generator, n: int = N) -> np.ndarray:
    t = np.arange(n)
    base = rng.uniform(500, 5000)
    if shape == "trend":
        return base + rng.uniform(5, 40) * t + rng.normal(0, base * 0.03, n)
    if shape == "mult_seasonal":
        return base * (1.02 ** t) * (1 + 0.3 * np.sin(2 * np.pi * t / 12)) * (1 + rng.normal(0, 0.02, n))
    if shape == "add_seasonal":
        return base + 0.2 * base * np.sin(2 * np.pi * t / 12 + rng.uniform(0, 6)) + rng.normal(0, base * 0.03, n)
    if shape == "level_shift":
        shift = np.where(t >= rng.integers(18, 26), base * 0.4, 0.0)
        return base + shift + rng.normal(0, base * 0.03, n)
    if shape == "intermittent":
        return np.where(rng.random(n) < 0.35, rng.uniform(20, 200, n), 0.0)
    if shape == "random_walk":
        return np.abs(base + np.cumsum(rng.normal(0, base * 0.04, n)))
    if shape == "short":
        return base + rng.uniform(5, 30) * np.arange(14) + rng.normal(0, base * 0.03, 14)
    if shape == "heavy_tail":
        return base + rng.uniform(5, 20) * t + rng.standard_t(2, n) * base * 0.05
    raise ValueError(shape)


SHAPES = ["trend", "mult_seasonal", "add_seasonal", "level_shift", "intermittent", "random_walk", "short", "heavy_tail"]


def main() -> None:
    rng = np.random.default_rng(2026)
    out = sys.stdout
    out.write("account_code,account_name,category,business_unit,period,value,currency\n")
    for shape in SHAPES:
        for i in range(10):
            values = series(shape, rng)
            start = N - len(values)
            for k, v in enumerate(values):
                m = start + k
                period = f"{2023 + m // 12}-{m % 12 + 1:02d}"
                out.write(f"SYN-{shape}-{i},{shape} {i},Revenue,Synthetic,{period},{max(float(v), 0.0):.2f},USD\n")


if __name__ == "__main__":
    main()
