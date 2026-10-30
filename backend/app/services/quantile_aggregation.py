"""Aggregate per-line P10/P50/P90 into a total without assuming perfect correlation.

Quantiles are not additive: summing P10s across lines and months gives the P10
of the total only if every line moves in lockstep. This module approximates the
total's distribution instead:

- P50 of the total is the (signed) sum of P50s.
- Each cell's spread is turned into a σ via the nominal 80% band,
  σ = (P90 − P10) / (2·z₈₀). The lower and upper halves are kept separate
  (σ_lo = (P50 − P10)/z₈₀, σ_hi = (P90 − P50)/z₈₀) so a skewed band stays skewed;
  for a symmetric band both equal the formula above.
- Cells are combined through a separable correlation, corr(line i, period t;
  line j, period u) = R_line[i, j] · R_period[t, u], so the variance of the total
  is Σ a·(R_line a R_period) with a the signed σ grid.
- Total P10 / P90 = P50 ∓ z₈₀·√variance.

Signs let one aggregate mix revenue and cost on a net-income basis: a cost line
has sign −1, so its *upper* half (a cost overrun) feeds net-income downside.

Pure numpy; callers supply the correlation matrices.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

# Nominal 80% central interval z-score (matches services/reconciliation.py)
Z80 = 1.2815515655446004

# Default month-to-month correlation of one line's forecast errors across the
# horizon, used as AR(1): ρ^|t−u|. Multi-step errors share the same level and
# trend misspecification, so they are strongly but not perfectly correlated.
DEFAULT_PERIOD_RHO = 0.7


@dataclass(frozen=True)
class AggregatedQuantiles:
    p10: float
    p50: float
    p90: float

    @property
    def downside(self) -> float:
        return self.p50 - self.p10

    @property
    def upside(self) -> float:
        return self.p90 - self.p50


def ar1_correlation(n: int, rho: float = DEFAULT_PERIOD_RHO) -> np.ndarray:
    """n×n AR(1) correlation, ρ^|i−j|."""
    idx = np.arange(n)
    return float(rho) ** np.abs(idx[:, None] - idx[None, :])


def aggregate_quantiles(
    p10: np.ndarray,
    p50: np.ndarray,
    p90: np.ndarray,
    *,
    signs: np.ndarray | None = None,
    line_corr: np.ndarray | None = None,
    period_corr: np.ndarray | None = None,
    z: float = Z80,
) -> AggregatedQuantiles:
    """Quantiles of Σ sᵢ·Yᵢₜ for a lines × periods grid of per-cell quantiles.

    ``p10``/``p50``/``p90`` are (n_lines, n_periods) arrays; a 1-D array is
    treated as one period. NaN marks a missing cell: a missing P50 contributes
    nothing, a missing P10/P90 contributes no spread on that side.
    ``signs`` (n_lines,) defaults to all +1. ``line_corr`` defaults to identity
    (independent lines); ``period_corr`` defaults to AR(1) with
    ``DEFAULT_PERIOD_RHO``.

    The returned P10/P90 are the lower/upper quantiles of the signed total.
    """
    P10, P50, P90 = (np.asarray(a, dtype=float) for a in (p10, p50, p90))
    if P50.ndim == 1:
        P10, P50, P90 = P10[:, None], P50[:, None], P90[:, None]
    n, t = P50.shape
    if n == 0 or t == 0:
        return AggregatedQuantiles(0.0, 0.0, 0.0)

    s = np.ones(n) if signs is None else np.asarray(signs, dtype=float).reshape(n)
    R_line = np.eye(n) if line_corr is None else np.asarray(line_corr, dtype=float)
    R_time = ar1_correlation(t) if period_corr is None else np.asarray(period_corr, dtype=float)

    mid = np.nan_to_num(P50, nan=0.0)
    total = float(s @ mid.sum(axis=1))

    # Half-band σ per cell; crossed or missing bounds give zero spread.
    has_mid = ~np.isnan(P50)
    sig_lo = np.where(has_mid, np.nan_to_num(np.clip(mid - P10, 0.0, None) / z, nan=0.0), 0.0)
    sig_hi = np.where(has_mid, np.nan_to_num(np.clip(P90 - mid, 0.0, None) / z, nan=0.0), 0.0)

    # The total's lower tail comes from the lower half of positive-sign lines
    # and the upper half of negative-sign lines, and vice versa.
    pos = (s >= 0)[:, None]
    down = np.where(pos, sig_lo, sig_hi) * s[:, None]
    up = np.where(pos, sig_hi, sig_lo) * s[:, None]

    def _sd(a: np.ndarray) -> float:
        var = float(np.sum(a * (R_line @ a @ R_time)))
        return float(np.sqrt(max(var, 0.0)))

    return AggregatedQuantiles(
        p10=total - z * _sd(down),
        p50=total,
        p90=total + z * _sd(up),
    )
