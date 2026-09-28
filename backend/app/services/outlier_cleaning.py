"""Pre-fit outlier cleaning — STL residual MAD + leave-one-out z-score.

Winsorizes (never deletes) spikes; seasonal peaks are preserved because
detection runs on the STL remainder, not the raw series.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# Default MAD z-score threshold (~3.5 ≈ strong outlier under normality)
DEFAULT_MAD_Z = 3.5
MIN_POINTS_STL = 24  # ≥2 seasonal cycles for monthly period=12
SEASONAL_PERIOD = 12


@dataclass
class OutlierCleaningResult:
    cleaned: pd.Series
    cleaned_periods: list[str] = field(default_factory=list)
    outlier_indices: list[int] = field(default_factory=list)
    method: str = "none"
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def n_cleaned(self) -> int:
        return len(self.outlier_indices)


def _loo_mad_z_scores(remainder: np.ndarray) -> np.ndarray:
    """Per-point leave-one-out MAD z-scores (median/MAD exclude the point)."""
    n = len(remainder)
    z = np.zeros(n, dtype=float)
    for i in range(n):
        others = np.delete(remainder, i)
        med = float(np.median(others))
        mad = float(np.median(np.abs(others - med)))
        scale = 1.4826 * mad + 1e-12
        z[i] = (remainder[i] - med) / scale
    return z


def detect_outliers_stl_mad(
    values: pd.Series,
    dates: pd.DatetimeIndex,
    *,
    mad_z: float = DEFAULT_MAD_Z,
    seasonal_period: int = SEASONAL_PERIOD,
) -> OutlierCleaningResult:
    """Flag outliers on STL remainder (or demeaned series if STL unavailable)."""
    n = len(values)
    arr = values.values.astype(float)
    period_labels = [d.strftime("%Y-%m") for d in dates]

    if n < 6:
        return OutlierCleaningResult(cleaned=values.copy(), method="skipped_short")

    # Pass 1: LOO MAD on demeaned series — reliable for raw spikes
    z_raw = _loo_mad_z_scores(arr - np.mean(arr))
    spike_idx = sorted(int(i) for i in np.where(np.abs(z_raw) >= mad_z)[0])

    # Pass 2: STL remainder LOO MAD only when the series looks clean enough
    # that seasonal structure won't be contaminated by extreme spikes.
    outlier_idx = list(spike_idx)
    method = "mad"
    max_z = float(np.max(np.abs(z_raw))) if len(z_raw) else 0.0

    if not spike_idx and n >= MIN_POINTS_STL:
        try:
            from statsmodels.tsa.seasonal import STL

            stl = STL(arr, period=seasonal_period, robust=True)
            res = stl.fit()
            remainder = np.asarray(res.resid, dtype=float)
            z = _loo_mad_z_scores(remainder)
            outlier_idx = [int(i) for i in np.where(np.abs(z) >= mad_z)[0]]
            method = "stl_mad"
            max_z = float(np.max(np.abs(z))) if len(z) else 0.0
        except Exception as e:
            logger.debug("STL failed, using demeaned MAD: %s", e)

    return OutlierCleaningResult(
        cleaned=values.copy(),
        cleaned_periods=[period_labels[i] for i in outlier_idx],
        outlier_indices=outlier_idx,
        method=method,
        details={"mad_z_threshold": mad_z, "max_abs_z": max_z},
    )


def winsorize_outliers(
    values: pd.Series,
    dates: pd.DatetimeIndex,
    *,
    mad_z: float = DEFAULT_MAD_Z,
    seasonal_period: int = SEASONAL_PERIOD,
) -> OutlierCleaningResult:
    """Detect outliers then winsorize at the MAD fence (never delete points)."""
    detected = detect_outliers_stl_mad(
        values, dates, mad_z=mad_z, seasonal_period=seasonal_period
    )
    if not detected.outlier_indices:
        return detected

    arr = values.values.astype(float).copy()
    # Fence from non-outlier points
    mask = np.ones(len(arr), dtype=bool)
    mask[detected.outlier_indices] = False
    base = arr[mask] if mask.any() else arr
    med = float(np.median(base))
    mad = float(np.median(np.abs(base - med)))
    scale = 1.4826 * mad + 1e-12
    lo, hi = med - mad_z * scale, med + mad_z * scale

    for i in detected.outlier_indices:
        arr[i] = float(np.clip(arr[i], lo, hi))

    cleaned = pd.Series(arr, index=values.index)
    return OutlierCleaningResult(
        cleaned=cleaned,
        cleaned_periods=detected.cleaned_periods,
        outlier_indices=detected.outlier_indices,
        method=f"{detected.method}_winsorize",
        details={**detected.details, "fence": [lo, hi]},
    )


def clean_series_for_fit(
    values: pd.Series,
    dates: pd.DatetimeIndex,
    *,
    enabled: bool = True,
    mad_z: float = DEFAULT_MAD_Z,
) -> OutlierCleaningResult:
    """Entry point used by generate_baseline when outlier cleaning is enabled."""
    if not enabled:
        return OutlierCleaningResult(cleaned=values.copy(), method="disabled")
    return winsorize_outliers(values, dates, mad_z=mad_z)
