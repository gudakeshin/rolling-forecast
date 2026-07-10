"""PlanForecast skill -- AI agent analyzes data and helps the user plan their forecast.

This is the conversational entry point for forecasting. Instead of blindly running models,
the agent:
1. Analyzes the data quality and characteristics
2. Runs a sample model comparison to show MAPE scores per algorithm
3. Presents findings and recommendations to the user
4. Lets the user choose models, horizon, and other settings
5. Then triggers generate_baseline with those choices
"""

import logging
import time
from typing import Any

import numpy as np
import pandas as pd
from sqlalchemy.orm import Session

from app.domain.base_skill import BaseSkill, SkillContext, SkillResult
from app.domain.engines.model_registry import get_model_registry
from app.models.actuals import ActualsDataset, ActualsRecord
from app.models.line_item import LineItem

logger = logging.getLogger(__name__)


class PlanForecastSkill(BaseSkill):
    """Skill to analyze data and plan forecast configuration before running models."""

    @property
    def name(self) -> str:
        return "plan_forecast"

    @property
    def description(self) -> str:
        return (
            "Analyze the uploaded actuals data and plan the forecast configuration. "
            "This is the FIRST step before generating a forecast. It runs all 4 algorithms "
            "(ARIMA, Prophet, ETS, Linear) on a sample of line items, computes MAPE scores "
            "for each, and presents the results so the user can choose which models to use "
            "and configure the forecast horizon. "
            "ALWAYS use this skill before generate_baseline when the user asks to create "
            "or run a forecast. Show the results and ask the user for their preferences."
        )

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "dataset_id": {
                    "type": "string",
                    "description": "ID of the actuals dataset to analyze (uses latest if not specified)",
                },
                "sample_size": {
                    "type": "integer",
                    "description": "Number of line items to sample for model comparison (default: 5, max: 10)",
                    "default": 5,
                },
            },
        }

    @property
    def required_role(self) -> str:
        return "generate"

    async def execute(self, params: dict[str, Any], context: SkillContext) -> SkillResult:
        """Analyze data and run model comparison."""
        db: Session = context.db
        start_time = time.time()

        # Get dataset
        dataset_id = params.get("dataset_id") or context.context_manager.get_memory("last_dataset_id")
        if not dataset_id:
            dataset = db.query(ActualsDataset).order_by(ActualsDataset.ingested_at.desc()).first()
            if not dataset:
                return SkillResult.fail("No actuals data found. Please upload actuals first.")
            dataset_id = dataset.id
        else:
            dataset = db.query(ActualsDataset).filter(ActualsDataset.id == dataset_id).first()
            if not dataset:
                return SkillResult.fail(f"Dataset '{dataset_id}' not found.")

        # Get all non-calculated line items
        line_items = db.query(LineItem).filter(LineItem.is_calculated == False).all()
        if not line_items:
            return SkillResult.fail("No line items found. Please ingest actuals data first.")

        # ── Step 1: Data Quality Analysis ──
        data_summary: list[dict] = []
        categories: dict[str, int] = {}
        total_records = 0
        min_points = float("inf")
        max_points = 0

        for li in line_items:
            records = (
                db.query(ActualsRecord)
                .filter(
                    ActualsRecord.dataset_id == dataset_id,
                    ActualsRecord.line_item_id == li.id,
                )
                .order_by(ActualsRecord.period)
                .all()
            )
            n_points = len(records)
            total_records += n_points
            min_points = min(min_points, n_points) if n_points > 0 else min_points
            max_points = max(max_points, n_points)
            categories[li.category] = categories.get(li.category, 0) + 1

            if records:
                values = [r.value for r in records]
                data_summary.append({
                    "name": li.name,
                    "category": li.category,
                    "n_points": n_points,
                    "period_start": records[0].period,
                    "period_end": records[-1].period,
                    "mean": np.mean(values),
                    "std": np.std(values),
                    "cv": np.std(values) / (np.mean(values) + 1e-10),  # Coefficient of variation
                    "is_zero": all(v == 0 for v in values),
                    "has_negatives": any(v < 0 for v in values),
                })

        # ── Step 2: Run Model Comparison on Sample ──
        sample_size = min(params.get("sample_size", 5), 10, len(data_summary))
        # Pick diverse sample: highest CV (most volatile) + a few random
        non_zero = [d for d in data_summary if not d["is_zero"] and d["n_points"] >= 12]
        non_zero.sort(key=lambda x: -x["cv"])
        sample_items = non_zero[:max(2, sample_size // 2)]  # Most volatile
        remaining = [d for d in non_zero if d not in sample_items]
        if remaining and len(sample_items) < sample_size:
            # Add random remainder
            np.random.seed(42)
            extra = np.random.choice(
                len(remaining),
                size=min(sample_size - len(sample_items), len(remaining)),
                replace=False,
            )
            sample_items.extend([remaining[i] for i in extra])

        model_registry = get_model_registry()
        comparison_results: list[dict] = []
        model_win_counts: dict[str, int] = {"linear": 0, "ets": 0, "arima": 0, "prophet": 0}
        model_avg_mape: dict[str, list[float]] = {"linear": [], "ets": [], "arima": [], "prophet": []}

        for item in sample_items:
            # Get the data for this line item
            li = next((l for l in line_items if l.name == item["name"]), None)
            if not li:
                continue

            records = (
                db.query(ActualsRecord)
                .filter(
                    ActualsRecord.dataset_id == dataset_id,
                    ActualsRecord.line_item_id == li.id,
                )
                .order_by(ActualsRecord.period)
                .all()
            )
            if not records:
                continue

            values = pd.Series([r.value for r in records])
            dates = pd.DatetimeIndex([pd.Timestamp(r.period + "-01") for r in records])

            # Run comparison
            result = model_registry.compare_models(values, dates, test_size=6)
            row = {
                "line_item": item["name"],
                "category": item["category"],
                "data_points": str(item["n_points"]),
                "volatility": f"{item['cv'] * 100:.0f}%",
                "best_model": result.best_model.upper(),
                "best_mape": f"{result.best_mape:.1f}%",
            }

            for comp in result.comparisons:
                if comp.mape != float("inf") and comp.mape is not None:
                    row[comp.model_name] = f"{comp.mape:.1f}%"
                    model_avg_mape[comp.model_name].append(comp.mape)
                else:
                    row[comp.model_name] = comp.error[:20] if comp.error else "N/A"

            comparison_results.append(row)
            model_win_counts[result.best_model] = model_win_counts.get(result.best_model, 0) + 1

        elapsed = time.time() - start_time

        # ── Step 3: Build Rich Response ──
        content_blocks = []

        # Data overview
        content_blocks.append(
            self._text_block(
                f"**Data Analysis Complete** — Analyzed {len(data_summary)} line items "
                f"across {len(categories)} categories."
            )
        )

        content_blocks.append(
            self._table_block(
                title="Data Overview",
                columns=[
                    {"key": "metric", "label": "Metric"},
                    {"key": "value", "label": "Value"},
                ],
                rows=[
                    {"metric": "Total line items", "value": str(len(data_summary))},
                    {"metric": "Categories", "value": ", ".join(sorted(categories.keys()))},
                    {"metric": "History range", "value": f"{data_summary[0]['period_start']} to {data_summary[0]['period_end']}" if data_summary else "N/A"},
                    {"metric": "Data points per item", "value": f"{int(min_points)} to {int(max_points)} months"},
                    {"metric": "Zero-activity lines", "value": str(sum(1 for d in data_summary if d["is_zero"]))},
                    {"metric": "Lines with < 12 months (ARIMA/Prophet ineligible)", "value": str(sum(1 for d in data_summary if d["n_points"] < 18))},
                ],
            )
        )

        # Model comparison table
        if comparison_results:
            content_blocks.append(
                self._text_block(
                    f"**Model Comparison** — Tested all 4 algorithms on {len(comparison_results)} "
                    f"representative line items using 6-month holdout cross-validation:"
                )
            )

            comp_columns = [
                {"key": "line_item", "label": "Line Item"},
                {"key": "data_points", "label": "Months"},
            ]
            for m in ["linear", "ets", "arima", "prophet"]:
                comp_columns.append({"key": m, "label": m.upper()})
            comp_columns.append({"key": "best_model", "label": "Winner"})

            content_blocks.append(
                self._table_block(
                    title="MAPE Scores by Model (lower = better fit)",
                    columns=comp_columns,
                    rows=comparison_results,
                )
            )

        # Model performance summary
        perf_rows = []
        for model_name in ["ets", "arima", "prophet", "linear"]:
            mapes = model_avg_mape.get(model_name, [])
            wins = model_win_counts.get(model_name, 0)
            if mapes:
                avg = np.mean(mapes)
                perf_rows.append({
                    "model": model_name.upper(),
                    "avg_mape": f"{avg:.1f}%",
                    "wins": str(wins),
                    "tested": str(len(mapes)),
                    "status": "Recommended" if wins > 0 else "Available",
                })
            else:
                perf_rows.append({
                    "model": model_name.upper(),
                    "avg_mape": "N/A",
                    "wins": "0",
                    "tested": "0",
                    "status": "Not enough data" if model_name in ("arima", "prophet") else "Available",
                })

        content_blocks.append(
            self._table_block(
                title="Algorithm Performance Summary",
                columns=[
                    {"key": "model", "label": "Algorithm"},
                    {"key": "avg_mape", "label": "Avg MAPE"},
                    {"key": "wins", "label": "Best Fit Count"},
                    {"key": "tested", "label": "Tested On"},
                    {"key": "status", "label": "Status"},
                ],
                rows=perf_rows,
            )
        )

        # Recommendation
        recommended_models = [m for m, w in model_win_counts.items() if w > 0]
        if not recommended_models:
            recommended_models = ["linear", "ets"]

        best_overall = max(model_win_counts.items(), key=lambda x: x[1])[0] if any(model_win_counts.values()) else "ets"

        content_blocks.append(
            self._text_block(
                f"\n**AI Recommendation:**\n"
                f"- **Best algorithm overall:** {best_overall.upper()} "
                f"(won {model_win_counts.get(best_overall, 0)} of {len(comparison_results)} tests)\n"
                f"- **Recommended approach:** Use `auto` mode to let each line item pick its best model\n"
                f"- **Suggested horizon:** 12 months (standard rolling forecast)\n\n"
                f"**Ready to proceed?** I can:\n"
                f"1. **Run with auto-selection** (recommended) — each line item uses its best model\n"
                f"2. **Force a specific model** — use {best_overall.upper()} for all items\n"
                f"3. **Limit models** — only test specific algorithms (e.g., ETS + ARIMA)\n"
                f"4. **Adjust horizon** — change from 12 months to a custom period\n\n"
                f"What would you like to do?"
            )
        )

        context.context_manager.set_memory("plan_forecast_complete", True)
        context.context_manager.set_memory("last_plan_best_model", best_overall)

        return SkillResult.ok(
            message=(
                f"Data analysis complete. Tested 4 algorithms on {len(comparison_results)} sample items. "
                f"Best performer: {best_overall.upper()}. "
                f"Models that won: {', '.join(m.upper() for m in recommended_models)}. "
                f"Ready for user to choose forecast configuration."
            ),
            data={
                "dataset_id": dataset_id,
                "line_item_count": len(data_summary),
                "categories": categories,
                "model_comparison": comparison_results,
                "model_wins": model_win_counts,
                "recommended_models": recommended_models,
                "best_overall": best_overall,
                "analysis_time": elapsed,
            },
            content_blocks=content_blocks,
        )
