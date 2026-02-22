"""Model Registry -- manages pluggable forecast models and auto-selection.

Enhanced with full model comparison results, detailed logging, and
support for model subset selection.
"""

import logging
import time
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from app.domain.engines.base_model import IForecastModel, ForecastOutput
from app.domain.engines.linear import LinearTrendModel
from app.domain.engines.ets import ETSModel
from app.domain.engines.arima import ARIMAModel
from app.domain.engines.prophet_model import ProphetModel

logger = logging.getLogger(__name__)


@dataclass
class ModelComparisonResult:
    """Result of comparing one model against a time series."""
    model_name: str
    mape: float
    evaluation_time_ms: float
    eligible: bool  # Had enough data points
    error: str | None = None  # If evaluation failed


@dataclass
class ModelSelectionResult:
    """Full result of auto-selecting the best model, including all comparisons."""
    best_model: str
    best_mape: float
    comparisons: list[ModelComparisonResult] = field(default_factory=list)
    data_points: int = 0
    selection_method: str = "walk_forward_cv"

    def to_dict(self) -> dict[str, Any]:
        """Serialize for storage and display."""
        return {
            "best_model": self.best_model,
            "best_mape": round(self.best_mape, 2) if self.best_mape != float("inf") else None,
            "data_points": self.data_points,
            "selection_method": self.selection_method,
            "comparisons": [
                {
                    "model": c.model_name,
                    "mape": round(c.mape, 2) if c.mape != float("inf") else None,
                    "eligible": c.eligible,
                    "evaluation_time_ms": round(c.evaluation_time_ms, 1),
                    "error": c.error,
                    "selected": c.model_name == self.best_model,
                }
                for c in self.comparisons
            ],
        }


class ModelRegistry:
    """
    Registry of available forecast models with auto-selection capability.

    Models are pluggable -- each implements the IForecastModel interface.
    Auto-selection uses walk-forward cross-validation to pick the best model per line item.
    """

    def __init__(self):
        self._models: dict[str, IForecastModel] = {}
        self._register_defaults()

    def _register_defaults(self):
        """Register the default set of forecast models."""
        self.register(LinearTrendModel())
        self.register(ETSModel())
        self.register(ARIMAModel())
        self.register(ProphetModel())

    def register(self, model: IForecastModel) -> None:
        """Register a model in the registry."""
        self._models[model.name] = model
        logger.info(f"Registered forecast model: {model.name}")

    def get(self, name: str) -> IForecastModel | None:
        """Get a model by name."""
        return self._models.get(name)

    def list_models(self) -> list[str]:
        """List all registered model names."""
        return list(self._models.keys())

    def compare_models(
        self,
        series: pd.Series,
        dates: pd.DatetimeIndex,
        test_size: int = 6,
        random_seed: int = 42,
        models_to_test: list[str] | None = None,
    ) -> ModelSelectionResult:
        """
        Run walk-forward CV for all (or specified) models and return full comparison.

        Args:
            series: Historical values
            dates: Date index
            test_size: Number of holdout periods for CV
            random_seed: For reproducibility
            models_to_test: Optional subset of model names to evaluate

        Returns:
            ModelSelectionResult with full comparison details
        """
        np.random.seed(random_seed)

        comparisons: list[ModelComparisonResult] = []
        best_model = "linear"
        best_mape = float("inf")

        candidates = models_to_test or list(self._models.keys())

        for model_name in candidates:
            model = self._models.get(model_name)
            if model is None:
                comparisons.append(ModelComparisonResult(
                    model_name=model_name,
                    mape=float("inf"),
                    evaluation_time_ms=0,
                    eligible=False,
                    error=f"Model '{model_name}' not found in registry",
                ))
                continue

            if len(series) < model.min_data_points:
                comparisons.append(ModelComparisonResult(
                    model_name=model_name,
                    mape=float("inf"),
                    evaluation_time_ms=0,
                    eligible=False,
                    error=f"Need {model.min_data_points} data points, have {len(series)}",
                ))
                continue

            t0 = time.time()
            try:
                mape = model.evaluate(series, dates, test_size)
                elapsed_ms = (time.time() - t0) * 1000

                comparisons.append(ModelComparisonResult(
                    model_name=model_name,
                    mape=mape,
                    evaluation_time_ms=elapsed_ms,
                    eligible=True,
                    error=None if mape != float("inf") else "Evaluation returned inf (internal error)",
                ))

                if mape < best_mape:
                    best_mape = mape
                    best_model = model_name
                elif mape == best_mape:
                    # Tie-breaking: prefer simpler model (EC8)
                    simplicity_rank = {"linear": 0, "ets": 1, "arima": 2, "prophet": 3}
                    if simplicity_rank.get(model_name, 99) < simplicity_rank.get(best_model, 99):
                        best_model = model_name

                logger.info(
                    f"  Model {model_name}: MAPE={mape:.2f}%, time={elapsed_ms:.0f}ms"
                )

            except Exception as e:
                elapsed_ms = (time.time() - t0) * 1000
                comparisons.append(ModelComparisonResult(
                    model_name=model_name,
                    mape=float("inf"),
                    evaluation_time_ms=elapsed_ms,
                    eligible=True,
                    error=str(e)[:200],
                ))
                logger.warning(f"  Model {model_name}: FAILED - {e}")

        logger.info(
            f"Model comparison: best={best_model} (MAPE={best_mape:.2f}%), "
            f"tested {len(comparisons)} models on {len(series)} data points"
        )

        return ModelSelectionResult(
            best_model=best_model,
            best_mape=best_mape,
            comparisons=comparisons,
            data_points=len(series),
            selection_method="walk_forward_cv",
        )

    def auto_select(
        self,
        series: pd.Series,
        dates: pd.DatetimeIndex,
        test_size: int = 6,
        random_seed: int = 42,
        models_to_test: list[str] | None = None,
    ) -> tuple[str, float, ModelSelectionResult]:
        """
        Auto-select the best model for a time series using walk-forward CV.

        Returns:
            Tuple of (best_model_name, best_mape, full_comparison_result)
        """
        result = self.compare_models(
            series, dates, test_size, random_seed, models_to_test
        )
        return result.best_model, result.best_mape, result

    def fit_and_predict(
        self,
        model_name: str,
        series: pd.Series,
        dates: pd.DatetimeIndex,
        horizon: int,
        random_seed: int = 42,
    ) -> ForecastOutput:
        """Fit a specific model and generate forecast."""
        np.random.seed(random_seed)

        model = self.get(model_name)
        if model is None:
            raise ValueError(f"Model '{model_name}' not found in registry")

        params = model.fit(series, dates)
        return model.predict(params, horizon, dates[-1])


# Global model registry singleton
_model_registry: ModelRegistry | None = None


def get_model_registry() -> ModelRegistry:
    """Get the global model registry singleton."""
    global _model_registry
    if _model_registry is None:
        _model_registry = ModelRegistry()
    return _model_registry
