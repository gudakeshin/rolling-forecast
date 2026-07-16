"""RunEnsemble skill -- combines multiple models via weighted averaging for more robust forecasts."""

import logging
from typing import Any

import numpy as np
import pandas as pd
from sqlalchemy.orm import Session

from app.domain.base_skill import BaseSkill, SkillContext, SkillResult
from app.domain.engines.model_registry import get_model_registry
from app.models.forecast import ForecastVersion, ForecastLineResult
from app.models.actuals import ActualsDataset, ActualsRecord
from app.models.line_item import LineItem

logger = logging.getLogger(__name__)


class RunEnsembleSkill(BaseSkill):
    """Skill to run ensemble forecasting with weighted model combination."""

    @property
    def name(self) -> str:
        return "run_ensemble"

    @property
    def description(self) -> str:
        return (
            "Run an ensemble of multiple statistical models for a line item or set "
            "of line items, then combine predictions using weighted averaging based "
            "on each model's out-of-sample accuracy."
        )

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "version_id": {
                    "type": "string",
                    "description": "Forecast version to ensemble (uses active version if not specified)",
                },
                "line_item_name": {
                    "type": "string",
                    "description": "Specific line item to ensemble (if blank, runs for all low/medium confidence lines)",
                },
                "models": {
                    "type": "array",
                    "description": "Models to include in ensemble (default: all available)",
                    "items": {"type": "string"},
                },
                "weighting_method": {
                    "type": "string",
                    "description": "How to weight predictions: inverse_mape, equal, or rank",
                    "enum": ["inverse_mape", "equal", "rank"],
                    "default": "inverse_mape",
                },
                "top_k": {
                    "type": "integer",
                    "description": "Only use top K models by accuracy (default 3)",
                    "default": 3,
                },
            },
        }

    @property
    def required_role(self) -> str:
        return "generate"

    async def execute(self, params: dict[str, Any], context: SkillContext) -> SkillResult:
        db: Session = context.db

        version_id = params.get("version_id") or context.context_manager.get_active_version_id()
        if not version_id:
            return SkillResult.fail("No active forecast version.")

        version = db.query(ForecastVersion).filter(ForecastVersion.id == version_id).first()
        if not version:
            return SkillResult.fail(f"Version '{version_id}' not found.")

        weighting = params.get("weighting_method", "inverse_mape")
        top_k = params.get("top_k", 3)
        model_names = params.get("models") or ["arima", "ets", "linear"]  # prophet excluded for speed in ensemble
        model_registry = get_model_registry()

        # Determine target line items
        line_item_name = params.get("line_item_name")
        if line_item_name:
            target_items = (
                db.query(LineItem)
                .filter(
                    (LineItem.name.ilike(f"%{line_item_name}%"))
                    | (LineItem.account_code.ilike(f"%{line_item_name}%"))
                )
                .all()
            )
        else:
            # Get all lines with low or medium confidence
            low_med_results = (
                db.query(ForecastLineResult.line_item_id)
                .filter(
                    ForecastLineResult.version_id == version_id,
                    ForecastLineResult.confidence_level.in_(["low", "medium"]),
                )
                .distinct()
                .all()
            )
            line_item_ids = [r[0] for r in low_med_results]
            target_items = db.query(LineItem).filter(LineItem.id.in_(line_item_ids)).all() if line_item_ids else []

        if not target_items:
            return SkillResult.ok(
                message="No target line items found for ensemble.",
                content_blocks=[self._text_block("No low/medium confidence items to ensemble. Specify a line item name, or all items already have high confidence.")],
            )

        # Get the dataset for historical data
        dataset = (
            db.query(ActualsDataset)
            .filter(ActualsDataset.id == version.actuals_dataset_id)
            .first()
        ) if version.actuals_dataset_id else db.query(ActualsDataset).order_by(ActualsDataset.ingested_at.desc()).first()

        if not dataset:
            return SkillResult.fail("No actuals dataset found for ensemble modeling.")

        results_summary = []
        ensemble_count = 0

        for li in target_items:
            # Get actuals
            records = (
                db.query(ActualsRecord)
                .filter(
                    ActualsRecord.dataset_id == dataset.id,
                    ActualsRecord.line_item_id == li.id,
                )
                .order_by(ActualsRecord.period)
                .all()
            )

            if len(records) < 6:
                results_summary.append({
                    "line_item": li.name,
                    "status": "Skipped (insufficient data)",
                    "models_used": 0,
                    "old_confidence": "-",
                    "new_confidence": "-",
                })
                continue

            values = pd.Series([r.value for r in records])
            periods = [r.period for r in records]
            dates = pd.DatetimeIndex([pd.Timestamp(p + "-01") for p in periods])
            horizon = version.horizon_months or 12

            # Run each model and collect forecasts + MAPE
            model_forecasts = {}
            model_mapes = {}

            for model_name in model_names:
                try:
                    output = model_registry.fit_and_predict(
                        model_name, values, dates, horizon,
                        random_seed=version.random_seed or 42,
                    )
                    model_forecasts[model_name] = output.point_forecast

                    # Weight on out-of-sample CV MAPE (same pattern as generate_baseline).
                    # Fall back to in-sample only when history is too short for CV.
                    mape: float | None = None
                    mape_source = "cv"
                    model = model_registry.get(model_name)
                    if model is not None:
                        try:
                            cv = model.evaluate_cv(
                                values, dates, n_folds=3, fold_horizon=3
                            )
                            mean_mape = cv.get("mean_mape")
                            if (
                                mean_mape is not None
                                and mean_mape != float("inf")
                                and (cv.get("n_folds_used") or 0) > 0
                            ):
                                mape = float(mean_mape)
                        except Exception as cv_err:
                            logger.debug(
                                "Ensemble CV MAPE failed for %s/%s: %s",
                                model_name,
                                li.name,
                                cv_err,
                            )
                    if mape is None:
                        mape = (
                            output.fit_metrics.get("in_sample_mape")
                            or output.fit_metrics.get("mape")
                        )
                        mape_source = "in_sample_fallback"
                        if mape is None:
                            mape = 100.0
                        logger.info(
                            "Ensemble weight for %s/%s uses %s MAPE=%.2f",
                            model_name,
                            li.name,
                            mape_source,
                            mape,
                        )
                    model_mapes[model_name] = max(float(mape), 0.01)
                except Exception as e:
                    logger.warning(f"Ensemble: model '{model_name}' failed for {li.name}: {e}")

            if not model_forecasts:
                results_summary.append({
                    "line_item": li.name,
                    "status": "Failed (all models errored)",
                    "models_used": 0,
                    "old_confidence": "-",
                    "new_confidence": "-",
                })
                continue

            # Select top K
            sorted_models = sorted(model_mapes.items(), key=lambda x: x[1])[:top_k]
            selected = {name: model_forecasts[name] for name, _ in sorted_models if name in model_forecasts}
            selected_mapes = {name: mape for name, mape in sorted_models if name in model_forecasts}

            # Compute weights
            weights = self._compute_weights(selected_mapes, weighting)

            # Weighted average
            ensemble_forecast = np.zeros(horizon)
            for model_name, forecast in selected.items():
                w = weights.get(model_name, 0)
                ensemble_forecast += w * np.array(forecast[:horizon])

            # Compute ensemble confidence interval (use weighted std across models)
            all_forecasts = np.array([np.array(f[:horizon]) for f in selected.values()])
            ensemble_std = np.std(all_forecasts, axis=0) if len(all_forecasts) > 1 else np.abs(ensemble_forecast) * 0.1

            # Clamp negatives for non-negative lines
            if not li.allow_negative:
                ensemble_forecast = np.maximum(ensemble_forecast, 0)

            # Get old confidence for comparison
            old_result = (
                db.query(ForecastLineResult)
                .filter(
                    ForecastLineResult.version_id == version_id,
                    ForecastLineResult.line_item_id == li.id,
                )
                .first()
            )
            old_confidence = old_result.confidence_score if old_result else 0

            # Update existing forecast results
            existing_results = (
                db.query(ForecastLineResult)
                .filter(
                    ForecastLineResult.version_id == version_id,
                    ForecastLineResult.line_item_id == li.id,
                )
                .order_by(ForecastLineResult.period)
                .all()
            )

            avg_mape = np.mean(list(selected_mapes.values()))
            new_confidence_base = max(0, min(100, 100 - avg_mape * 3))  # Ensemble bonus
            new_confidence = min(100, new_confidence_base + 10)  # +10 ensemble bonus

            for i, result in enumerate(existing_results):
                if i < horizon:
                    result.p50 = float(ensemble_forecast[i])
                    result.p10 = float(max(0, ensemble_forecast[i] - 1.28 * ensemble_std[i])) if not li.allow_negative else float(ensemble_forecast[i] - 1.28 * ensemble_std[i])
                    result.p90 = float(ensemble_forecast[i] + 1.28 * ensemble_std[i])
                    result.model_type = f"ensemble({'+'.join(selected.keys())})"
                    result.model_mape = float(avg_mape)
                    result.confidence_score = new_confidence
                    result.confidence_level = (
                        "high" if new_confidence >= 70
                        else "medium" if new_confidence >= 50
                        else "low"
                    )

            ensemble_count += 1
            model_list = ", ".join(f"{m}({w:.0%})" for m, w in weights.items())
            results_summary.append({
                "line_item": li.name,
                "status": "Ensembled",
                "models_used": len(selected),
                "model_weights": model_list,
                "avg_mape": f"{avg_mape:.1f}%",
                "old_confidence": f"{old_confidence:.0f}",
                "new_confidence": f"{new_confidence:.0f}",
            })

        db.commit()

        # Build response
        rows = [
            {
                "line_item": r["line_item"],
                "status": r["status"],
                "models": r.get("model_weights", str(r.get("models_used", 0))),
                "old_conf": r["old_confidence"],
                "new_conf": r["new_confidence"],
            }
            for r in results_summary
        ]

        content_blocks = [
            self._text_block(
                f"Ensemble forecasting complete: **{ensemble_count}** line items updated "
                f"using **{weighting}** weighting with top {top_k} models."
            ),
            self._table_block(
                title=f"Ensemble Results ({len(results_summary)} items)",
                columns=[
                    {"key": "line_item", "label": "Line Item"},
                    {"key": "status", "label": "Status"},
                    {"key": "models", "label": "Models (Weight)"},
                    {"key": "old_conf", "label": "Old Conf"},
                    {"key": "new_conf", "label": "New Conf"},
                ],
                rows=rows,
            ),
        ]

        if ensemble_count > 0:
            content_blocks.append(
                self._panel_trigger(
                    panel="forecast_table",
                    params={"version_id": version_id},
                    label="View Updated Forecast Table",
                )
            )

        return SkillResult.ok(
            message=f"Ensemble complete: {ensemble_count} items updated with {weighting} weighting",
            data={
                "ensemble_count": ensemble_count,
                "weighting_method": weighting,
                "top_k": top_k,
                "results": results_summary,
            },
            content_blocks=content_blocks,
        )

    def _compute_weights(
        self, model_mapes: dict[str, float], method: str
    ) -> dict[str, float]:
        """Compute model weights based on the chosen method."""
        if method == "equal":
            n = len(model_mapes)
            return {m: 1.0 / n for m in model_mapes}

        elif method == "rank":
            sorted_models = sorted(model_mapes.items(), key=lambda x: x[1])
            n = len(sorted_models)
            # Higher rank (lower MAPE) gets more weight
            total = sum(range(1, n + 1))
            return {
                name: (n - i) / total
                for i, (name, _) in enumerate(sorted_models)
            }

        else:  # inverse_mape
            inv = {m: 1.0 / mape for m, mape in model_mapes.items()}
            total = sum(inv.values())
            return {m: v / total for m, v in inv.items()}
