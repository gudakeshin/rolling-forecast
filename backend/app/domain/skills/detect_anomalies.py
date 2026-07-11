"""DetectAnomalies skill -- statistical anomaly detection for actuals and forecasts."""

import logging
from typing import Any

import numpy as np
from sqlalchemy.orm import Session

from app.domain.base_skill import BaseSkill, SkillContext, SkillResult
from app.models.forecast import ForecastLineResult
from app.models.actuals import ActualsRecord
from app.models.line_item import LineItem

logger = logging.getLogger(__name__)


class DetectAnomaliesSkill(BaseSkill):
    """Skill to detect anomalies and outliers in financial data."""

    @property
    def name(self) -> str:
        return "detect_anomalies"

    @property
    def description(self) -> str:
        return (
            "Detect anomalies and outliers in forecast data and actuals. Uses "
            "statistical methods (Z-score, IQR, trend deviation) to flag unusual "
            "values that may indicate data errors or structural changes."
        )

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "target": {
                    "type": "string",
                    "description": "What to scan: actuals, forecast, or both",
                    "enum": ["actuals", "forecast", "both"],
                    "default": "both",
                },
                "version_id": {
                    "type": "string",
                    "description": "Forecast version to analyze (uses active if not specified)",
                },
                "method": {
                    "type": "string",
                    "description": "Detection method: zscore, iqr, trend_deviation, all",
                    "enum": ["zscore", "iqr", "trend_deviation", "all"],
                    "default": "all",
                },
                "sensitivity": {
                    "type": "string",
                    "description": "Detection sensitivity: low, medium, high",
                    "enum": ["low", "medium", "high"],
                    "default": "medium",
                },
                "category": {
                    "type": "string",
                    "description": "Filter to a specific category",
                },
                "line_item_name": {
                    "type": "string",
                    "description": "Filter to a specific line item",
                },
            },
        }

    async def execute(self, params: dict[str, Any], context: SkillContext) -> SkillResult:
        db: Session = context.db
        target = params.get("target", "both")
        method = params.get("method", "all")
        sensitivity = params.get("sensitivity", "medium")

        version_id = params.get("version_id") or context.context_manager.get_active_version_id()
        if not version_id:
            return SkillResult.fail("No active forecast version. Generate a baseline first.")

        thresholds = self._get_thresholds(sensitivity)
        all_anomalies: list[dict] = []
        actor = None
        try:
            from app.services.permissions import resolve_skill_user
            actor = resolve_skill_user(context)
        except Exception:
            actor = None

        if target in ("actuals", "both"):
            all_anomalies.extend(self._scan_actuals(db, params, method, thresholds, user=actor))
        if target in ("forecast", "both"):
            all_anomalies.extend(
                self._scan_forecast(db, version_id, params, method, thresholds, user=actor)
            )

        if not all_anomalies:
            return SkillResult.ok(
                message="No anomalies detected with the current sensitivity settings.",
                content_blocks=[
                    self._text_block(
                        f"No anomalies detected using **{method}** method at **{sensitivity}** sensitivity. "
                        f"Try increasing sensitivity to 'high' for more granular detection."
                    )
                ],
            )

        # Categorize by severity
        critical = [a for a in all_anomalies if a["severity"] == "critical"]
        warning = [a for a in all_anomalies if a["severity"] == "warning"]
        info = [a for a in all_anomalies if a["severity"] == "info"]

        # Group by line item to get unique item count
        li_names = set(a["line_item"] for a in all_anomalies)
        critical_names = sorted(set(a["line_item"] for a in critical))[:5]

        # Build a concise chat summary (NOT the overwhelming table)
        summary_lines = [
            f"**Anomaly Scan Complete** — scanned {len(li_names)} line items\n",
            f"- **{len(critical)}** critical issues requiring action",
            f"- **{len(warning)}** warnings to review",
            f"- **{len(info)}** informational flags",
        ]

        if critical_names:
            summary_lines.append("\n**Top items needing attention:**")
            for name in critical_names:
                item_findings = [a for a in critical if a["line_item"] == name]
                note = item_findings[0].get("note", "") if item_findings else ""
                summary_lines.append(f"- {name}: {note}")

        summary_lines.append(
            "\nI've opened the **Anomaly Dashboard** in the side panel where you can "
            "filter by severity, category, and materiality — and take action on each item."
        )

        content_blocks = [
            self._text_block("\n".join(summary_lines)),
            {
                "type": "panel_trigger",
                "data": {
                    "panel": "anomaly_dashboard",
                    "params": {"version_id": version_id},
                    "label": "Open Anomaly Dashboard",
                },
            },
        ]

        return SkillResult.ok(
            message=f"Detected {len(all_anomalies)} anomalies across {len(li_names)} line items ({len(critical)} critical). Opening anomaly dashboard.",
            data={
                "total_anomalies": len(all_anomalies),
                "unique_line_items": len(li_names),
                "critical": len(critical),
                "warning": len(warning),
                "info": len(info),
                "sensitivity": sensitivity,
                "method": method,
            },
            content_blocks=content_blocks,
        )

    def _get_thresholds(self, sensitivity: str) -> dict:
        """Get detection thresholds based on sensitivity."""
        return {
            "low": {"zscore": 3.0, "iqr_mult": 2.0, "trend_pct": 50},
            "medium": {"zscore": 2.5, "iqr_mult": 1.5, "trend_pct": 30},
            "high": {"zscore": 2.0, "iqr_mult": 1.0, "trend_pct": 20},
        }[sensitivity]

    def _classify_severity(self, score: float, threshold: float) -> str:
        """Classify anomaly severity based on how far it exceeds the threshold."""
        ratio = score / threshold if threshold > 0 else 0
        if ratio > 2.0:
            return "critical"
        elif ratio > 1.5:
            return "warning"
        else:
            return "info"

    def _scan_actuals(
        self, db: Session, params: dict, method: str, thresholds: dict, user=None
    ) -> list[dict]:
        """Scan actuals data for anomalies."""
        anomalies = []

        # Get all line items (filtered + BU-scoped)
        from app.services.permissions import line_item_scope_filter

        query = db.query(LineItem)
        if user is not None:
            query = line_item_scope_filter(query, user, LineItem)
        category = params.get("category")
        line_item_name = params.get("line_item_name")
        if category:
            query = query.filter(LineItem.category.ilike(f"%{category}%"))
        if line_item_name:
            query = query.filter(
                (LineItem.name.ilike(f"%{line_item_name}%"))
                | (LineItem.account_code.ilike(f"%{line_item_name}%"))
            )

        line_items = query.all()

        for li in line_items:
            records = (
                db.query(ActualsRecord)
                .filter(ActualsRecord.line_item_id == li.id)
                .order_by(ActualsRecord.period)
                .all()
            )

            if len(records) < 4:
                continue

            values = np.array([r.value for r in records])
            periods = [r.period for r in records]

            if method in ("zscore", "all"):
                anomalies.extend(self._zscore_detection(
                    values, periods, li, "actuals", thresholds["zscore"]
                ))

            if method in ("iqr", "all"):
                anomalies.extend(self._iqr_detection(
                    values, periods, li, "actuals", thresholds["iqr_mult"]
                ))

            if method in ("trend_deviation", "all"):
                anomalies.extend(self._trend_detection(
                    values, periods, li, "actuals", thresholds["trend_pct"]
                ))

        return anomalies

    def _scan_forecast(
        self, db: Session, version_id: str, params: dict, method: str, thresholds: dict, user=None
    ) -> list[dict]:
        """Scan forecast data for anomalies."""
        anomalies = []

        from app.services.permissions import line_item_scope_filter

        query = (
            db.query(ForecastLineResult, LineItem)
            .join(LineItem)
            .filter(ForecastLineResult.version_id == version_id)
        )
        if user is not None:
            query = line_item_scope_filter(query, user, LineItem)
        category = params.get("category")
        line_item_name = params.get("line_item_name")
        if category:
            query = query.filter(LineItem.category.ilike(f"%{category}%"))
        if line_item_name:
            query = query.filter(
                (LineItem.name.ilike(f"%{line_item_name}%"))
                | (LineItem.account_code.ilike(f"%{line_item_name}%"))
            )

        results = query.order_by(LineItem.id, ForecastLineResult.period).all()

        # Group by line item
        grouped: dict[int, tuple] = {}
        for r, li in results:
            if li.id not in grouped:
                grouped[li.id] = (li, [], [])
            grouped[li.id][1].append(float(r.p50))
            grouped[li.id][2].append(r.period)

        for li_id, (li, values_list, periods) in grouped.items():
            if len(values_list) < 3:
                continue

            values = np.array(values_list)

            # Also compare forecast to actuals for jump detection
            actuals = (
                db.query(ActualsRecord)
                .filter(ActualsRecord.line_item_id == li_id)
                .order_by(ActualsRecord.period.desc())
                .limit(6)
                .all()
            )

            if actuals:
                last_actual = actuals[0].value
                first_forecast = values[0]
                if last_actual != 0:
                    jump_pct = abs((first_forecast - last_actual) / last_actual) * 100
                    if jump_pct > thresholds["trend_pct"]:
                        severity = self._classify_severity(jump_pct, thresholds["trend_pct"])
                        anomalies.append({
                            "line_item": li.name,
                            "category": li.category,
                            "period": periods[0],
                            "value": first_forecast,
                            "source": "forecast",
                            "method": "actuals_jump",
                            "severity": severity,
                            "score": jump_pct,
                            "note": f"Forecast jumps {jump_pct:.0f}% from last actual (${last_actual:,.0f})",
                        })

            if method in ("zscore", "all"):
                anomalies.extend(self._zscore_detection(
                    values, periods, li, "forecast", thresholds["zscore"]
                ))

            if method in ("iqr", "all"):
                anomalies.extend(self._iqr_detection(
                    values, periods, li, "forecast", thresholds["iqr_mult"]
                ))

        return anomalies

    def _zscore_detection(
        self, values: np.ndarray, periods: list, li: LineItem, source: str, threshold: float
    ) -> list[dict]:
        """Detect anomalies using Z-score method."""
        anomalies = []
        if len(values) < 3:
            return anomalies

        mean = np.mean(values)
        std = np.std(values)
        if std == 0:
            return anomalies

        zscores = np.abs((values - mean) / std)

        for i, z in enumerate(zscores):
            if z > threshold:
                severity = self._classify_severity(z, threshold)
                anomalies.append({
                    "line_item": li.name,
                    "category": li.category,
                    "period": periods[i],
                    "value": float(values[i]),
                    "source": source,
                    "method": "zscore",
                    "severity": severity,
                    "score": float(z),
                    "note": f"Z-score: {z:.1f} (threshold: {threshold})",
                })

        return anomalies

    def _iqr_detection(
        self, values: np.ndarray, periods: list, li: LineItem, source: str, multiplier: float
    ) -> list[dict]:
        """Detect anomalies using IQR method."""
        anomalies = []
        if len(values) < 4:
            return anomalies

        q1 = np.percentile(values, 25)
        q3 = np.percentile(values, 75)
        iqr = q3 - q1
        if iqr == 0:
            return anomalies

        lower = q1 - multiplier * iqr
        upper = q3 + multiplier * iqr

        for i, v in enumerate(values):
            if v < lower or v > upper:
                deviation = max(abs(v - lower), abs(v - upper)) / iqr
                severity = self._classify_severity(deviation, 1.0)
                direction = "below" if v < lower else "above"
                anomalies.append({
                    "line_item": li.name,
                    "category": li.category,
                    "period": periods[i],
                    "value": float(v),
                    "source": source,
                    "method": "iqr",
                    "severity": severity,
                    "score": float(deviation),
                    "note": f"Value {direction} {multiplier}x IQR bounds [${lower:,.0f}, ${upper:,.0f}]",
                })

        return anomalies

    def _trend_detection(
        self, values: np.ndarray, periods: list, li: LineItem, source: str, pct_threshold: float
    ) -> list[dict]:
        """Detect anomalies as large deviations from the trend line."""
        anomalies = []
        if len(values) < 4:
            return anomalies

        # Fit a simple linear trend
        x = np.arange(len(values))
        try:
            coeffs = np.polyfit(x, values, 1)
            trend = np.polyval(coeffs, x)
        except Exception:
            return anomalies

        for i, (actual, predicted) in enumerate(zip(values, trend)):
            if abs(predicted) < 1e-10:
                continue
            deviation_pct = abs(actual - predicted) / abs(predicted) * 100
            if deviation_pct > pct_threshold:
                severity = self._classify_severity(deviation_pct, pct_threshold)
                anomalies.append({
                    "line_item": li.name,
                    "category": li.category,
                    "period": periods[i],
                    "value": float(actual),
                    "source": source,
                    "method": "trend_deviation",
                    "severity": severity,
                    "score": float(deviation_pct),
                    "note": f"Deviates {deviation_pct:.0f}% from trend (expected ${predicted:,.0f})",
                })

        return anomalies
