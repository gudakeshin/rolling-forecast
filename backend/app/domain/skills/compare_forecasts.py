"""CompareForecastsSkill -- variance analysis between forecast versions or vs actuals."""

import logging
from typing import Any

from sqlalchemy.orm import Session
from sqlalchemy import func

from app.domain.base_skill import BaseSkill, SkillContext, SkillResult
from app.models.forecast import ForecastVersion, ForecastLineResult
from app.models.actuals import ActualsRecord
from app.models.line_item import LineItem

logger = logging.getLogger(__name__)


class CompareForecastsSkill(BaseSkill):
    """Skill to compare forecast versions or forecast vs actuals with variance analysis."""

    @property
    def name(self) -> str:
        return "compare_forecasts"

    @property
    def description(self) -> str:
        return (
            "Compare two forecast versions against each other or compare a forecast "
            "against historical actuals. Performs variance analysis showing absolute "
            "and percentage differences by line item and period. "
            "Use this when the user asks to compare forecasts, check variances, "
            "see what changed between versions, or compare forecast to actuals."
        )

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "comparison_type": {
                    "type": "string",
                    "description": "Type of comparison: version_vs_version or forecast_vs_actuals",
                    "enum": ["version_vs_version", "forecast_vs_actuals"],
                    "default": "version_vs_version",
                },
                "version_id_a": {
                    "type": "string",
                    "description": "First version ID (current/active version if not specified)",
                },
                "version_id_b": {
                    "type": "string",
                    "description": "Second version ID (for version_vs_version comparison)",
                },
                "category_filter": {
                    "type": "string",
                    "description": "Filter by P&L category (e.g., 'Revenue', 'COGS', 'OpEx')",
                },
                "threshold_pct": {
                    "type": "number",
                    "description": "Only show lines with variance above this threshold (percentage). Default 0 = show all.",
                    "default": 0,
                },
                "top_n": {
                    "type": "integer",
                    "description": "Show only top N variances by absolute percentage. Default 20.",
                    "default": 20,
                },
            },
            "required": [],
        }

    async def execute(self, params: dict[str, Any], context: SkillContext) -> SkillResult:
        db: Session = context.db
        comparison_type = params.get("comparison_type", "version_vs_version")

        if comparison_type == "forecast_vs_actuals":
            return await self._compare_forecast_vs_actuals(db, params, context)
        else:
            return await self._compare_version_vs_version(db, params, context)

    async def _compare_version_vs_version(
        self, db: Session, params: dict[str, Any], context: SkillContext
    ) -> SkillResult:
        """Compare two forecast versions."""
        version_id_a = params.get("version_id_a") or context.context_manager.get_active_version_id()
        version_id_b = params.get("version_id_b")

        if not version_id_a:
            return SkillResult.fail("No active forecast version for comparison.")

        # If no second version specified, find the previous version
        if not version_id_b:
            version_a = db.query(ForecastVersion).filter(ForecastVersion.id == version_id_a).first()
            if not version_a:
                return SkillResult.fail(f"Version '{version_id_a}' not found.")

            previous = (
                db.query(ForecastVersion)
                .filter(
                    ForecastVersion.id != version_id_a,
                    ForecastVersion.created_at < version_a.created_at,
                )
                .order_by(ForecastVersion.created_at.desc())
                .first()
            )
            if not previous:
                return SkillResult.fail("No prior forecast version found for comparison.")
            version_id_b = previous.id
        
        va = db.query(ForecastVersion).filter(ForecastVersion.id == version_id_a).first()
        vb = db.query(ForecastVersion).filter(ForecastVersion.id == version_id_b).first()

        if not va or not vb:
            return SkillResult.fail("One or both forecast versions not found.")

        # Fetch results for both versions
        category_filter = params.get("category_filter")
        threshold_pct = params.get("threshold_pct", 0)
        top_n = params.get("top_n", 20)

        results_a = self._get_aggregated_results(db, version_id_a, category_filter)
        results_b = self._get_aggregated_results(db, version_id_b, category_filter)

        # Compute variances
        all_keys = set(results_a.keys()) | set(results_b.keys())
        variances = []

        for key in all_keys:
            line_item_id, line_item_name, category = key
            val_a = results_a.get(key, 0.0)
            val_b = results_b.get(key, 0.0)

            abs_var = val_a - val_b
            pct_var = (abs_var / abs(val_b) * 100) if val_b != 0 else (100.0 if val_a != 0 else 0.0)

            if abs(pct_var) >= threshold_pct:
                variances.append({
                    "line_item_name": line_item_name,
                    "category": category,
                    "value_a": val_a,
                    "value_b": val_b,
                    "abs_variance": abs_var,
                    "pct_variance": pct_var,
                })

        # Sort by absolute percentage variance
        variances.sort(key=lambda x: abs(x["pct_variance"]), reverse=True)
        variances = variances[:top_n]

        if not variances:
            return SkillResult.ok(
                message="No significant variances found between the two versions.",
                data={"variance_count": 0},
                content_blocks=[
                    self._text_block("No significant variances found between the selected versions.")
                ],
            )

        # Build comparison table
        rows = []
        for v in variances:
            rows.append({
                "line_item": v["line_item_name"],
                "category": v["category"],
                "version_a": self._format_value(v["value_a"]),
                "version_b": self._format_value(v["value_b"]),
                "variance": self._format_value(v["abs_variance"]),
                "pct_change": f"{'+' if v['pct_variance'] > 0 else ''}{v['pct_variance']:.1f}%",
            })

        # Summary stats
        total_var_a = sum(results_a.values())
        total_var_b = sum(results_b.values())
        total_change = ((total_var_a - total_var_b) / abs(total_var_b) * 100) if total_var_b != 0 else 0

        content_blocks = [
            self._text_block(
                f"**Comparison: {va.name} vs {vb.name}**\n"
                f"Total forecast change: {'+' if total_change > 0 else ''}{total_change:.1f}%"
            ),
            self._table_block(
                title=f"Top Variances ({len(variances)} lines)",
                columns=[
                    {"key": "line_item", "label": "Line Item"},
                    {"key": "category", "label": "Category"},
                    {"key": "version_a", "label": va.name},
                    {"key": "version_b", "label": vb.name},
                    {"key": "variance", "label": "Variance"},
                    {"key": "pct_change", "label": "% Change"},
                ],
                rows=rows,
            ),
        ]

        # Summary by category
        cat_summary = {}
        for v in variances:
            cat = v["category"]
            if cat not in cat_summary:
                cat_summary[cat] = {"count": 0, "total_abs_var": 0}
            cat_summary[cat]["count"] += 1
            cat_summary[cat]["total_abs_var"] += abs(v["abs_variance"])

        cat_rows = [
            {"category": cat, "lines": str(info["count"]), "total_var": self._format_value(info["total_abs_var"])}
            for cat, info in sorted(cat_summary.items(), key=lambda x: x[1]["total_abs_var"], reverse=True)
        ]

        if cat_rows:
            content_blocks.append(
                self._table_block(
                    title="Variance by Category",
                    columns=[
                        {"key": "category", "label": "Category"},
                        {"key": "lines", "label": "# Lines"},
                        {"key": "total_var", "label": "Total |Variance|"},
                    ],
                    rows=cat_rows,
                )
            )

        return SkillResult.ok(
            message=f"Compared {va.name} vs {vb.name}: {len(variances)} variances, overall {'+' if total_change > 0 else ''}{total_change:.1f}% change",
            data={
                "version_a": {"id": va.id, "name": va.name},
                "version_b": {"id": vb.id, "name": vb.name},
                "variance_count": len(variances),
                "total_change_pct": total_change,
            },
            content_blocks=content_blocks,
        )

    async def _compare_forecast_vs_actuals(
        self, db: Session, params: dict[str, Any], context: SkillContext
    ) -> SkillResult:
        """Compare a forecast version against historical actuals (accuracy check)."""
        version_id = params.get("version_id_a") or context.context_manager.get_active_version_id()
        if not version_id:
            return SkillResult.fail("No active forecast version.")

        version = db.query(ForecastVersion).filter(ForecastVersion.id == version_id).first()
        if not version:
            return SkillResult.fail(f"Version '{version_id}' not found.")

        category_filter = params.get("category_filter")
        threshold_pct = params.get("threshold_pct", 0)
        top_n = params.get("top_n", 20)

        # Get forecast results aggregated by line item
        forecast_query = (
            db.query(
                ForecastLineResult.line_item_id,
                LineItem.name,
                LineItem.category,
                func.avg(ForecastLineResult.p50).label("avg_forecast"),
            )
            .join(LineItem, LineItem.id == ForecastLineResult.line_item_id)
            .filter(ForecastLineResult.version_id == version_id)
        )
        if category_filter:
            forecast_query = forecast_query.filter(LineItem.category.ilike(f"%{category_filter}%"))
        forecast_data = forecast_query.group_by(
            ForecastLineResult.line_item_id, LineItem.name, LineItem.category
        ).all()

        # Get actuals aggregated by line item (latest dataset)
        actuals_query = (
            db.query(
                ActualsRecord.line_item_id,
                func.avg(ActualsRecord.value).label("avg_actual"),
            )
            .group_by(ActualsRecord.line_item_id)
        )
        actuals_map = {row.line_item_id: row.avg_actual for row in actuals_query.all()}

        # Compare
        variances = []
        for row in forecast_data:
            actual = actuals_map.get(row.line_item_id)
            if actual is None:
                continue

            abs_var = row.avg_forecast - actual
            pct_var = (abs_var / abs(actual) * 100) if actual != 0 else 0

            if abs(pct_var) >= threshold_pct:
                variances.append({
                    "line_item_name": row.name,
                    "category": row.category,
                    "forecast": row.avg_forecast,
                    "actual": actual,
                    "abs_variance": abs_var,
                    "pct_variance": pct_var,
                })

        variances.sort(key=lambda x: abs(x["pct_variance"]), reverse=True)
        variances = variances[:top_n]

        if not variances:
            return SkillResult.ok(
                message="No overlapping periods found between forecast and actuals for comparison.",
                data={"variance_count": 0},
            )

        rows = [
            {
                "line_item": v["line_item_name"],
                "category": v["category"],
                "forecast": self._format_value(v["forecast"]),
                "actual": self._format_value(v["actual"]),
                "variance": self._format_value(v["abs_variance"]),
                "accuracy": f"{100 - abs(v['pct_variance']):.1f}%",
            }
            for v in variances
        ]

        avg_accuracy = sum(100 - abs(v["pct_variance"]) for v in variances) / len(variances)

        content_blocks = [
            self._text_block(
                f"**Forecast vs Actuals: {version.name}**\n"
                f"Average accuracy: **{avg_accuracy:.1f}%** across {len(variances)} comparable lines"
            ),
            self._table_block(
                title="Forecast vs Actuals Comparison",
                columns=[
                    {"key": "line_item", "label": "Line Item"},
                    {"key": "category", "label": "Category"},
                    {"key": "forecast", "label": "Forecast Avg"},
                    {"key": "actual", "label": "Actual Avg"},
                    {"key": "variance", "label": "Variance"},
                    {"key": "accuracy", "label": "Accuracy"},
                ],
                rows=rows,
            ),
        ]

        return SkillResult.ok(
            message=f"Forecast vs Actuals: {avg_accuracy:.1f}% average accuracy across {len(variances)} lines",
            data={"average_accuracy": avg_accuracy, "variance_count": len(variances)},
            content_blocks=content_blocks,
        )

    def _get_aggregated_results(
        self, db: Session, version_id: str, category_filter: str | None
    ) -> dict[tuple, float]:
        """Get forecast results aggregated by line item for a version."""
        query = (
            db.query(
                ForecastLineResult.line_item_id,
                LineItem.name,
                LineItem.category,
                func.sum(ForecastLineResult.p50).label("total"),
            )
            .join(LineItem, LineItem.id == ForecastLineResult.line_item_id)
            .filter(ForecastLineResult.version_id == version_id)
        )
        if category_filter:
            query = query.filter(LineItem.category.ilike(f"%{category_filter}%"))

        results = query.group_by(
            ForecastLineResult.line_item_id, LineItem.name, LineItem.category
        ).all()

        return {
            (row.line_item_id, row.name, row.category): row.total
            for row in results
        }

    @staticmethod
    def _format_value(value: float) -> str:
        if abs(value) >= 1_000_000:
            return f"${value / 1_000_000:.1f}M"
        if abs(value) >= 1_000:
            return f"${value / 1_000:.0f}K"
        return f"${value:,.0f}"
