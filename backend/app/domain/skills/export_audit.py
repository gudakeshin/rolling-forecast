"""ExportAudit skill -- generate SOX-compliant audit trail exports."""

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.domain.base_skill import BaseSkill, SkillContext, SkillResult
from app.models.forecast import ForecastVersion, ForecastLineResult, ModelMetadata
from app.models.line_item import LineItem
from app.models.override import Override
from app.models.actuals import ActualsDataset
from app.models.user import User

logger = logging.getLogger(__name__)


class ExportAuditSkill(BaseSkill):
    """Skill to generate audit trail exports for SOX compliance."""

    @property
    def name(self) -> str:
        return "export_audit"

    @property
    def description(self) -> str:
        return (
            "Generate a comprehensive audit trail export for a forecast version. "
            "Includes full lineage: data source, model parameters, override history, "
            "approval workflow, and version metadata. Suitable for SOX audit compliance. "
            "Can export as JSON report or CSV summary. "
            "Use when the user asks for an audit trail, compliance report, or export."
        )

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "version_id": {
                    "type": "string",
                    "description": "Forecast version to audit (uses active version if not specified)",
                },
                "format": {
                    "type": "string",
                    "description": "Export format: summary (chat display), json (full structured), csv (tabular)",
                    "enum": ["summary", "json", "csv"],
                    "default": "summary",
                },
                "include_model_details": {
                    "type": "boolean",
                    "description": "Include detailed model parameters and diagnostics",
                    "default": True,
                },
            },
        }

    async def execute(self, params: dict[str, Any], context: SkillContext) -> SkillResult:
        db: Session = context.db
        export_format = params.get("format", "summary")

        version_id = params.get("version_id") or context.context_manager.get_active_version_id()
        if not version_id:
            return SkillResult.fail("No active forecast version.")

        version = db.query(ForecastVersion).filter(ForecastVersion.id == version_id).first()
        if not version:
            return SkillResult.fail(f"Version '{version_id}' not found.")

        # Build the audit report
        report = self._build_audit_report(db, version, params.get("include_model_details", True))

        if export_format == "json":
            return self._json_response(report, version)
        elif export_format == "csv":
            return self._csv_response(report, version)
        else:
            return self._summary_response(report, version)

    def _build_audit_report(
        self, db: Session, version: ForecastVersion, include_model_details: bool
    ) -> dict[str, Any]:
        """Build the full audit trail."""
        report: dict[str, Any] = {
            "report_generated_at": datetime.now(timezone.utc).isoformat(),
            "report_type": "SOX Forecast Audit Trail",
        }

        # 1. Version metadata
        creator = db.query(User).filter(User.id == version.created_by).first() if version.created_by else None
        approver_name = None
        if version.approved_by:
            approver = db.query(User).filter(User.id == version.approved_by).first()
            approver_name = approver.full_name if approver else version.approved_by

        report["version"] = {
            "id": version.id,
            "name": version.name,
            "status": version.status,
            "version_type": version.version_type,
            "created_at": version.created_at.isoformat() if version.created_at else None,
            "created_by": creator.full_name if creator else version.created_by,
            "approved_at": version.approved_at.isoformat() if version.approved_at else None,
            "approved_by": approver_name,
            "published_at": version.published_at.isoformat() if version.published_at else None,
            "horizon_months": version.horizon_months,
            "base_period": version.base_period,
            "random_seed": version.random_seed,
        }

        # 2. Data lineage
        dataset = (
            db.query(ActualsDataset).filter(ActualsDataset.id == version.actuals_dataset_id).first()
            if version.actuals_dataset_id else None
        )
        report["data_lineage"] = {
            "actuals_dataset_id": version.actuals_dataset_id,
            "actuals_hash": version.actuals_hash,
            "input_hash": version.input_hash,
            "source_name": dataset.source_name if dataset else None,
            "source_type": dataset.source_type if dataset else None,
            "period_range": f"{dataset.period_start} to {dataset.period_end}" if dataset else None,
            "row_count": dataset.row_count if dataset else None,
            "completeness_pct": dataset.completeness_pct if dataset else None,
        }

        # 3. Model configuration
        report["model_configuration"] = {
            "model_versions": version.model_versions,
            "generation_time_seconds": version.generation_time_seconds,
            "total_line_items": version.total_line_items,
        }

        if include_model_details:
            model_details = []
            line_results = (
                db.query(ForecastLineResult)
                .filter(ForecastLineResult.version_id == version.id)
                .all()
            )
            seen_items = set()
            for r in line_results:
                if r.line_item_id in seen_items:
                    continue
                seen_items.add(r.line_item_id)

                li = db.query(LineItem).filter(LineItem.id == r.line_item_id).first()
                meta = (
                    db.query(ModelMetadata)
                    .filter(ModelMetadata.line_result_id == r.id)
                    .first()
                )

                detail = {
                    "line_item": li.name if li else "Unknown",
                    "account_code": li.account_code if li else "N/A",
                    "model_type": r.model_type,
                    "mape": r.model_mape,
                    "r_squared": r.model_r_squared,
                    "confidence_score": r.confidence_score,
                    "confidence_level": r.confidence_level,
                    "is_overridden": r.is_overridden,
                }
                if meta:
                    detail.update({
                        "training_window": f"{meta.training_window_start} to {meta.training_window_end}",
                        "training_points": meta.training_points,
                        "seasonality_detected": meta.seasonality_detected,
                        "structural_break": meta.structural_break_detected,
                        "random_seed": meta.random_seed,
                    })
                model_details.append(detail)

            report["model_details"] = model_details

        # 4. Override history
        overrides = (
            db.query(Override)
            .filter(Override.version_id == version.id)
            .order_by(Override.created_at)
            .all()
        )
        override_records = []
        for o in overrides:
            li = db.query(LineItem).filter(LineItem.id == o.line_item_id).first()
            user = db.query(User).filter(User.id == o.user_id).first()
            override_records.append({
                "id": o.id,
                "line_item": li.name if li else "Unknown",
                "period": o.period,
                "original_model_value": o.original_model_value,
                "override_value": o.override_value,
                "reason": o.reason,
                "status": o.status,
                "carry_forward": o.carry_forward,
                "user": user.full_name if user else o.user_id,
                "created_at": o.created_at.isoformat() if o.created_at else None,
                "reverted_at": o.reverted_at.isoformat() if o.reverted_at else None,
                "downstream_recalc_count": o.downstream_recalc_count,
            })

        report["overrides"] = {
            "total_count": len(overrides),
            "active_count": sum(1 for o in overrides if o.status == "active"),
            "reverted_count": sum(1 for o in overrides if o.status == "reverted"),
            "records": override_records,
        }

        # 5. Summary stats
        report["summary"] = {
            "total_line_items": version.total_line_items,
            "high_confidence": version.high_confidence_count,
            "medium_confidence": version.medium_confidence_count,
            "low_confidence": version.low_confidence_count,
            "override_count": version.override_count,
        }

        return report

    def _summary_response(self, report: dict, version: ForecastVersion) -> SkillResult:
        """Build a chat-friendly summary."""
        v = report["version"]
        d = report["data_lineage"]
        o = report["overrides"]
        s = report["summary"]

        content_blocks = [
            self._text_block(f"**Audit Trail: {version.name}**"),
            self._table_block(
                title="Version Metadata",
                columns=[
                    {"key": "field", "label": "Field"},
                    {"key": "value", "label": "Value"},
                ],
                rows=[
                    {"field": "Version ID", "value": v["id"][:12] + "..."},
                    {"field": "Status", "value": v["status"]},
                    {"field": "Created", "value": v["created_at"][:19] if v["created_at"] else "N/A"},
                    {"field": "Created By", "value": str(v["created_by"] or "N/A")},
                    {"field": "Approved", "value": v["approved_at"][:19] if v["approved_at"] else "Pending"},
                    {"field": "Approved By", "value": str(v["approved_by"] or "N/A")},
                    {"field": "Random Seed", "value": str(v["random_seed"])},
                ],
            ),
            self._table_block(
                title="Data Lineage",
                columns=[
                    {"key": "field", "label": "Field"},
                    {"key": "value", "label": "Value"},
                ],
                rows=[
                    {"field": "Source", "value": str(d["source_name"] or "N/A")},
                    {"field": "Data Hash", "value": (d["actuals_hash"] or "N/A")[:16] + "..."},
                    {"field": "Period Range", "value": str(d["period_range"] or "N/A")},
                    {"field": "Completeness", "value": f"{d['completeness_pct']}%" if d["completeness_pct"] else "N/A"},
                ],
            ),
            self._table_block(
                title="Confidence & Overrides",
                columns=[
                    {"key": "metric", "label": "Metric"},
                    {"key": "value", "label": "Count"},
                ],
                rows=[
                    {"metric": "Total Line Items", "value": str(s["total_line_items"])},
                    {"metric": "High Confidence", "value": str(s["high_confidence"])},
                    {"metric": "Medium Confidence", "value": str(s["medium_confidence"])},
                    {"metric": "Low Confidence", "value": str(s["low_confidence"])},
                    {"metric": "Active Overrides", "value": str(o["active_count"])},
                    {"metric": "Reverted Overrides", "value": str(o["reverted_count"])},
                ],
            ),
        ]

        if o["active_count"] > 0:
            override_rows = [
                {
                    "line": r["line_item"],
                    "period": r["period"],
                    "change": f"${r['original_model_value']:,.0f} → ${r['override_value']:,.0f}",
                    "user": r["user"],
                    "reason": r["reason"][:40] + ("..." if len(r["reason"]) > 40 else ""),
                }
                for r in o["records"][:10]
                if r["status"] == "active"
            ]
            content_blocks.append(
                self._table_block(
                    title="Override Audit Log",
                    columns=[
                        {"key": "line", "label": "Line"},
                        {"key": "period", "label": "Period"},
                        {"key": "change", "label": "Change"},
                        {"key": "user", "label": "User"},
                        {"key": "reason", "label": "Reason"},
                    ],
                    rows=override_rows,
                )
            )

        return SkillResult.ok(
            message=f"Audit trail for {version.name}: {s['total_line_items']} lines, {o['total_count']} overrides",
            data=report,
            content_blocks=content_blocks,
        )

    def _json_response(self, report: dict, version: ForecastVersion) -> SkillResult:
        """Return full JSON audit report."""
        return SkillResult.ok(
            message=f"Full JSON audit trail generated for {version.name}",
            data=report,
            content_blocks=[
                self._text_block(
                    f"**Audit Trail Export (JSON)** for **{version.name}**\n"
                    f"Report contains {len(report.get('model_details', []))} model details "
                    f"and {report['overrides']['total_count']} override records."
                ),
                self._text_block(
                    "The full JSON report has been generated and is available in the response data. "
                    "This can be downloaded or forwarded to Internal Audit for SOX validation."
                ),
            ],
        )

    def _csv_response(self, report: dict, version: ForecastVersion) -> SkillResult:
        """Return CSV-formatted audit data."""
        # We can't actually return a file from a skill, but we can format key data
        model_details = report.get("model_details", [])
        rows = []
        for d in model_details:
            rows.append({
                "line_item": d["line_item"],
                "account_code": d["account_code"],
                "model": d["model_type"],
                "mape": str(round(d["mape"], 2)) if d["mape"] else "N/A",
                "r_squared": str(round(d["r_squared"], 3)) if d["r_squared"] else "N/A",
                "confidence": str(round(d["confidence_score"], 1)),
                "level": d["confidence_level"],
                "overridden": "Yes" if d["is_overridden"] else "No",
            })

        content_blocks = [
            self._text_block(
                f"**Audit Trail (CSV format)** for **{version.name}** — {len(rows)} line items"
            ),
            self._table_block(
                title="Model Audit Detail",
                columns=[
                    {"key": "line_item", "label": "Line Item"},
                    {"key": "account_code", "label": "Code"},
                    {"key": "model", "label": "Model"},
                    {"key": "mape", "label": "MAPE"},
                    {"key": "r_squared", "label": "R²"},
                    {"key": "confidence", "label": "Score"},
                    {"key": "level", "label": "Level"},
                    {"key": "overridden", "label": "Override"},
                ],
                rows=rows[:30],
            ),
        ]

        if len(rows) > 30:
            content_blocks.append(
                self._text_block(f"_Showing 30 of {len(rows)} rows. Use JSON format for full export._")
            )

        return SkillResult.ok(
            message=f"CSV audit trail: {len(rows)} line items for {version.name}",
            data=report,
            content_blocks=content_blocks,
        )
