"""Chronos-2 foundation-model adapter (Phase C).

Chronos-2 runs in the self-hosted ``fm-service`` sidecar; this module is pure
model code (no DB, no HTTP) in the same layering as ``global_gbm.py``. The
one HTTP call per version-generation run — every line, every CV origin, in a
single batch — is made by ``app.services.foundation_forecast``, which caches
the returned P10/P50/P90 paths by ``(line_item_id, origin_period)`` in a
``FoundationPanelContext``. That context rides on ``GlobalPanelContext.foundation``
so it reaches the registry through the existing ``panel=`` plumbing; this
model's CV and predict are then dict lookups, like ``GlobalGBMModel``.

Unlike the GBM's fixed panel folds, the CV origins here are the exact
windows the registry scores every per-series model on
(``cv_fold_horizon`` + ``cv_n_folds`` + ``cv_fold_windows``), so Chronos-2's
fold scores pair with the other candidates' and it is judged by the same
paired-SE band. It wins only when it beats every simpler model by more than
that band (complexity rank 47 loses every tie).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

import numpy as np
import pandas as pd

from app.domain.engines.base_model import (
    ForecastOutput,
    IForecastModel,
    ModelCapabilities,
    make_period_labels,
)

if TYPE_CHECKING:
    from app.domain.engines.global_gbm import GlobalPanelContext

CHRONOS2_NAME = "chronos2"
# Minimum context for a CV fold / production forecast. Chronos-2 itself
# accepts shorter context, but below a year there is no seasonal cycle to
# condition on and the per-series CV has nothing to pair against.
CHRONOS2_MIN_POINTS = 12
# Shortest context for a production-only forecast of a new or short line
# (foundation_short_lines_enabled): no CV fold, sibling lines in the same
# group supply the rest of the context.
CHRONOS2_MIN_CONTEXT = 3


@dataclass(frozen=True)
class QuantileForecast:
    p10: np.ndarray
    p50: np.ndarray
    p90: np.ndarray


@dataclass
class FoundationPanelContext:
    """One run's Chronos-2 results, built once by foundation_forecast.

    ``status="unavailable"`` means the service was down, timed out, or the
    circuit was open: the model is then skipped on every line with
    ``skip_reason``, and the run carries on with the rest of the roster.
    """

    status: Literal["ok", "unavailable"]
    skip_reason: str | None = None
    # model_id, model_revision, service_version, library_version, fitted_at,
    # n_series, n_lines — stored on the version and on each line's metadata.
    provenance: dict[str, Any] = field(default_factory=dict)
    # (line_item_id, origin_period) -> forecast from that origin. Holds both
    # the CV-origin forecasts and the production one (origin = last actual).
    forecasts: dict[tuple[int, str], QuantileForecast] = field(default_factory=dict)
    # line_item_id -> last actual period, i.e. the production origin key.
    production_origin_by_line: dict[int, str] = field(default_factory=dict)
    # line_item_id -> evaluate_cv-shaped result scored from ``forecasts``.
    per_line_cv_results: dict[int, dict[str, Any]] = field(default_factory=dict)

    @classmethod
    def unavailable(cls, reason: str, provenance: dict[str, Any] | None = None) -> "FoundationPanelContext":
        return cls(status="unavailable", skip_reason=reason[:300], provenance=dict(provenance or {}))

    def metadata_note(self) -> dict[str, Any]:
        """Compact per-line provenance for ModelMetadata.parameters."""
        note: dict[str, Any] = {"model": CHRONOS2_NAME, "status": self.status}
        if self.skip_reason:
            note["skip_reason"] = self.skip_reason
        for k in ("model_id", "model_revision", "service_version"):
            if self.provenance.get(k):
                note[k] = self.provenance[k]
        return note


def _skipped_cv(reason: str) -> dict[str, Any]:
    return {
        "mean_mape": float("inf"),
        "mean_smape": float("inf"),
        "mean_mase": float("inf"),
        "mean_pinball_10": float("inf"),
        "mean_pinball_90": float("inf"),
        "mean_pinball": float("inf"),
        "coverage_80": None,
        "fold_residuals": {},
        "n": 0,
        "fold_mapes": [],
        "fold_mases": [],
        "n_folds_used": 0,
        "mase_scale_method": "ineligible",
        # Read by ModelRegistry._comparison_from_cv: the candidate becomes
        # ineligible with this as its error, instead of "Evaluation returned inf".
        "skip_reason": reason,
    }


def foundation_unavailable_reason(panel: "GlobalPanelContext | None", line_item_id: int | None) -> str | None:
    """Why Chronos-2 cannot be scored/used for this line, or None if it can."""
    if panel is None or getattr(panel, "foundation", None) is None:
        return "Skipped: foundation-model context not built this run"
    fm: FoundationPanelContext = panel.foundation
    if fm.status != "ok":
        return f"Skipped: foundation service unavailable ({fm.skip_reason})"
    if line_item_id is None or line_item_id not in fm.production_origin_by_line:
        return "Skipped: line not in the foundation-model batch"
    return None


class Chronos2Model(IForecastModel):
    """Chronos-2 via fm-service: zero-shot, covariate-aware, quantile-native.

    Implements the panel protocol (``SupportsPanelModel``) and accepts the
    exog keyword of ``SupportsExog``; covariates (past drivers and
    origin-restricted known-future drivers) are already inside the batched
    request, so a separate per-line exog fit never happens. A bare
    ``fit(series, dates)`` without a panel raises — the base ``evaluate_cv``
    loop then records failed folds instead of making one HTTP call per fold.
    """

    @property
    def name(self) -> str:
        return CHRONOS2_NAME

    @property
    def min_data_points(self) -> int:
        return CHRONOS2_MIN_POINTS

    @property
    def capabilities(self) -> ModelCapabilities:
        return ModelCapabilities(
            supports_exog=True,
            # 47: above Prophet (40) and the global GBM (45) — the most complex
            # candidate, so it loses every tie inside the paired-SE band.
            complexity_rank=47,
            min_data_points=CHRONOS2_MIN_POINTS,
            base_confidence=55.0,
            is_benchmark=False,
            cost_class="expensive",
            display_label="Chronos-2 (foundation)",
            auto_selectable=True,
            is_panel_model=True,
        )

    def fit(
        self,
        series: pd.Series,
        dates: pd.DatetimeIndex,
        *,
        panel: "GlobalPanelContext | None" = None,
        line_item_id: int | None = None,
        exog: pd.DataFrame | np.ndarray | None = None,
    ) -> dict[str, Any]:
        reason = foundation_unavailable_reason(panel, line_item_id)
        if reason is not None:
            raise RuntimeError(f"chronos2.fit: {reason}")
        assert panel is not None and panel.foundation is not None and line_item_id is not None
        fm: FoundationPanelContext = panel.foundation
        origin = fm.production_origin_by_line[line_item_id]
        return {
            "line_item_id": line_item_id,
            "origin_period": origin,
            **{k: fm.provenance.get(k) for k in ("model_id", "model_revision", "service_version")},
        }

    def predict(
        self,
        params: dict[str, Any],
        horizon: int,
        last_date: pd.Timestamp,
        confidence_level: float = 0.80,
        *,
        panel: "GlobalPanelContext | None" = None,
        exog_future: pd.DataFrame | np.ndarray | None = None,
    ) -> ForecastOutput:
        if panel is None or panel.foundation is None:
            raise RuntimeError("chronos2.predict called without a foundation context")
        fm: FoundationPanelContext = panel.foundation
        key = (int(params["line_item_id"]), str(params["origin_period"]))
        fc = fm.forecasts.get(key)
        if fc is None:
            raise RuntimeError(f"no cached Chronos-2 forecast for {key}")
        lower, point, upper = (np.asarray(a, dtype=float) for a in (fc.p10, fc.p50, fc.p90))
        n = len(point)
        if n == 0:
            raise RuntimeError(f"empty Chronos-2 forecast for {key}")
        if n < horizon:
            pad = horizon - n
            lower = np.concatenate([lower, np.full(pad, lower[-1])])
            point = np.concatenate([point, np.full(pad, point[-1])])
            upper = np.concatenate([upper, np.full(pad, upper[-1])])
        return ForecastOutput(
            point_forecast=point[:horizon],
            lower_bound=lower[:horizon],
            upper_bound=upper[:horizon],
            periods=make_period_labels(last_date, horizon),
            model_type=self.name,
            parameters=dict(params),
            fit_metrics={},
            diagnostics={
                "seasonality_detected": False,
                "pi_method": "foundation_quantiles",
                "foundation_fitted_at": fm.provenance.get("fitted_at"),
            },
        )

    def evaluate_cv_panel(
        self,
        line_item_id: int,
        series: pd.Series,
        dates: pd.DatetimeIndex,
        panel: "GlobalPanelContext",
        n_folds: int = 3,
        fold_horizon: int = 3,
    ) -> dict[str, Any]:
        """This line's CV, scored once per run from the cached origin forecasts."""
        reason = foundation_unavailable_reason(panel, line_item_id)
        if reason is not None:
            return _skipped_cv(reason)
        result = panel.foundation.per_line_cv_results.get(line_item_id)
        if result is None:
            return _skipped_cv("Skipped: too little history for a Chronos-2 CV fold")
        return result
