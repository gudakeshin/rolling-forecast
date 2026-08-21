"""Model Registry -- pluggable forecast models and auto-selection.

Selection uses either legacy MAPE or multi-metric (MASE → pinball → complexity)
controlled by ``settings.selection_metric``. Benchmark models (naive /
seasonal_naive) are gated by ``settings.enable_benchmark_models``.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from app.domain.engines.base_model import ForecastOutput, IForecastModel, as_exog_model
from app.domain.engines.linear import LinearTrendModel
from app.domain.engines.ets import ETSModel
from app.domain.engines.arima import ARIMAModel
from app.domain.engines.prophet_model import ProphetModel

logger = logging.getLogger(__name__)

_CHEAP_COST = frozenset({"trivial", "cheap"})
_EXPENSIVE_COST = frozenset({"moderate", "expensive"})


@dataclass
class ModelComparisonResult:
    """Result of comparing one model against a time series."""

    model_name: str
    mape: float
    evaluation_time_ms: float
    eligible: bool
    error: str | None = None
    fold_mapes: list[float] = field(default_factory=list)
    n_folds: int = 0
    mase: float = float("inf")
    smape: float = float("inf")
    pinball: float = float("inf")
    pinball_10: float = float("inf")
    pinball_90: float = float("inf")
    coverage_80: float | None = None
    fold_mases: list[float] = field(default_factory=list)
    complexity_rank: int = 50
    cost_class: str = "cheap"
    is_benchmark: bool = False
    skipped_budget: bool = False
    mase_scale_method: str | None = None


@dataclass
class ModelSelectionResult:
    """Full result of auto-selecting the best model, including all comparisons."""

    best_model: str | None
    best_mape: float
    comparisons: list[ModelComparisonResult] = field(default_factory=list)
    data_points: int = 0
    selection_method: str = "rolling_origin_cv"
    selection_rule: str = "mape"
    best_mase: float = float("inf")
    best_pinball: float = float("inf")
    n_downgraded: int = 0

    def to_dict(self) -> dict[str, Any]:
        """Serialize for storage and display."""
        return {
            "best_model": self.best_model,
            "best_mape": round(self.best_mape, 2) if self.best_mape != float("inf") else None,
            "best_mase": round(self.best_mase, 4) if self.best_mase != float("inf") else None,
            "best_pinball": round(self.best_pinball, 4) if self.best_pinball != float("inf") else None,
            "data_points": self.data_points,
            "selection_method": self.selection_method,
            "selection_rule": self.selection_rule,
            "n_downgraded": self.n_downgraded,
            "comparisons": [
                {
                    "model": c.model_name,
                    "model_name": c.model_name,
                    "mape": round(c.mape, 2) if c.mape != float("inf") else None,
                    "mase": round(c.mase, 4) if c.mase != float("inf") else None,
                    "smape": round(c.smape, 2) if c.smape != float("inf") else None,
                    "pinball": round(c.pinball, 4) if c.pinball != float("inf") else None,
                    "coverage_80": round(c.coverage_80, 3) if c.coverage_80 is not None else None,
                    "eligible": c.eligible,
                    "evaluation_time_ms": round(c.evaluation_time_ms, 1),
                    "error": c.error,
                    "fold_mapes": [round(m, 2) if m != float("inf") else None for m in c.fold_mapes],
                    "fold_mases": [round(m, 4) if m != float("inf") else None for m in c.fold_mases],
                    "n_folds": c.n_folds,
                    "complexity_rank": c.complexity_rank,
                    "cost_class": c.cost_class,
                    "is_benchmark": c.is_benchmark,
                    "skipped_budget": c.skipped_budget,
                    "selected": c.model_name == self.best_model,
                }
                for c in self.comparisons
            ],
        }


def effective_selection_rule() -> str:
    from app.config import settings

    metric = (settings.selection_metric or "mape").lower()
    return "mase_pinball_complexity" if metric == "multi" else "mape"


def _series_cv(series: pd.Series) -> float:
    y = np.asarray(series.values, dtype=float)
    mean_abs = float(np.mean(np.abs(y))) + 1e-10
    return float(np.std(y) / mean_abs)


def _admit_expensive(
    series: pd.Series,
    cheap_best_mase: float,
    *,
    is_material: bool | None,
) -> bool:
    """Admit moderate/expensive models only where they can pay for themselves."""
    if is_material is True:
        return True
    if cheap_best_mase != float("inf") and cheap_best_mase > 1.0:
        return True
    if _series_cv(series) >= 0.30:
        return True
    if is_material is False:
        return False
    # Unknown materiality: admit on CV or poor cheap screen only (already checked)
    return False


def _selection_band(fold_scores: list[float], n_folds: int) -> float:
    """1-SE band; floor at 5% of mean when folds ≤ 3 (under-powered SE)."""
    finite = [s for s in fold_scores if s != float("inf") and not np.isnan(s)]
    if not finite:
        return 0.0
    mean = float(np.mean(finite))
    if len(finite) <= 1:
        se = 0.0
    else:
        se = float(np.std(finite, ddof=1) / np.sqrt(len(finite)))
    band = se
    if n_folds <= 3:
        band = max(se, 0.05 * abs(mean))
    return band


def _pick_best(
    comparisons: list[ModelComparisonResult],
    *,
    rule: str,
) -> tuple[str | None, float, float, float]:
    """Return (best_model, best_mape, best_mase, best_pinball)."""
    eligible = [
        c for c in comparisons
        if c.eligible and not c.skipped_budget and c.error is None
    ]
    if rule == "mape":
        scored = [c for c in eligible if c.mape != float("inf")]
        if not scored:
            return None, float("inf"), float("inf"), float("inf")
        best_mape = min(c.mape for c in scored)
        # Prefer simpler on exact MAPE ties; benchmarks lose pure ties
        def mape_key(c: ModelComparisonResult) -> tuple:
            return (
                abs(c.mape - best_mape) > 1e-12,  # False (0) = at best
                c.mape,
                c.is_benchmark,  # False before True on ties
                c.complexity_rank,
            )

        # First filter to near-best, then tie-break
        band = _selection_band(
            [m for c in scored for m in (c.fold_mapes or [c.mape])],
            max((c.n_folds for c in scored), default=0),
        )
        within = [c for c in scored if c.mape <= best_mape + band]
        within.sort(key=lambda c: (c.mape, c.is_benchmark, c.complexity_rank))
        winner = within[0]
        return winner.model_name, winner.mape, winner.mase, winner.pinball

    # multi: MASE → pinball → complexity; benchmarks lose pure ties
    scored = [c for c in eligible if c.mase != float("inf")]
    if not scored:
        # Fall back to MAPE pool if no MASE
        return _pick_best(comparisons, rule="mape")

    best_mase = min(c.mase for c in scored)
    # Use fold MASEs from the best model's peers for SE; approximate with all fold_mases
    all_folds = [m for c in scored for m in (c.fold_mases or []) if m != float("inf")]
    n_folds = max((c.n_folds for c in scored), default=0)
    band = _selection_band(all_folds or [best_mase], n_folds)
    within = [c for c in scored if c.mase <= best_mase + band]
    within.sort(
        key=lambda c: (
            c.mase,
            c.pinball if c.pinball != float("inf") else 1e18,
            c.is_benchmark,  # benchmarks win ties only when strictly better on MASE/pinball
            c.complexity_rank,
        )
    )
    winner = within[0]
    return winner.model_name, winner.mape, winner.mase, winner.pinball


class ModelRegistry:
    """Registry of available forecast models with auto-selection."""

    def __init__(self):
        self._models: dict[str, IForecastModel] = {}
        self._register_defaults()

    def _register_defaults(self) -> None:
        from app.config import settings

        self.register(LinearTrendModel())
        self.register(ETSModel())
        from app.domain.engines.theta import ThetaForecastModel
        from app.domain.engines.croston import CrostonModel, TSBModel

        self.register(ThetaForecastModel())
        self.register(CrostonModel())
        self.register(TSBModel())
        self.register(ARIMAModel())
        self.register(ProphetModel())
        # Edge-case models (always available — used by the pipeline for EC1/EC2)
        from app.domain.engines.zero import ZeroModel
        from app.domain.engines.mean import MeanModel

        self.register(ZeroModel())
        self.register(MeanModel())
        if settings.enable_benchmark_models:
            from app.domain.engines.naive import NaiveModel
            from app.domain.engines.seasonal_naive import SeasonalNaiveModel

            self.register(NaiveModel())
            self.register(SeasonalNaiveModel())

    def register(self, model: IForecastModel) -> None:
        self._models[model.name] = model
        logger.info("Registered forecast model: %s", model.name)

    def get(self, name: str) -> IForecastModel | None:
        return self._models.get(name)

    def list_models(self) -> list[str]:
        return list(self._models.keys())

    def list_models_by_complexity(self) -> list[str]:
        return sorted(
            self._models.keys(),
            key=lambda n: self._models[n].capabilities.complexity_rank,
        )

    def models_for_cost_classes(self, classes: set[str] | frozenset[str]) -> list[str]:
        return [
            n for n, m in self._models.items()
            if m.capabilities.cost_class in classes
        ]

    def _evaluate_one(
        self,
        model_name: str,
        series: pd.Series,
        dates: pd.DatetimeIndex,
        *,
        test_size: int,
        raise_folds: bool = False,
    ) -> ModelComparisonResult:
        model = self._models.get(model_name)
        if model is None:
            return ModelComparisonResult(
                model_name=model_name,
                mape=float("inf"),
                evaluation_time_ms=0,
                eligible=False,
                error=f"Model '{model_name}' not found in registry",
            )

        caps = model.capabilities
        if len(series) < model.min_data_points:
            return ModelComparisonResult(
                model_name=model_name,
                mape=float("inf"),
                evaluation_time_ms=0,
                eligible=False,
                error=f"Need {model.min_data_points} data points, have {len(series)}",
                complexity_rank=caps.complexity_rank,
                cost_class=caps.cost_class,
                is_benchmark=caps.is_benchmark,
            )

        n_folds = 3
        fold_horizon = max(3, test_size // 2) if test_size else 3
        if (
            caps.max_folds_short_series is not None
            and len(series) < caps.short_series_threshold
        ):
            n_folds = caps.max_folds_short_series

        # Prefer more folds when history allows (cheap fix for under-powered 1-SE)
        max_folds_by_data = max(1, (len(series) - model.min_data_points) // fold_horizon)
        if max_folds_by_data >= 5 and (caps.max_folds_short_series is None or len(series) >= caps.short_series_threshold):
            n_folds = min(5, max_folds_by_data)

        t0 = time.time()
        try:
            cv = model.evaluate_cv(series, dates, n_folds=n_folds, fold_horizon=fold_horizon)
            elapsed_ms = (time.time() - t0) * 1000
            mape = cv["mean_mape"]
            mase = cv["mean_mase"]
            return ModelComparisonResult(
                model_name=model_name,
                mape=mape,
                mase=mase,
                smape=cv.get("mean_smape", float("inf")),
                pinball=cv.get("mean_pinball", float("inf")),
                pinball_10=cv.get("mean_pinball_10", float("inf")),
                pinball_90=cv.get("mean_pinball_90", float("inf")),
                coverage_80=cv.get("coverage_80"),
                evaluation_time_ms=elapsed_ms,
                eligible=True,
                error=None if (mape != float("inf") or mase != float("inf")) else "Evaluation returned inf",
                fold_mapes=cv.get("fold_mapes") or [],
                fold_mases=cv.get("fold_mases") or [],
                n_folds=cv.get("n_folds_used") or 0,
                complexity_rank=caps.complexity_rank,
                cost_class=caps.cost_class,
                is_benchmark=caps.is_benchmark,
                mase_scale_method=cv.get("mase_scale_method"),
            )
        except Exception as e:
            elapsed_ms = (time.time() - t0) * 1000
            if raise_folds:
                raise
            return ModelComparisonResult(
                model_name=model_name,
                mape=float("inf"),
                evaluation_time_ms=elapsed_ms,
                eligible=True,
                error=str(e)[:200],
                complexity_rank=caps.complexity_rank,
                cost_class=caps.cost_class,
                is_benchmark=caps.is_benchmark,
            )

    def compare_models(
        self,
        series: pd.Series,
        dates: pd.DatetimeIndex,
        test_size: int = 6,
        random_seed: int = 42,
        models_to_test: list[str] | None = None,
        *,
        selection_rule: str | None = None,
        wall_clock_budget_seconds: float | None = None,
        is_material: bool | None = None,
        two_stage: bool = True,
    ) -> ModelSelectionResult:
        """Walk-forward CV comparison with optional two-stage cost screening."""
        from app.config import settings

        np.random.seed(random_seed)
        rule = selection_rule or effective_selection_rule()
        budget = (
            wall_clock_budget_seconds
            if wall_clock_budget_seconds is not None
            else float(settings.selection_wall_clock_seconds)
        )
        deadline = time.time() + budget if budget and budget > 0 else None

        requested = models_to_test or [
            n for n, m in self._models.items() if m.capabilities.auto_selectable
        ]
        # Drop unknown names early
        candidates = [n for n in requested if n in self._models]
        for n in requested:
            if n not in self._models:
                logger.warning("Unknown model in models_to_test: %s", n)

        comparisons: list[ModelComparisonResult] = []
        n_downgraded = 0

        if two_stage and models_to_test is None:
            stage1 = [n for n in candidates if self._models[n].capabilities.cost_class in _CHEAP_COST]
            stage2 = [n for n in candidates if self._models[n].capabilities.cost_class in _EXPENSIVE_COST]
        else:
            stage1 = list(candidates)
            stage2 = []

        for model_name in stage1:
            if deadline and time.time() > deadline:
                comparisons.append(ModelComparisonResult(
                    model_name=model_name,
                    mape=float("inf"),
                    evaluation_time_ms=0,
                    eligible=True,
                    skipped_budget=True,
                    error="Skipped: wall-clock budget exhausted",
                    complexity_rank=self._models[model_name].capabilities.complexity_rank,
                    cost_class=self._models[model_name].capabilities.cost_class,
                    is_benchmark=self._models[model_name].capabilities.is_benchmark,
                ))
                n_downgraded += 1
                continue
            comparisons.append(self._evaluate_one(model_name, series, dates, test_size=test_size))

        cheap_mases = [c.mase for c in comparisons if c.mase != float("inf")]
        cheap_best_mase = min(cheap_mases) if cheap_mases else float("inf")
        admit = _admit_expensive(series, cheap_best_mase, is_material=is_material)

        for model_name in stage2:
            if not admit:
                caps = self._models[model_name].capabilities
                comparisons.append(ModelComparisonResult(
                    model_name=model_name,
                    mape=float("inf"),
                    evaluation_time_ms=0,
                    eligible=True,
                    skipped_budget=True,
                    error="Skipped: not admitted by two-stage screen",
                    complexity_rank=caps.complexity_rank,
                    cost_class=caps.cost_class,
                    is_benchmark=caps.is_benchmark,
                ))
                n_downgraded += 1
                continue
            if deadline and time.time() > deadline:
                caps = self._models[model_name].capabilities
                comparisons.append(ModelComparisonResult(
                    model_name=model_name,
                    mape=float("inf"),
                    evaluation_time_ms=0,
                    eligible=True,
                    skipped_budget=True,
                    error="Skipped: wall-clock budget exhausted",
                    complexity_rank=caps.complexity_rank,
                    cost_class=caps.cost_class,
                    is_benchmark=caps.is_benchmark,
                ))
                n_downgraded += 1
                continue
            comparisons.append(self._evaluate_one(model_name, series, dates, test_size=test_size))

        # Also record unknown requested models
        for n in requested:
            if n not in self._models and not any(c.model_name == n for c in comparisons):
                comparisons.append(ModelComparisonResult(
                    model_name=n,
                    mape=float("inf"),
                    evaluation_time_ms=0,
                    eligible=False,
                    error=f"Model '{n}' not found in registry",
                ))

        best_model, best_mape, best_mase, best_pinball = _pick_best(comparisons, rule=rule)

        logger.info(
            "Model comparison: best=%s (MAPE=%s, MASE=%s, rule=%s), tested %s models on %s points",
            best_model,
            f"{best_mape:.2f}%" if best_mape != float("inf") else "inf",
            f"{best_mase:.3f}" if best_mase != float("inf") else "inf",
            rule,
            len(comparisons),
            len(series),
        )

        return ModelSelectionResult(
            best_model=best_model,
            best_mape=best_mape,
            best_mase=best_mase,
            best_pinball=best_pinball,
            comparisons=comparisons,
            data_points=len(series),
            selection_method="rolling_origin_cv",
            selection_rule=rule,
            n_downgraded=n_downgraded,
        )

    def auto_select(
        self,
        series: pd.Series,
        dates: pd.DatetimeIndex,
        test_size: int = 6,
        random_seed: int = 42,
        models_to_test: list[str] | None = None,
        **kwargs: Any,
    ) -> tuple[str | None, float, ModelSelectionResult]:
        result = self.compare_models(
            series, dates, test_size, random_seed, models_to_test, **kwargs
        )
        return result.best_model, result.best_mape, result

    def fit_and_predict(
        self,
        model_name: str,
        series: pd.Series,
        dates: pd.DatetimeIndex,
        horizon: int,
        random_seed: int = 42,
        *,
        exog: pd.DataFrame | np.ndarray | None = None,
        exog_future: pd.DataFrame | np.ndarray | None = None,
    ) -> ForecastOutput:
        np.random.seed(random_seed)
        model = self.get(model_name)
        if model is None:
            raise ValueError(f"Model '{model_name}' not found in registry")
        if model.capabilities.supports_exog and exog is not None:
            exog_model = as_exog_model(model)
            params = exog_model.fit(series, dates, exog=exog)
            return exog_model.predict(params, horizon, dates[-1], exog_future=exog_future)
        params = model.fit(series, dates)
        return model.predict(params, horizon, dates[-1])


_model_registry: ModelRegistry | None = None


def get_model_registry() -> ModelRegistry:
    """Get the global model registry singleton."""
    global _model_registry
    if _model_registry is None:
        _model_registry = ModelRegistry()
    return _model_registry


def reset_model_registry() -> None:
    """Clear singleton (tests / settings flips)."""
    global _model_registry
    _model_registry = None
