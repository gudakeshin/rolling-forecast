"""Split-conformal calibration of forecast prediction intervals.

Every engine in ``app/domain/engines`` produces P10/P90 from its own
distributional assumption -- SARIMAX state-space variance, ETS ``pred_int``,
an OLS t-interval, or ``z * sigma * sqrt(h)``. Those are *model-assumed* bands:
they are only right when the model is right.

``IForecastModel.evaluate_cv`` already backtests every candidate with
rolling-origin CV and, since this module landed, returns the signed
out-of-sample errors bucketed by horizon step (``fold_residuals``). Those
errors are a valid conformal calibration set for the selected model, so we can
replace the assumed band with one the model has actually earned.

Two properties matter for finance:

* **Per-horizon.** A one-month-ahead error and a twelve-month-ahead error are
  not drawn from the same distribution. Pooling them produces a band that is
  too wide near-term and too narrow far-term -- precisely backwards for an
  analyst deciding where to spend review time.
* **Bias-aware.** Conformal offsets are asymmetric, so a model that runs
  consistently low produces a band shifted upward rather than a symmetric band
  centred on a biased point. The bias is reported rather than hidden.

Nothing here assumes normality, and nothing here is fitted -- these are order
statistics of realized errors.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np

# Two-sided nominal level of the published band. The product speaks P10/P90
# everywhere (see ForecastLineResult), so alpha is 0.20 rather than 0.05.
DEFAULT_ALPHA = 0.20


@dataclass
class CalibrationBands:
    """Per-horizon additive offsets applied to a point forecast.

    ``lower_offsets[h]`` is normally negative and ``upper_offsets[h]`` positive,
    but neither is forced -- a biased model legitimately yields two offsets of
    the same sign, and that is the signal we want to surface.
    """

    lower_offsets: dict[int, float]
    upper_offsets: dict[int, float]
    method: str
    n_residuals: int
    per_horizon_counts: dict[int, int] = field(default_factory=dict)
    median_bias: dict[int, float] = field(default_factory=dict)
    # Multiplicative widening applied after realized-vintage feedback (1.0 = none).
    scale: float = 1.0
    # Horizon steps whose upper/lower order statistic saturated at the sample
    # extreme -- the band is a lower bound on the true one, not a guarantee.
    saturated_horizons: list[int] = field(default_factory=list)

    @property
    def max_horizon(self) -> int:
        return max(self.lower_offsets) if self.lower_offsets else 0

    def to_metadata(self) -> dict[str, Any]:
        """Compact, JSON-safe summary for ModelMetadata / the explain drawer."""
        return {
            "method": self.method,
            "n_residuals": self.n_residuals,
            "scale": round(self.scale, 4),
            "per_horizon_counts": dict(self.per_horizon_counts),
            "median_bias": {h: round(v, 6) for h, v in self.median_bias.items()},
            "saturated_horizons": list(self.saturated_horizons),
        }


def _conformal_offset(sorted_residuals: list[float], beta: float) -> tuple[float, bool]:
    """Order statistic for one-sided conformal level ``beta``.

    Returns ``(offset, saturated)``. Split conformal takes the
    ``ceil((n + 1) * beta)``-th smallest residual; when that index exceeds ``n``
    the finite sample cannot certify the level, so we take the sample extreme
    and report saturation rather than silently pretending to the nominal level.
    """
    n = len(sorted_residuals)
    if n == 0:
        return 0.0, True
    k = math.ceil((n + 1) * beta)
    if k < 1:
        return sorted_residuals[0], True
    if k > n:
        return sorted_residuals[-1], True
    return sorted_residuals[k - 1], False


def conformal_bands(
    residuals_by_h: dict[int, list[float]],
    *,
    alpha: float = DEFAULT_ALPHA,
    min_per_horizon: int = 5,
    min_total: int = 9,
) -> CalibrationBands | None:
    """Build per-horizon conformal offsets, or ``None`` if there is too little data.

    ``None`` is a deliberate outcome, not a failure: the caller keeps the
    engine's analytic interval and records that it did. Fabricating a band from
    three residuals would be worse than the parametric one it replaced.
    """
    clean: dict[int, list[float]] = {}
    for h, values in (residuals_by_h or {}).items():
        finite = [float(v) for v in values if v is not None and np.isfinite(v)]
        if finite:
            clean[int(h)] = sorted(finite)

    total = sum(len(v) for v in clean.values())
    if total < min_total:
        return None

    pooled = sorted(v for values in clean.values() for v in values)
    # Count-weighted mean horizon of the pool -- the reference step that pooled
    # offsets are quoted at before sqrt-horizon rescaling.
    weighted = sum(h * len(v) for h, v in clean.items())
    h_ref = max(1.0, weighted / total)

    pooled_lo, pooled_lo_sat = _conformal_offset(pooled, alpha / 2.0)
    pooled_hi, pooled_hi_sat = _conformal_offset(pooled, 1.0 - alpha / 2.0)

    lower: dict[int, float] = {}
    upper: dict[int, float] = {}
    counts: dict[int, int] = {}
    bias: dict[int, float] = {}
    saturated: list[int] = []
    used_pooled = False

    for h in sorted(clean):
        values = clean[h]
        counts[h] = len(values)
        bias[h] = float(np.median(values))
        if len(values) >= min_per_horizon:
            lo, lo_sat = _conformal_offset(values, alpha / 2.0)
            hi, hi_sat = _conformal_offset(values, 1.0 - alpha / 2.0)
            if lo_sat or hi_sat:
                saturated.append(h)
        else:
            # Too thin at this step. Borrow the pooled set and grow it with the
            # random-walk rule (error sd ~ sqrt(h)) relative to the pool's own
            # mean horizon. Assumption is explicit and recorded in the method.
            used_pooled = True
            grow = math.sqrt(max(h, 1) / h_ref)
            lo, hi = pooled_lo * grow, pooled_hi * grow
            if pooled_lo_sat or pooled_hi_sat:
                saturated.append(h)
        lower[h] = float(min(lo, hi))
        upper[h] = float(max(lo, hi))

    if not lower:
        return None

    method = "conformal_pooled" if used_pooled else "conformal_per_horizon"
    return CalibrationBands(
        lower_offsets=lower,
        upper_offsets=upper,
        method=method,
        n_residuals=total,
        per_horizon_counts=counts,
        median_bias=bias,
        saturated_horizons=sorted(set(saturated)),
    )


def rescale_for_realized_coverage(
    bands: CalibrationBands,
    realized_coverage: float | None,
    *,
    target: float = 1.0 - DEFAULT_ALPHA,
    max_scale: float = 2.0,
    min_scale: float = 0.5,
) -> CalibrationBands:
    """Widen or tighten using coverage actually observed against realized actuals.

    CV residuals describe how the model behaved on history it was fitted near;
    ``ForecastAccuracyRecord.within_p10_p90`` describes how published forecasts
    behaved against actuals that had not happened yet. The second is the honest
    measure, so once enough cycles have closed it corrects the first.

    The correction is deliberately gentle and clamped: coverage estimated from a
    few dozen realized points is noisy, and a band that thrashes cycle to cycle
    destroys the trust this whole mechanism exists to build.
    """
    if realized_coverage is None or not np.isfinite(realized_coverage):
        return bands
    coverage = float(min(max(realized_coverage, 0.01), 0.999))
    # Gaussian-equivalent width ratio needed to move `coverage` onto `target`.
    # Using the normal quantile only as a smooth, monotone interpolator -- the
    # offsets themselves stay non-parametric order statistics.
    from scipy.stats import norm

    have = float(norm.ppf(0.5 + coverage / 2.0))
    want = float(norm.ppf(0.5 + target / 2.0))
    if have <= 1e-9:
        return bands
    scale = float(min(max(want / have, min_scale), max_scale))
    if abs(scale - 1.0) < 1e-6:
        return bands

    return CalibrationBands(
        lower_offsets={h: v * scale for h, v in bands.lower_offsets.items()},
        upper_offsets={h: v * scale for h, v in bands.upper_offsets.items()},
        method=f"{bands.method}+realized",
        n_residuals=bands.n_residuals,
        per_horizon_counts=dict(bands.per_horizon_counts),
        median_bias=dict(bands.median_bias),
        scale=scale,
        saturated_horizons=list(bands.saturated_horizons),
    )


def apply_bands(
    point_forecast: np.ndarray,
    bands: CalibrationBands,
    *,
    non_negative: bool = False,
) -> tuple[np.ndarray, np.ndarray, dict[str, Any]]:
    """Return ``(lower, upper, diagnostics)`` for a point forecast.

    Beyond the last calibrated horizon the final offset is extended with the
    sqrt-horizon rule rather than held flat -- a 24-month band that stops
    widening at month 12 reads as false precision.

    The band is clamped to contain the point forecast. Downstream code treats
    p10 <= p50 <= p90 as an invariant (MinT backs leaf sigma out of the p10/p90
    gap in ``reconciliation._leaf_sigma_from_intervals``, and the pipeline
    floors p10 at zero for non-negative lines), so a bias large enough to push
    the whole band off the point is reported in ``diagnostics`` instead of
    being allowed to break that invariant.
    """
    point = np.asarray(point_forecast, dtype=float)
    h_max = bands.max_horizon
    if h_max == 0:
        return point.copy(), point.copy(), {"calibrated_steps": 0}

    lo_last = bands.lower_offsets[h_max]
    hi_last = bands.upper_offsets[h_max]

    lower = np.empty_like(point)
    upper = np.empty_like(point)
    extrapolated = 0
    for i in range(len(point)):
        h = i + 1
        if h in bands.lower_offsets:
            lo, hi = bands.lower_offsets[h], bands.upper_offsets[h]
        else:
            grow = math.sqrt(h / h_max)
            lo, hi = lo_last * grow, hi_last * grow
            extrapolated += 1
        lower[i] = point[i] + lo
        upper[i] = point[i] + hi

    bias_clamped = int(np.sum((lower > point) | (upper < point)))
    lower = np.minimum(lower, point)
    upper = np.maximum(upper, point)
    if non_negative:
        lower = np.maximum(lower, 0.0)

    diagnostics = {
        "calibrated_steps": int(len(point) - extrapolated),
        "extrapolated_steps": extrapolated,
        "bias_clamped_steps": bias_clamped,
        **bands.to_metadata(),
    }
    return lower, upper, diagnostics
