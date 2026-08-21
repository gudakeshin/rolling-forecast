"""AutoAccuracyReport skill -- forecast accuracy analysis and bias detection."""

import logging
from typing import Any

import numpy as np
from sqlalchemy.orm import Session

from app.domain.base_skill import BaseSkill, SkillContext, SkillResult
from app.models.forecast import ForecastVersion
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
        """Horizon-bucketed MAPE/bias from forecast_accuracy_records + interval calibration."""
        from app.models.fx import ForecastAccuracyRecord
        from app.models.line_item import LineItem

        top_n = params.get("top_n", 10)
        category_filter = params.get("category")

        q = (
            db.query(ForecastAccuracyRecord, LineItem)
            .join(LineItem, LineItem.id == ForecastAccuracyRecord.line_item_id)
            .filter(ForecastAccuracyRecord.version_id == version.id)
        )
        if category_filter:
            q = q.filter(LineItem.category == category_filter)
        rows = q.all()

        if not rows:
            return SkillResult.ok(
                message=(
                    "No vintage accuracy records yet for this version. "
                    "Ingest actuals covering forecast periods to populate them."
                ),
                data={"avg_mape": None, "records": 0},
                content_blocks=[
                    self._text_block(
                        "No period-aligned accuracy records found. "
                        "Upload actuals for forecasted periods to enable vintage tracking."
                    )
                ],
            )

        # By horizon bucket
        buckets: dict[str, list[ForecastAccuracyRecord]] = {
            "1m": [], "2-3m": [], "4-6m": [], "7m+": [],
        }
        by_item: dict[int, list[ForecastAccuracyRecord]] = {}
        for rec, li in rows:
            by_item.setdefault(rec.line_item_id, []).append(rec)
            h = rec.horizon_offset
            if h <= 1:
                buckets["1m"].append(rec)
            elif h <= 3:
                buckets["2-3m"].append(rec)
            elif h <= 6:
                buckets["4-6m"].append(rec)
            else:
                buckets["7m+"].append(rec)

        def _mape(recs: list) -> float | None:
            pcts = [r.pct_error for r in recs if r.pct_error is not None]
            return float(sum(pcts) / len(pcts)) if pcts else None

        def _coverage(recs: list) -> float | None:
            flagged = [r for r in recs if r.within_p10_p90 is not None]
            if not flagged:
                return None
            return sum(1 for r in flagged if r.within_p10_p90) / len(flagged) * 100

        horizon_rows = []
        for label, recs in buckets.items():
            if not recs:
                continue
            mape = _mape(recs)
            cov = _coverage(recs)
            note = ""
            if cov is not None and cov < 70:
                note = "intervals too narrow"
            elif cov is not None and cov > 95:
                note = "intervals too wide"
            horizon_rows.append({
                "horizon": label,
                "n": str(len(recs)),
                "mape": f"{mape:.1f}%" if mape is not None else "n/a",
                "p10_p90_coverage": f"{cov:.0f}%" if cov is not None else "n/a",
                "calibration": note or "ok",
            })

        # Per line item
        item_rows = []
        for lid, recs in by_item.items():
            li = next(li for r, li in rows if r.line_item_id == lid)
            mape = _mape(recs)
            bias = float(sum(r.predicted_p50 - r.actual for r in recs) / len(recs))
            item_rows.append({
                "name": li.name,
                "category": li.category,
                "n": len(recs),
                "mape": mape or 999,
                "bias": bias,
                "mape_display": f"{mape:.1f}%" if mape is not None else "n/a",
                "bias_display": f"{bias:+,.0f}",
            })
        item_rows.sort(key=lambda x: x["mape"])

        avg_mape = _mape([r for r, _ in rows])
        overall_cov = _coverage([r for r, _ in rows])

        summary_text = (
            f"**Vintage accuracy for {version.name}** — {len(rows)} period observations. "
            f"Overall MAPE: **{avg_mape:.1f}%**"
            if avg_mape is not None
            else f"**Vintage accuracy for {version.name}** — {len(rows)} observations."
        )
        content_blocks = [
            self._text_block(summary_text),
            self._table_block(
                title="Accuracy by Horizon",
                columns=[
                    {"key": "horizon", "label": "Horizon"},
                    {"key": "n", "label": "N"},
                    {"key": "mape", "label": "MAPE"},
                    {"key": "p10_p90_coverage", "label": "P10–P90 Coverage"},
                    {"key": "calibration", "label": "Calibration"},
                ],
                rows=horizon_rows,
            ),
            self._table_block(
                title=f"Most Accurate (top {top_n})",
                columns=[
                    {"key": "name", "label": "Line Item"},
                    {"key": "category", "label": "Category"},
                    {"key": "mape_display", "label": "MAPE"},
                    {"key": "bias_display", "label": "Bias"},
                ],
                rows=[{k: v for k, v in r.items() if k not in ("mape", "bias", "n")} for r in item_rows[:top_n]],
            ),
            self._table_block(
                title=f"Least Accurate (bottom {top_n})",
                columns=[
                    {"key": "name", "label": "Line Item"},
                    {"key": "category", "label": "Category"},
                    {"key": "mape_display", "label": "MAPE"},
                    {"key": "bias_display", "label": "Bias"},
                ],
                rows=[{k: v for k, v in r.items() if k not in ("mape", "bias", "n")} for r in item_rows[-top_n:][::-1]],
            ),
        ]

        msg = (
            f"Accuracy summary: MAPE={avg_mape:.1f}%, coverage={overall_cov:.0f}%"
            if avg_mape is not None and overall_cov is not None
            else "Accuracy summary generated"
        )
        return SkillResult.ok(
            message=msg,
            data={
                "avg_mape": avg_mape,
                "interval_coverage": overall_cov,
                "records": len(rows),
                "by_horizon": horizon_rows,
            },
            content_blocks=content_blocks,
            panel_payload={
                "panel": "accuracy_tracking",
                "params": {"version_id": version.id},
                "title": "Accuracy Tracking",
            },
        )

    async def _bias_analysis(
        self, db: Session, version: ForecastVersion, params: dict
    ) -> SkillResult:
        """Detect systematic over/under-forecasting from vintage accuracy records."""
        from app.models.fx import ForecastAccuracyRecord

        category_filter = params.get("category")

        q = (
            db.query(ForecastAccuracyRecord, LineItem)
            .join(LineItem, LineItem.id == ForecastAccuracyRecord.line_item_id)
            .filter(ForecastAccuracyRecord.version_id == version.id)
        )
        if category_filter:
            q = q.filter(LineItem.category.ilike(f"%{category_filter}%"))
        rows = q.all()

        if not rows:
            return SkillResult.ok(
                message="No vintage accuracy records for bias analysis.",
                content_blocks=[
                    self._text_block(
                        "No period-aligned accuracy records found. "
                        "Ingest actuals covering forecast periods first."
                    )
                ],
            )

        line_biases: dict[int, dict] = {}
        for rec, li in rows:
            if rec.actual == 0:
                continue
            if rec.line_item_id not in line_biases:
                line_biases[rec.line_item_id] = {
                    "name": li.name,
                    "category": li.category,
                    "sum_bias": 0.0,
                    "sum_abs_error": 0.0,
                    "count": 0,
                }
            pct = rec.pct_error
            if pct is None:
                pct = abs(rec.predicted_p50 - rec.actual) / abs(rec.actual) * 100
            signed = (rec.predicted_p50 - rec.actual) / abs(rec.actual) * 100
            line_biases[rec.line_item_id]["sum_bias"] += signed
            line_biases[rec.line_item_id]["sum_abs_error"] += float(pct)
            line_biases[rec.line_item_id]["count"] += 1

        if not line_biases:
            return SkillResult.ok(
                message="No comparable data for bias analysis.",
                content_blocks=[self._text_block("Insufficient overlapping data for bias analysis.")],
            )

        bias_items = []
        for _li_id, data in line_biases.items():
            avg_bias = data["sum_bias"] / data["count"]
            avg_mape = data["sum_abs_error"] / data["count"]
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

        bias_items.sort(key=lambda x: abs(x["avg_bias"]), reverse=True)

        over_count = sum(1 for b in bias_items if b["bias_type"] == "Over-forecasting")
        under_count = sum(1 for b in bias_items if b["bias_type"] == "Under-forecasting")

        table_rows = [
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
                f"**Bias Analysis (vintage): {version.name}**\n"
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
                rows=table_rows,
            ),
        ]

        return SkillResult.ok(
            message=f"Bias analysis: {over_count} over-forecasting, {under_count} under-forecasting items",
            data={
                "over_forecasting": over_count,
                "under_forecasting": under_count,
                "neutral": len(bias_items) - over_count - under_count,
                "source": "forecast_accuracy_records",
            },
            content_blocks=content_blocks,
        )

    async def _trend_over_time(
        self, db: Session, version: ForecastVersion, params: dict
    ) -> SkillResult:
        """Show accuracy evolution across forecast versions using vintage records."""
        from app.models.fx import ForecastAccuracyRecord

        versions = (
            db.query(ForecastVersion)
            .order_by(ForecastVersion.created_at)
            .limit(10)
            .all()
        )

        if len(versions) < 2:
            return SkillResult.ok(
                message="Need at least 2 forecast versions to show trends.",
                content_blocks=[
                    self._text_block(
                        "Create more forecast versions to track accuracy trends over time."
                    )
                ],
            )

        version_metrics: list[dict[str, Any]] = []
        for v in versions:
            recs = (
                db.query(ForecastAccuracyRecord)
                .filter(ForecastAccuracyRecord.version_id == v.id)
                .all()
            )
            pcts = [r.pct_error for r in recs if r.pct_error is not None]
            avg_mape = float(np.mean(pcts)) if pcts else None
            version_metrics.append({
                "version": v.name,
                "created": v.created_at.strftime("%Y-%m-%d") if v.created_at else "N/A",
                "lines": len(pcts),
                "avg_mape": avg_mape,
            })

        table_rows = [
            {
                "version": m["version"],
                "date": m["created"],
                "lines": str(m["lines"]),
                "avg_mape": f"{m['avg_mape']:.1f}%" if m["avg_mape"] is not None else "N/A",
                "trend": "",
            }
            for m in version_metrics
        ]

        for i in range(1, len(table_rows)):
            prev = version_metrics[i - 1]["avg_mape"]
            curr = version_metrics[i]["avg_mape"]
            if prev is not None and curr is not None:
                if curr < prev:
                    table_rows[i]["trend"] = "Improving"
                elif curr > prev:
                    table_rows[i]["trend"] = "Declining"
                else:
                    table_rows[i]["trend"] = "Stable"

        content_blocks = [
            self._text_block(
                f"**Accuracy Trend (vintage)** across {len(versions)} forecast versions"
            ),
            self._table_block(
                title="MAPE Over Time",
                columns=[
                    {"key": "version", "label": "Version"},
                    {"key": "date", "label": "Date"},
                    {"key": "lines", "label": "Comparable Lines"},
                    {"key": "avg_mape", "label": "Avg MAPE"},
                    {"key": "trend", "label": "Trend"},
                ],
                rows=table_rows,
            ),
        ]

        return SkillResult.ok(
            message=f"Accuracy trend across {len(versions)} versions",
            data={
                "versions_analyzed": len(versions),
                "metrics": version_metrics,
                "source": "forecast_accuracy_records",
            },
            content_blocks=content_blocks,
        )

    async def _line_item_detail(
        self, db: Session, version: ForecastVersion, params: dict
    ) -> SkillResult:
        """Deep dive on a specific line item's vintage accuracy."""
        from app.models.fx import ForecastAccuracyRecord

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

        recs = (
            db.query(ForecastAccuracyRecord)
            .filter(
                ForecastAccuracyRecord.version_id == version.id,
                ForecastAccuracyRecord.line_item_id == li.id,
            )
            .order_by(ForecastAccuracyRecord.period)
            .all()
        )

        if not recs:
            return SkillResult.ok(
                message=f"No vintage accuracy records for {li.name}.",
                content_blocks=[
                    self._text_block(
                        f"No period-aligned accuracy records for **{li.name}**. "
                        "Ingest actuals for forecasted periods to populate them."
                    )
                ],
            )

        table_rows = []
        errors = []
        for rec in recs:
            if rec.actual != 0:
                error = (rec.predicted_p50 - rec.actual) / abs(rec.actual) * 100
                abs_error = abs(error)
                errors.append(abs_error)
                table_rows.append({
                    "period": rec.period,
                    "horizon": str(rec.horizon_offset),
                    "forecast": f"${rec.predicted_p50:,.0f}",
                    "actual": f"${rec.actual:,.0f}",
                    "error": f"{'+' if error > 0 else ''}{error:.1f}%",
                    "in_band": (
                        "yes" if rec.within_p10_p90 is True
                        else "no" if rec.within_p10_p90 is False
                        else "n/a"
                    ),
                })
            else:
                table_rows.append({
                    "period": rec.period,
                    "horizon": str(rec.horizon_offset),
                    "forecast": f"${rec.predicted_p50:,.0f}",
                    "actual": f"${rec.actual:,.0f}",
                    "error": "N/A",
                    "in_band": "n/a",
                })

        avg_mape = float(np.mean(errors)) if errors else 0.0
        avg_accuracy = 100 - avg_mape if errors else 0.0
        model = next((r.model_type for r in recs if r.model_type), "N/A")

        content_blocks = [
            self._text_block(
                f"**Accuracy Detail (vintage): {li.name}** ({li.account_code})\n"
                f"Category: {li.category} | Model: {model}\n"
                f"Average MAPE: **{avg_mape:.1f}%** | Average Accuracy: **{avg_accuracy:.1f}%**"
            ),
            self._table_block(
                title="Period Detail",
                columns=[
                    {"key": "period", "label": "Period"},
                    {"key": "horizon", "label": "Horizon"},
                    {"key": "forecast", "label": "Forecast"},
                    {"key": "actual", "label": "Actual"},
                    {"key": "error", "label": "Error"},
                    {"key": "in_band", "label": "In P10–P90"},
                ],
                rows=table_rows,
            ),
        ]

        return SkillResult.ok(
            message=f"Detail for {li.name}: MAPE={avg_mape:.1f}%",
            data={
                "line_item": li.name,
                "avg_mape": avg_mape,
                "periods": len(recs),
                "source": "forecast_accuracy_records",
            },
            content_blocks=content_blocks,
        )
