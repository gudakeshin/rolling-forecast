"""Croston (SBA) and TSB intermittent demand models."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from app.domain.engines.base_model import (
    ForecastOutput,
    IForecastModel,
    ModelCapabilities,
    make_period_labels,
)
from app.domain.engines.intermittent import ADI_INTERMITTENT, demand_classification


def _croston_once(y: np.ndarray, alpha: float) -> tuple[float, float, float, float, float]:
    """One Croston-SBA recursion for a fixed alpha.

    Returns (forecast, demand_level, interval_level, residual_std, in_sample_mse), where
    the MSE is the one-step-ahead squared error of (z, p) against each new (demand,
    interval) pair, used to grid-search alpha in `_croston_sba` below.
    """
    y = np.asarray(y, dtype=float)
    demands: list[float] = []
    intervals: list[float] = []
    gap = 0
    for v in y:
        gap += 1
        if v != 0:
            demands.append(abs(float(v)))
            intervals.append(float(gap))
            gap = 0
    if not demands:
        return 0.0, 0.0, float(len(y) or 1), 0.0, float("inf")

    z = demands[0]
    p = intervals[0]
    sse = 0.0
    for d, iv in zip(demands[1:], intervals[1:]):
        sse += (d - z) ** 2 + (iv - p) ** 2
        z = alpha * d + (1 - alpha) * z
        p = alpha * iv + (1 - alpha) * p

    # SBA bias correction
    forecast = (1.0 - alpha / 2.0) * (z / max(p, 1e-9))
    # Rough residual scale from non-zero demands
    resid_std = float(np.std(demands, ddof=1)) if len(demands) > 1 else float(abs(z) * 0.2)
    n_updates = len(demands) - 1
    mse = sse / n_updates if n_updates > 0 else float("inf")
    return float(forecast), float(z), float(p), resid_std, mse


_CROSTON_ALPHA_GRID: tuple[float, ...] = (0.05, 0.1, 0.15, 0.2, 0.3, 0.4)


def _croston_sba(y: np.ndarray) -> tuple[float, float, float, float, float]:
    """Croston with Syntetos–Boylan approximation; alpha chosen by in-sample
    one-step-ahead SSE over a small grid instead of a hardcoded 0.1.

    Returns (forecast, demand_level, interval_level, residual_std, alpha).
    """
    best_alpha = 0.1
    best = _croston_once(y, best_alpha)
    best_mse = best[4]
    for alpha in _CROSTON_ALPHA_GRID:
        if alpha == best_alpha:
            continue
        candidate = _croston_once(y, alpha)
        if np.isfinite(candidate[4]) and candidate[4] < best_mse:
            best, best_mse, best_alpha = candidate, candidate[4], alpha

    forecast, z, p, resid_std, _ = best
    return forecast, z, p, resid_std, best_alpha


def _tsb_once(y: np.ndarray, alpha: float, beta: float) -> tuple[float, float, float, float, float]:
    """One TSB recursion for a fixed (alpha, beta).

    Returns (forecast, size_level, prob_level, residual_std, in_sample_mse), where the
    MSE is the one-step-ahead squared error of z*p against each observation, used to
    grid-search (alpha, beta) in `_tsb` below.
    """
    y = np.asarray(y, dtype=float)
    if len(y) == 0:
        return 0.0, 0.0, 0.0, 0.0, float("inf")

    # Initialize from first non-zero if present
    nz = y[y != 0]
    z = float(abs(nz[0])) if len(nz) else 0.0
    p = 1.0 if y[0] != 0 else 0.0
    sizes: list[float] = []
    sse = 0.0

    for v in y:
        sse += (float(v) - z * p) ** 2
        occur = 1.0 if v != 0 else 0.0
        p = beta * occur + (1 - beta) * p
        if v != 0:
            z = alpha * abs(float(v)) + (1 - alpha) * z
            sizes.append(abs(float(v)))

    forecast = z * p
    resid_std = float(np.std(sizes, ddof=1)) if len(sizes) > 1 else float(abs(z) * 0.2)
    mse = sse / len(y) if len(y) else float("inf")
    return float(forecast), float(z), float(p), resid_std, mse


_TSB_ALPHA_GRID: tuple[float, ...] = (0.05, 0.1, 0.15, 0.2, 0.3)
_TSB_BETA_GRID: tuple[float, ...] = (0.05, 0.1, 0.15, 0.2, 0.3)


def _tsb(y: np.ndarray) -> tuple[float, float, float, float, float, float]:
    """Teunter–Syntetos–Babai: smooth demand size and occurrence probability, with
    (alpha, beta) chosen by in-sample one-step-ahead SSE over a small grid instead
    of a hardcoded (0.1, 0.1).

    Returns (forecast, size_level, prob_level, residual_std, alpha, beta).
    """
    best_alpha, best_beta = 0.1, 0.1
    best = _tsb_once(y, best_alpha, best_beta)
    best_mse = best[4]
    for alpha in _TSB_ALPHA_GRID:
        for beta in _TSB_BETA_GRID:
            if alpha == best_alpha and beta == best_beta:
                continue
            candidate = _tsb_once(y, alpha, beta)
            if np.isfinite(candidate[4]) and candidate[4] < best_mse:
                best, best_mse, best_alpha, best_beta = candidate, candidate[4], alpha, beta

    forecast, z, p, resid_std, _ = best
    return forecast, z, p, resid_std, best_alpha, best_beta


class CrostonModel(IForecastModel):
    """Croston + SBA for intermittent demand (ADI ≥ 1.32)."""

    @property
    def name(self) -> str:
        return "croston"

    @property
    def min_data_points(self) -> int:
        return 8

    @property
    def capabilities(self) -> ModelCapabilities:
        return ModelCapabilities(
            complexity_rank=12,
            min_data_points=8,
            base_confidence=45.0,
            cost_class="cheap",
            display_label="Croston (SBA)",
            handles_intermittent=True,
        )

    def fit(self, series: pd.Series, dates: pd.DatetimeIndex) -> dict[str, Any]:
        y = series.values.astype(float)
        cls = demand_classification(y)
        if not cls["intermittent"]:
            raise ValueError(
                f"Croston requires intermittent demand (ADI≥{ADI_INTERMITTENT}), "
                f"got ADI={cls['adi']:.2f}"
            )
        forecast, z, p, resid_std, alpha = _croston_sba(y)
        return {
            "alpha": alpha,
            "demand_level": z,
            "interval_level": p,
            "point": forecast,
            "residual_std": resid_std,
            "adi": cls["adi"],
            "cv2": cls["cv2"],
            "pattern": cls["pattern"],
            "n_points": len(y),
            "in_sample_mape": None,
            "r_squared": 0.0,
        }

    def predict(
        self,
        params: dict[str, Any],
        horizon: int,
        last_date: pd.Timestamp,
        confidence_level: float = 0.80,
    ) -> ForecastOutput:
        point = float(params["point"])
        sd = float(params.get("residual_std") or 0.0)
        from scipy import stats

        z = float(stats.norm.ppf(0.5 + confidence_level / 2.0))
        fc = np.full(horizon, point, dtype=float)
        # Intermittent uncertainty grows slowly with horizon
        half = z * sd * np.sqrt(np.arange(1, horizon + 1) / max(params.get("interval_level", 1.0), 1.0))
        return ForecastOutput(
            point_forecast=fc,
            lower_bound=np.maximum(fc - half, 0.0),
            upper_bound=fc + half,
            periods=make_period_labels(last_date, horizon),
            model_type=self.name,
            parameters={k: v for k, v in params.items() if not str(k).startswith("_")},
            fit_metrics={"r_squared": 0.0},
            diagnostics={
                "intermittent": True,
                "adi": params.get("adi"),
                "cv2": params.get("cv2"),
                "pattern": params.get("pattern"),
            },
        )


class TSBModel(IForecastModel):
    """Teunter–Syntetos–Babai — intermittent with obsolescence handling."""

    @property
    def name(self) -> str:
        return "tsb"

    @property
    def min_data_points(self) -> int:
        return 8

    @property
    def capabilities(self) -> ModelCapabilities:
        return ModelCapabilities(
            complexity_rank=13,
            min_data_points=8,
            base_confidence=45.0,
            cost_class="cheap",
            display_label="TSB",
            handles_intermittent=True,
        )

    def fit(self, series: pd.Series, dates: pd.DatetimeIndex) -> dict[str, Any]:
        y = series.values.astype(float)
        cls = demand_classification(y)
        if not cls["intermittent"]:
            raise ValueError(
                f"TSB requires intermittent demand (ADI≥{ADI_INTERMITTENT}), "
                f"got ADI={cls['adi']:.2f}"
            )
        forecast, z, p, resid_std, alpha, beta = _tsb(y)
        return {
            "alpha": alpha,
            "beta": beta,
            "size_level": z,
            "prob_level": p,
            "point": forecast,
            "residual_std": resid_std,
            "adi": cls["adi"],
            "cv2": cls["cv2"],
            "pattern": cls["pattern"],
            "n_points": len(y),
            "in_sample_mape": None,
            "r_squared": 0.0,
        }

    def predict(
        self,
        params: dict[str, Any],
        horizon: int,
        last_date: pd.Timestamp,
        confidence_level: float = 0.80,
    ) -> ForecastOutput:
        point = float(params["point"])
        sd = float(params.get("residual_std") or 0.0)
        from scipy import stats

        z = float(stats.norm.ppf(0.5 + confidence_level / 2.0))
        fc = np.full(horizon, point, dtype=float)
        half = z * sd * np.sqrt(np.arange(1, horizon + 1, dtype=float))
        return ForecastOutput(
            point_forecast=fc,
            lower_bound=np.maximum(fc - half, 0.0),
            upper_bound=fc + half,
            periods=make_period_labels(last_date, horizon),
            model_type=self.name,
            parameters={k: v for k, v in params.items() if not str(k).startswith("_")},
            fit_metrics={"r_squared": 0.0},
            diagnostics={
                "intermittent": True,
                "adi": params.get("adi"),
                "cv2": params.get("cv2"),
                "pattern": params.get("pattern"),
                "prob_level": params.get("prob_level"),
            },
        )
