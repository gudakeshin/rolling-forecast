"""Global cross-series panel forecasting model (Phase 2.1).

Every other engine in this package (arima.py, ets.py, linear.py, ...) fits one
series in isolation. This one is different: a HistGradientBoostingRegressor
per quantile (P10/P50/P90) is fit ONCE across every line item in a
version-generation run, so short/new/correlated lines can borrow statistical
strength from the rest of the panel — the M5-competition-winning recipe,
scoped to an FP&A panel.

Direct multi-horizon: the horizon step ``h`` is itself a training feature, so
one set of three models covers every step of the forecast instead of a
recursive rollout or one booster per step.

Pure model code only — no DB/Session imports, matching how arima.py/linear.py
are layered. DB-aware panel construction (loading actuals, building driver
columns, running the walk-forward panel CV, fitting the production models)
lives in app.services.panel_forecast.build_global_panel_context.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor

from app.domain.engines.base_model import (
    ForecastOutput,
    IForecastModel,
    ModelCapabilities,
    make_period_labels,
    seasonal_period_length,
)

QUANTILES: tuple[float, ...] = (0.10, 0.50, 0.90)
LAGS: tuple[int, ...] = (1, 2, 3, 12)
ROLLING_WINDOWS: tuple[int, ...] = (3, 6, 12)


@dataclass
class GlobalPanelContext:
    """Shared state for one version-generation run's global panel model.

    Built once by panel_forecast.build_global_panel_context and threaded
    explicitly through LineForecastContext.panel to every line's registry
    calls. Deliberately a plain local value, never cached on a model instance
    or a module-level global: two version-generation jobs can run
    concurrently in the same worker process, and each must get its own panel.
    """

    feature_columns: list[str]
    # alpha -> fitted model, trained once on the FULL history (no holdout).
    # Distinct from, and fit after, the CV-fold models below.
    production_models: dict[float, HistGradientBoostingRegressor]
    # line_item_id -> feature rows for the real future horizon, one row per
    # step, already ordered h=1..horizon. predict() needs no DB access.
    predict_features_by_line: dict[int, pd.DataFrame]
    # line_item_id -> a dict shaped exactly like IForecastModel.evaluate_cv's
    # return value, from one shared panel-wide walk-forward CV.
    per_line_cv_results: dict[int, dict[str, Any]]
    fitted_at: str
    n_training_rows: int = 0
    n_lines: int = 0


def lag_rolling_features(values: np.ndarray, origin_idx: int) -> dict[str, float]:
    """Lag/rolling features computed strictly from history up to origin_idx (inclusive).

    ``values`` is a line's own effective (cleaned) series as a plain array;
    ``origin_idx`` is the index of the last known observation the forecast is
    made from. NaN lags (not enough history yet) are legitimate — tree models
    handle missing values natively, unlike linear models.
    """
    hist = values[: origin_idx + 1]
    feats: dict[str, float] = {}
    for lag in LAGS:
        feats[f"lag_{lag}"] = float(hist[-lag]) if len(hist) >= lag else float("nan")
    for window in ROLLING_WINDOWS:
        tail = hist[-window:] if len(hist) else hist
        feats[f"roll_mean_{window}"] = float(np.mean(tail)) if len(tail) else float("nan")
        feats[f"roll_std_{window}"] = float(np.std(tail)) if len(tail) > 1 else 0.0
    return feats


def fiscal_features(period: str) -> dict[str, float]:
    """Position-in-year features parsed directly from a period label.

    Avoids threading a FiscalCalendarConfig through feature building: works
    for both "YYYY-MM" (month) and "FYyyyy-Pnn" (period number) labels.
    Cyclical sin/cos encoding avoids a false ordinal distance between period
    12 and period 1 (December and January are seasonally adjacent).
    """
    m = seasonal_period_length()
    upper = period.upper()
    if upper.startswith("FY") and "-P" in upper:
        pos = int(upper.split("-P")[-1])
    else:
        pos = int(period.split("-")[-1])
    angle = 2.0 * np.pi * (pos - 1) / max(m, 1)
    return {
        "period_position": float(pos),
        "period_sin": float(np.sin(angle)),
        "period_cos": float(np.cos(angle)),
    }


def horizon_feature(h: int) -> dict[str, float]:
    return {"h": float(h)}


class GlobalGBMModel(IForecastModel):
    """M5-style global panel model — fit once across all line items.

    Cannot fit/predict from a bare ``(series, dates)`` call: it needs a
    ``GlobalPanelContext`` (see ``SupportsPanelModel`` in base_model.py) built
    once per version-generation run by
    ``app.services.panel_forecast.build_global_panel_context``. Calling the
    plain ``fit()``/``predict()`` without ``panel``/``line_item_id`` raises —
    the base ABC's ``evaluate_cv`` loop catches that per-fold and marks the
    fold ineligible rather than crashing a run, so an accidental bare
    invocation (e.g. a stray ``models_to_test=["global_gbm"]``) degrades
    gracefully instead of taking the whole generation down.
    """

    @property
    def name(self) -> str:
        return "global_gbm"

    @property
    def min_data_points(self) -> int:
        # Real eligibility is gated by presence in panel.per_line_cv_results,
        # not this property — keep it low so the registry's generic
        # length check never blocks a line the panel already scored.
        return 6

    @property
    def capabilities(self) -> ModelCapabilities:
        return ModelCapabilities(
            complexity_rank=45,  # above Prophet (40) — the most complex candidate
            min_data_points=6,
            base_confidence=55.0,
            is_benchmark=False,
            cost_class="expensive",
            display_label="Global panel (GBM)",
            auto_selectable=True,
            is_panel_model=True,
        )

    def fit(
        self,
        series: pd.Series,
        dates: pd.DatetimeIndex,
        *,
        panel: GlobalPanelContext | None = None,
        line_item_id: int | None = None,
    ) -> dict[str, Any]:
        if (
            panel is None
            or line_item_id is None
            or line_item_id not in panel.predict_features_by_line
        ):
            raise RuntimeError(
                "global_gbm.fit called without a populated GlobalPanelContext "
                f"for line_item_id={line_item_id!r}. This model cannot fit from "
                "a bare (series, dates) call — it must go through ModelRegistry "
                "with panel= and line_item_id= threaded from "
                "LineForecastContext.panel (see panel_forecast.py)."
            )
        return {"line_item_id": line_item_id, "fitted_at": panel.fitted_at}

    def predict(
        self,
        params: dict[str, Any],
        horizon: int,
        last_date: pd.Timestamp,
        confidence_level: float = 0.80,
        *,
        panel: GlobalPanelContext | None = None,
    ) -> ForecastOutput:
        if panel is None:
            raise RuntimeError("global_gbm.predict called without a GlobalPanelContext")
        line_item_id = params["line_item_id"]
        feat_df = panel.predict_features_by_line.get(line_item_id)
        if feat_df is None or feat_df.empty:
            raise RuntimeError(
                f"no precomputed future feature rows for line_item_id={line_item_id}"
            )

        X = feat_df[panel.feature_columns].iloc[:horizon]
        preds: dict[float, np.ndarray] = {}
        for q in QUANTILES:
            gbm = panel.production_models.get(q)
            if gbm is None:
                raise RuntimeError(f"no fitted production model for quantile {q}")
            preds[q] = np.asarray(gbm.predict(X), dtype=float)

        lower, point, upper = preds[0.10], preds[0.50], preds[0.90]
        # Independently-trained quantile regressors are not guaranteed
        # monotonic — sort per row so lower <= point <= upper always holds
        # before anything downstream (which assumes that ordering) sees it.
        stacked = np.vstack([lower, point, upper])
        stacked.sort(axis=0)
        lower, point, upper = stacked[0], stacked[1], stacked[2]

        n = len(point)
        if n < horizon:
            pad = horizon - n
            last_l = lower[-1] if n else 0.0
            last_p = point[-1] if n else 0.0
            last_u = upper[-1] if n else 0.0
            lower = np.concatenate([lower, np.full(pad, last_l)])
            point = np.concatenate([point, np.full(pad, last_p)])
            upper = np.concatenate([upper, np.full(pad, last_u)])

        periods = make_period_labels(last_date, horizon)
        return ForecastOutput(
            point_forecast=point[:horizon],
            lower_bound=lower[:horizon],
            upper_bound=upper[:horizon],
            periods=periods,
            model_type=self.name,
            parameters=params,
            fit_metrics={},
            diagnostics={
                "seasonality_detected": False,
                "pi_method": "quantile_gbm",
                "panel_fitted_at": panel.fitted_at,
                "panel_n_lines": panel.n_lines,
            },
        )

    def evaluate_cv_panel(
        self,
        line_item_id: int,
        series: pd.Series,
        dates: pd.DatetimeIndex,
        panel: GlobalPanelContext,
        n_folds: int = 3,
        fold_horizon: int = 3,
    ) -> dict[str, Any]:
        """Look up this line's precomputed panel-wide walk-forward CV result.

        The global model is refit once per fold across the WHOLE panel (see
        panel_forecast.build_global_panel_context), not per line — so this is
        a dict lookup, not a recomputation. ``n_folds``/``fold_horizon`` are
        accepted for interface parity with evaluate_cv but ignored: the panel
        CV uses one fixed fold structure shared across every line, which only
        ever makes this model's reported score MORE conservative than a
        line-specific fold count would justify, never less — documented v1
        simplification (see the Phase 2.1 plan doc).
        """
        result = panel.per_line_cv_results.get(line_item_id)
        if result is not None:
            return result
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
        }
