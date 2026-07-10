"""AutoAccuracyReport skill -- forecast accuracy analysis and bias detection."""

import logging
from typing import Any

import numpy as np
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.domain.base_skill import BaseSkill, SkillContext, SkillResult
from app.models.forecast import ForecastVersion, ForecastLineResult
from app.models.actuals import ActualsRecord
from app.models.line_item import LineItem

logger = logging.getLogger(__name__)


class AutoAccuracyReportSkill(BaseSkill):
    """Skill to generate automated forecast accuracy reports."""

    @property
    def name(self) -> str:
        return "auto_accuracy_report"

    @property
    def description(self) -> str:
        return (
            "Generate an automated forecast accuracy report comparing past forecasts "
            "against realized actuals. Tracks MAPE, bias, and accuracy trends."
        )

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "version_id": {
                    "type": "string",
                    "description": "Forecast version to analyze (uses active if not specified)",
                },
                "report_type": {
                    "type": "string",
                    "description": "Report type: accuracy_summary, bias_analysis, trend_over_time, line_item_detail",
                    "enum": ["accuracy_summary", "bias_analysis", "trend_over_time", "line_item_detail"],
                    "default": "accuracy_summary",
                },
                "category": {
                    "type": "string",
                    "description": "Filter by category",
                },
                "line_item_name": {
                    "type": "string",
                    "description": "Specific line item for detail analysis",
                },
                "top_n": {
                    "type": "integer",
                    "description": "Show top N items",
                    "default": 10,
                },
            },
        }

    async def execute(self, params: dict[str, Any], context: SkillContext) -> SkillResult:
        db: Session = context.db
        report_type = params.get("report_type", "accuracy_summary")

        version_id = params.get("version_id") or context.context_manager.get_active_version_id()
        if not version_id:
            return SkillResult.fail("No active forecast version.")

        version = db.query(ForecastVersion).filter(ForecastVersion.id == version_id).first()
        if not version:
            return SkillResult.fail(f"Version '{version_id}' not found.")

        if report_type == "accuracy_summary":
            return await self._accuracy_summary(db, version, params)
        elif report_type == "bias_analysis":
            return await self._bias_analysis(db, version, params)
        elif report_type == "trend_over_time":
            return await self._trend_over_time(db, version, params)
        elif report_type == "line_item_detail":
            return await self._line_item_detail(db, version, params)
        else:
            return SkillResult.fail(f"Unknown report type: {report_type}")

    async def _accuracy_summary(
        self, db: Session, version: ForecastVersion, params: dict
    ) -> SkillResult:
        """Compute accuracy metrics: MAPE, bias, hit rate per line item."""
        top_n = params.get("top_n", 10)
        category_filter = params.get("category")

        # Get forecast data grouped by line item
        query = (
            db.query(
                ForecastLineResult.line_item_id,
                LineItem.name,
                LineItem.category,
                func.avg(ForecastLineResult.p50).label("avg_forecast"),
            )
            .join(LineItem)
            .filter(ForecastLineResult.version_id == version.id)
        )
        if category_filter:
            query = query.filter(LineItem.category.ilike(f"%{category_filter}%"))

        forecast_data = query.group_by(
            ForecastLineResult.line_item_id, LineItem.name, LineItem.category
        ).all()

        # Get actuals averages
        actuals_map = {}
        for row in db.query(
            ActualsRecord.line_item_id,
            func.avg(ActualsRecord.value).label("avg_actual"),
        ).group_by(ActualsRecord.line_item_id).all():
            actuals_map[row.line_item_id] = float(row.avg_actual)

        # Compute metrics
        items = []
        total_mape = 0
        total_bias = 0
        hit_count = 0
        comparable = 0

        for row in forecast_data:
            actual = actuals_map.get(row.line_item_id)
            if actual is None or actual == 0:
                continue

            forecast_val = float(row.avg_forecast)
            error = forecast_val - actual
            abs_pct_error = abs(error / actual) * 100
            bias = (error / actual) * 100
            accuracy = max(0, 100 - abs_pct_error)
            within_10 = abs_pct_error <= 10

            items.append({
                "name": row.name,
                "category": row.category,
                "forecast": forecast_val,
                "actual": actual,
                "mape": abs_pct_error,
                "bias": bias,
                "accuracy": accuracy,
                "within_10": within_10,
            })

            total_mape += abs_pct_error
            total_bias += bias
            comparable += 1
            if within_10:
                hit_count += 1

        if not items:
            return SkillResult.ok(
                message="No comparable data found (no overlapping forecast vs actuals periods).",
                content_blocks=[self._text_block("Cannot compute accuracy without overlapping forecast and actuals periods.")],
            )

        avg_mape = total_mape / comparable
        avg_bias = total_bias / comparable
        hit_rate = (hit_count / comparable) * 100
        avg_accuracy = sum(i["accuracy"] for i in items) / len(items)

        # Sort by MAPE for worst performers
        items.sort(key=lambda x: x["mape"], reverse=True)

        content_blocks = [
            self._text_block(
                f"**Forecast Accuracy Report: {version.name}**\n"
                f"Analyzed {comparable} line items with comparable actuals data."
            ),
            self._table_block(
                title="Overall Metrics",
                columns=[
                    {"key": "metric", "label": "Metric"},
                    {"key": "value", "label": "Value"},
                    {"key": "rating", "label": "Rating"},
                ],
                rows=[
                    {
                        "metric": "Average MAPE",
                        "value": f"{avg_mape:.1f}%",
                        "rating": "Good" if avg_mape < 10 else ("Fair" if avg_mape < 20 else "Needs Improvement"),
                    },
                    {
                        "metric": "Average Bias",
                        "value": f"{'+' if avg_bias > 0 else ''}{avg_bias:.1f}%",
                        "rating": "Over-forecasting" if avg_bias > 2 else ("Under-forecasting" if avg_bias < -2 else "Balanced"),
                    },
                    {
                        "metric": "Hit Rate (±10%)",
                        "value": f"{hit_rate:.0f}%",
                        "rating": "Good" if hit_rate > 70 else ("Fair" if hit_rate > 50 else "Low"),
                    },
                    {
                        "metric": "Average Accuracy",
                        "value": f"{avg_accuracy:.1f}%",
                        "rating": "Good" if avg_accuracy > 85 else ("Fair" if avg_accuracy > 70 else "Needs Improvement"),
                    },
                ],
            ),
        ]

        # Worst performers
        worst_rows = [
            {
                "line_item": i["name"],
                "category": i["category"],
                "forecast": f"${i['forecast']:,.0f}",
                "actual": f"${i['actual']:,.0f}",
                "mape": f"{i['mape']:.1f}%",
                "bias": f"{'+' if i['bias'] > 0 else ''}{i['bias']:.1f}%",
            }
            for i in items[:top_n]
        ]
        content_blocks.append(
            self._table_block(
                title=f"Least Accurate Items (Top {top_n})",
                columns=[
                    {"key": "line_item", "label": "Line Item"},
                    {"key": "category", "label": "Category"},
                    {"key": "forecast", "label": "Forecast"},
                    {"key": "actual", "label": "Actual"},
                    {"key": "mape", "label": "MAPE"},
                    {"key": "bias", "label": "Bias"},
                ],
                rows=worst_rows,
            )
        )

        # Best performers
        items.sort(key=lambda x: x["mape"])
        best_rows = [
            {
                "line_item": i["name"],
                "category": i["category"],
                "accuracy": f"{i['accuracy']:.1f}%",
                "mape": f"{i['mape']:.1f}%",
            }
            for i in items[:5]
        ]
        content_blocks.append(
            self._table_block(
                title="Most Accurate Items (Top 5)",
                columns=[
                    {"key": "line_item", "label": "Line Item"},
                    {"key": "category", "label": "Category"},
                    {"key": "accuracy", "label": "Accuracy"},
                    {"key": "mape", "label": "MAPE"},
                ],
                rows=best_rows,
            )
        )

        return SkillResult.ok(
            message=f"Accuracy report: {avg_mape:.1f}% MAPE, {hit_rate:.0f}% hit rate across {comparable} items",
            data={
                "avg_mape": avg_mape,
                "avg_bias": avg_bias,
                "hit_rate": hit_rate,
                "comparable_items": comparable,
            },
            content_blocks=content_blocks,
        )

    async def _bias_analysis(
        self, db: Session, version: ForecastVersion, params: dict
    ) -> SkillResult:
        """Detect systematic over/under-forecasting patterns."""
        category_filter = params.get("category")

        # Get per-line forecast-vs-actual comparison
        query = (
            db.query(
                ForecastLineResult.line_item_id,
                LineItem.name,
                LineItem.category,
                ForecastLineResult.period,
                ForecastLineResult.p50,
            )
            .join(LineItem)
            .filter(ForecastLineResult.version_id == version.id)
        )
        if category_filter:
            query = query.filter(LineItem.category.ilike(f"%{category_filter}%"))

        forecast_rows = query.all()

        # Get actuals by line item + period
        actuals_map = {}
        for row in db.query(ActualsRecord.line_item_id, ActualsRecord.period, ActualsRecord.value).all():
            actuals_map[(row.line_item_id, row.period)] = float(row.value)

        # Compute bias per line item across all overlapping periods
        line_biases: dict[int, dict] = {}  # line_item_id -> {sum_bias, count, name, category}
        for row in forecast_rows:
            actual = actuals_map.get((row.line_item_id, row.period))
            if actual is None or actual == 0:
                continue

            if row.line_item_id not in line_biases:
                line_biases[row.line_item_id] = {
                    "name": row.name,
                    "category": row.category,
                    "sum_bias": 0,
                    "sum_abs_error": 0,
                    "count": 0,
                }

            error = float(row.p50) - actual
            line_biases[row.line_item_id]["sum_bias"] += error / actual * 100
            line_biases[row.line_item_id]["sum_abs_error"] += abs(error) / actual * 100
            line_biases[row.line_item_id]["count"] += 1

        if not line_biases:
            return SkillResult.ok(
                message="No comparable data for bias analysis.",
                content_blocks=[self._text_block("Insufficient overlapping data for bias analysis.")],
            )

        # Identify systematic bias
        bias_items = []
        for li_id, data in line_biases.items():
            avg_bias = data["sum_bias"] / data["count"]
            avg_mape = data["sum_abs_error"] / data["count"]
            # Tracking signal: cumulative bias / MAD
            mad = avg_mape if avg_mape > 0 else 1
            tracking_signal = abs(avg_bias) / mad

            bias_type = "Neutral"
            if avg_bias > 5:
                bias_type = "Over-forecasting"
            elif avg_bias < -5:
                bias_type = "Under-forecasting"

            bias_items.append({
                "name": data["name"],
                "category": data["category"],
                "avg_bias": avg_bias,
                "tracking_signal": tracking_signal,
                "bias_type": bias_type,
                "periods": data["count"],
            })

        # Sort by absolute bias
        bias_items.sort(key=lambda x: abs(x["avg_bias"]), reverse=True)

        over_count = sum(1 for b in bias_items if b["bias_type"] == "Over-forecasting")
        under_count = sum(1 for b in bias_items if b["bias_type"] == "Under-forecasting")

        rows = [
            {
                "line_item": b["name"],
                "category": b["category"],
                "avg_bias": f"{'+' if b['avg_bias'] > 0 else ''}{b['avg_bias']:.1f}%",
                "signal": f"{b['tracking_signal']:.2f}",
                "direction": b["bias_type"],
                "periods": str(b["periods"]),
            }
            for b in bias_items[:15]
        ]

        content_blocks = [
            self._text_block(
                f"**Bias Analysis: {version.name}**\n"
                f"- Over-forecasting: **{over_count}** items\n"
                f"- Under-forecasting: **{under_count}** items\n"
                f"- Neutral: **{len(bias_items) - over_count - under_count}** items"
            ),
            self._table_block(
                title="Systematic Bias by Line Item",
                columns=[
                    {"key": "line_item", "label": "Line Item"},
                    {"key": "category", "label": "Category"},
                    {"key": "avg_bias", "label": "Avg Bias"},
                    {"key": "signal", "label": "Tracking Signal"},
                    {"key": "direction", "label": "Direction"},
                    {"key": "periods", "label": "Periods"},
                ],
                rows=rows,
            ),
        ]

        return SkillResult.ok(
            message=f"Bias analysis: {over_count} over-forecasting, {under_count} under-forecasting items",
            data={
                "over_forecasting": over_count,
                "under_forecasting": under_count,
                "neutral": len(bias_items) - over_count - under_count,
            },
            content_blocks=content_blocks,
        )

    async def _trend_over_time(
        self, db: Session, version: ForecastVersion, params: dict
    ) -> SkillResult:
        """Show accuracy evolution across forecast versions."""
        versions = (
            db.query(ForecastVersion)
            .order_by(ForecastVersion.created_at)
            .limit(10)
            .all()
        )

        if len(versions) < 2:
            return SkillResult.ok(
                message="Need at least 2 forecast versions to show trends.",
                content_blocks=[self._text_block("Create more forecast versions to track accuracy trends over time.")],
            )

        # For each version, compute average MAPE against actuals
        actuals_map = {}
        for row in db.query(ActualsRecord.line_item_id, func.avg(ActualsRecord.value).label("avg")).group_by(ActualsRecord.line_item_id).all():
            actuals_map[row.line_item_id] = float(row.avg)

        version_metrics = []
        for v in versions:
            forecasts = (
                db.query(
                    ForecastLineResult.line_item_id,
                    func.avg(ForecastLineResult.p50).label("avg_fc"),
                )
                .filter(ForecastLineResult.version_id == v.id)
                .group_by(ForecastLineResult.line_item_id)
                .all()
            )

            mapes = []
            for row in forecasts:
                actual = actuals_map.get(row.line_item_id)
                if actual and actual != 0:
                    mapes.append(abs((float(row.avg_fc) - actual) / actual) * 100)

            avg_mape = np.mean(mapes) if mapes else None

            version_metrics.append({
                "version": v.name,
                "created": v.created_at.strftime("%Y-%m-%d") if v.created_at else "N/A",
                "lines": len(mapes),
                "avg_mape": avg_mape,
            })

        rows = [
            {
                "version": m["version"],
                "date": m["created"],
                "lines": str(m["lines"]),
                "avg_mape": f"{m['avg_mape']:.1f}%" if m["avg_mape"] is not None else "N/A",
                "trend": "",
            }
            for m in version_metrics
        ]

        # Add trend arrows
        for i in range(1, len(rows)):
            prev = version_metrics[i - 1]["avg_mape"]
            curr = version_metrics[i]["avg_mape"]
            if prev is not None and curr is not None:
                if curr < prev:
                    rows[i]["trend"] = "Improving"
                elif curr > prev:
                    rows[i]["trend"] = "Declining"
                else:
                    rows[i]["trend"] = "Stable"

        content_blocks = [
            self._text_block(f"**Accuracy Trend** across {len(versions)} forecast versions"),
            self._table_block(
                title="MAPE Over Time",
                columns=[
                    {"key": "version", "label": "Version"},
                    {"key": "date", "label": "Date"},
                    {"key": "lines", "label": "Comparable Lines"},
                    {"key": "avg_mape", "label": "Avg MAPE"},
                    {"key": "trend", "label": "Trend"},
                ],
                rows=rows,
            ),
        ]

        return SkillResult.ok(
            message=f"Accuracy trend across {len(versions)} versions",
            data={"versions_analyzed": len(versions), "metrics": version_metrics},
            content_blocks=content_blocks,
        )

    async def _line_item_detail(
        self, db: Session, version: ForecastVersion, params: dict
    ) -> SkillResult:
        """Deep dive on a specific line item's accuracy."""
        name = params.get("line_item_name", "")
        if not name:
            return SkillResult.fail("Line item name is required for detail analysis.")

        li = (
            db.query(LineItem)
            .filter(
                (LineItem.name.ilike(f"%{name}%"))
                | (LineItem.account_code.ilike(f"%{name}%"))
            )
            .first()
        )
        if not li:
            return SkillResult.fail(f"Line item '{name}' not found.")

        # Get forecast periods
        forecasts = (
            db.query(ForecastLineResult)
            .filter(
                ForecastLineResult.version_id == version.id,
                ForecastLineResult.line_item_id == li.id,
            )
            .order_by(ForecastLineResult.period)
            .all()
        )

        # Get actuals for same periods
        actuals_map = {}
        for row in db.query(ActualsRecord).filter(ActualsRecord.line_item_id == li.id).all():
            actuals_map[row.period] = float(row.value)

        rows = []
        errors = []
        for f in forecasts:
            actual = actuals_map.get(f.period)
            if actual is not None and actual != 0:
                error = (float(f.p50) - actual) / actual * 100
                abs_error = abs(error)
                errors.append(abs_error)
                rows.append({
                    "period": f.period,
                    "forecast": f"${f.p50:,.0f}",
                    "actual": f"${actual:,.0f}",
                    "error": f"{'+' if error > 0 else ''}{error:.1f}%",
                    "accuracy": f"{max(0, 100 - abs_error):.1f}%",
                })
            else:
                rows.append({
                    "period": f.period,
                    "forecast": f"${f.p50:,.0f}",
                    "actual": "N/A",
                    "error": "N/A",
                    "accuracy": "N/A",
                })

        avg_mape = np.mean(errors) if errors else 0
        avg_accuracy = 100 - avg_mape if errors else 0

        content_blocks = [
            self._text_block(
                f"**Accuracy Detail: {li.name}** ({li.account_code})\n"
                f"Category: {li.category} | Model: {forecasts[0].model_type if forecasts else 'N/A'}\n"
                f"Average MAPE: **{avg_mape:.1f}%** | Average Accuracy: **{avg_accuracy:.1f}%**"
            ),
            self._table_block(
                title=f"{li.name} — Period-by-Period Accuracy",
                columns=[
                    {"key": "period", "label": "Period"},
                    {"key": "forecast", "label": "Forecast"},
                    {"key": "actual", "label": "Actual"},
                    {"key": "error", "label": "Error"},
                    {"key": "accuracy", "label": "Accuracy"},
                ],
                rows=rows,
            ),
        ]

        return SkillResult.ok(
            message=f"Accuracy detail for {li.name}: {avg_mape:.1f}% MAPE, {avg_accuracy:.1f}% accuracy",
            data={
                "line_item": li.name,
                "avg_mape": avg_mape,
                "avg_accuracy": avg_accuracy,
                "periods_compared": len(errors),
            },
            content_blocks=content_blocks,
        )
