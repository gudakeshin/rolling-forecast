"""ReviewForecast skill -- RBAC-aware approval/rejection workflow with AI triage.

This skill provides the chat-based review workflow:
- ai_triage: AI analyzes all items, summarizes what needs attention, opens review panel
- submit_for_review: Move forecast from draft to in_review status
- approve / reject: Version-level approval (RBAC enforced)
- review_queue: Show items needing human attention (concise, grouped by line item)
- auto_approve_check: Check if the forecast qualifies for auto-approval
"""

import logging
from typing import Any
from datetime import datetime, timezone

import numpy as np
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.domain.base_skill import BaseSkill, SkillContext, SkillResult
from app.models.forecast import ForecastVersion, ForecastLineResult
from app.models.line_item import LineItem
from app.models.actuals import ActualsRecord
from app.models.override import Override

logger = logging.getLogger(__name__)


class ReviewForecastSkill(BaseSkill):
    """Skill to review, approve, or reject forecast versions with AI-powered triage."""

    @property
    def name(self) -> str:
        return "review_forecast"

    @property
    def description(self) -> str:
        return (
            "Review and manage the forecast approval workflow. "
            "Use 'ai_triage' to run an AI analysis that identifies which items "
            "can be auto-approved vs which need human attention. "
            "Use 'submit_for_review' to move a forecast into the review process. "
            "Use 'approve' or 'reject' for version-level actions (requires manager/admin). "
            "Use 'review_queue' to see a concise summary of items needing review. "
            "When the user asks to review a forecast, ALWAYS use 'ai_triage' first."
        )

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "description": "Action to perform",
                    "enum": [
                        "ai_triage",
                        "submit_for_review",
                        "approve",
                        "reject",
                        "review_queue",
                        "auto_approve_check",
                    ],
                },
                "version_id": {
                    "type": "string",
                    "description": "Forecast version ID (uses active version if not specified)",
                },
                "comments": {
                    "type": "string",
                    "description": "Review comments (required for rejection)",
                },
                "publish": {
                    "type": "boolean",
                    "description": "Also publish the forecast after approval",
                    "default": False,
                },
            },
            "required": ["action"],
        }

    @property
    def required_role(self) -> str:
        return "generate"

    async def execute(self, params: dict[str, Any], context: SkillContext) -> SkillResult:
        db: Session = context.db
        action = params.get("action", "ai_triage")

        if action == "ai_triage":
            return await self._ai_triage(db, params, context)
        elif action == "submit_for_review":
            return await self._submit_for_review(db, params, context)
        elif action == "approve":
            return await self._approve(db, params, context)
        elif action == "reject":
            return await self._reject(db, params, context)
        elif action == "review_queue":
            return await self._review_queue(db, params, context)
        elif action == "auto_approve_check":
            return await self._auto_approve_check(db, params, context)
        else:
            return SkillResult.fail(f"Unknown action: {action}")

    # ──────────────────────────────────────────────
    # AI Triage — the key new action
    # ──────────────────────────────────────────────

    async def _ai_triage(
        self, db: Session, params: dict[str, Any], context: SkillContext
    ) -> SkillResult:
        """Run AI analysis across all line items and provide an executive summary."""
        version_id = params.get("version_id") or context.context_manager.get_active_version_id()
        if not version_id:
            return SkillResult.fail("No active forecast version to review.")

        version = db.query(ForecastVersion).filter(ForecastVersion.id == version_id).first()
        if not version:
            return SkillResult.fail(f"Version '{version_id}' not found.")

        results = (
            db.query(ForecastLineResult)
            .join(LineItem)
            .filter(ForecastLineResult.version_id == version_id)
            .all()
        )

        if not results:
            return SkillResult.fail("No forecast results to review.")

        # Build actuals lookup
        actuals_map: dict[tuple[int, str], float] = {}
        if version.actuals_dataset_id:
            actuals = (
                db.query(ActualsRecord)
                .filter(ActualsRecord.dataset_id == version.actuals_dataset_id)
                .all()
            )
            for a in actuals:
                actuals_map[(a.line_item_id, a.period)] = a.value

        # Build category statistics
        cat_values: dict[str, list[float]] = {}
        for r in results:
            cat = r.line_item.category if r.line_item else "Unknown"
            cat_values.setdefault(cat, []).append(r.p50)
        category_stats = {}
        for cat, vals in cat_values.items():
            arr = np.array(vals)
            category_stats[cat] = {
                "mean_p50": float(np.mean(arr)),
                "std_p50": float(np.std(arr)) if len(arr) > 1 else 0,
            }

        # Group by line item
        groups: dict[int, list[ForecastLineResult]] = {}
        for r in results:
            groups.setdefault(r.line_item_id, []).append(r)

        approve_items = []
        review_items = []
        flag_items = []

        for li_id, group in groups.items():
            worst = min(group, key=lambda x: x.confidence_score)
            analysis = self._analyze_item(worst, actuals_map, category_stats)

            # Persist
            for r in group:
                r.ai_recommendation = analysis["recommendation"]
                r.ai_reasoning = analysis["reasoning"]
                r.ai_risk_score = analysis["risk_score"]

            li = worst.line_item
            actions = analysis.get("actions", [])
            top_action = actions[0]["label"] if actions else "Review"
            item_info = {
                "name": li.name if li else "Unknown",
                "category": li.category if li else "?",
                "periods": len(group),
                "confidence": round(worst.confidence_score, 0),
                "risk": round(analysis["risk_score"], 0),
                "recommendation": analysis["recommendation"],
                "reasoning": analysis["reasoning"],
                "next_step": top_action,
                "materiality": analysis.get("materiality", "low"),
            }

            if analysis["recommendation"] == "approve":
                approve_items.append(item_info)
            elif analysis["recommendation"] in ("flag", "override", "manual_input"):
                flag_items.append(item_info)
            else:
                review_items.append(item_info)

        db.commit()

        # Build concise response
        total = len(groups)
        content_blocks = []

        # Executive summary
        summary_text = (
            f"**AI Review Analysis for {version.name}** — {total} line items analyzed\n\n"
            f"- **{len(approve_items)} items can be auto-approved** — all quality checks passed\n"
            f"- **{len(review_items)} items need human review** — moderate concerns found\n"
            f"- **{len(flag_items)} items are flagged** — significant issues detected"
        )
        content_blocks.append(self._text_block(summary_text))

        # Flagged items table (the ones that actually matter)
        if flag_items:
            flag_items.sort(key=lambda x: -x["risk"])
            content_blocks.append(
                self._table_block(
                    title=f"Flagged Items ({len(flag_items)}) — Action Required",
                    columns=[
                        {"key": "name", "label": "Line Item"},
                        {"key": "category", "label": "Category"},
                        {"key": "materiality", "label": "Impact"},
                        {"key": "risk", "label": "Risk"},
                        {"key": "next_step", "label": "Next Step"},
                        {"key": "reasoning", "label": "AI Finding"},
                    ],
                    rows=flag_items[:10],
                )
            )

        # Review items table (concise — only top concerns)
        if review_items:
            review_items.sort(key=lambda x: -x["risk"])
            content_blocks.append(
                self._table_block(
                    title=f"Needs Review ({len(review_items)}) — Top Items",
                    columns=[
                        {"key": "name", "label": "Line Item"},
                        {"key": "category", "label": "Category"},
                        {"key": "confidence", "label": "Conf"},
                        {"key": "next_step", "label": "Next Step"},
                        {"key": "reasoning", "label": "AI Finding"},
                    ],
                    rows=review_items[:5],
                )
            )

        # Recommendation
        if flag_items:
            content_blocks.append(
                self._text_block(
                    f"**Recommendation:** Review the {len(flag_items)} flagged items first. "
                    f"The {len(approve_items)} auto-approvable items can be accepted in one click from the Review Dashboard."
                )
            )
        elif review_items:
            content_blocks.append(
                self._text_block(
                    f"**Recommendation:** No critical flags. Review the {len(review_items)} moderate items, "
                    f"then auto-approve the remaining {len(approve_items)} items."
                )
            )
        else:
            content_blocks.append(
                self._text_block(
                    "**All items pass AI quality checks!** You can auto-approve all items from the Review Dashboard."
                )
            )

        # Open the interactive review dashboard
        content_blocks.append(
            self._panel_trigger(
                panel="review_dashboard",
                params={"version_id": version_id},
                label="Open Interactive Review Dashboard",
            )
        )

        return SkillResult.ok(
            message=(
                f"AI triage complete: {len(approve_items)} auto-approve, "
                f"{len(review_items)} review, {len(flag_items)} flagged"
            ),
            data={
                "version_id": version_id,
                "auto_approve": len(approve_items),
                "needs_review": len(review_items),
                "flagged": len(flag_items),
            },
            content_blocks=content_blocks,
        )

    def _analyze_item(
        self,
        r: ForecastLineResult,
        actuals_map: dict[tuple[int, str], float],
        category_stats: dict[str, dict],
    ) -> dict[str, Any]:
        """Business-context-aware AI analysis for a single forecast line.

        Returns recommendation, reasoning, risk_score, and structured actions
        that tell the user exactly what they can do.
        """
        from app.api.dashboard import _ai_analyze_item
        return _ai_analyze_item(r, actuals_map, category_stats)

    # ──────────────────────────────────────────────
    # Existing workflow actions (enhanced)
    # ──────────────────────────────────────────────

    async def _submit_for_review(
        self, db: Session, params: dict[str, Any], context: SkillContext
    ) -> SkillResult:
        """Submit a draft forecast for review."""
        version_id = params.get("version_id") or context.context_manager.get_active_version_id()
        if not version_id:
            return SkillResult.fail("No active forecast version.")

        version = db.query(ForecastVersion).filter(ForecastVersion.id == version_id).first()
        if not version:
            return SkillResult.fail(f"Version '{version_id}' not found.")

        if version.status != "draft":
            return SkillResult.fail(
                f"Cannot submit a '{version.status}' forecast for review. Only drafts can be submitted."
            )

        total_results = (
            db.query(ForecastLineResult)
            .filter(ForecastLineResult.version_id == version_id)
            .count()
        )
        low_conf = (
            db.query(ForecastLineResult)
            .filter(
                ForecastLineResult.version_id == version_id,
                ForecastLineResult.confidence_level == "low",
            )
            .count()
        )

        version.status = "in_review"
        db.commit()

        content_blocks = [
            self._text_block(f"Submitted **{version.name}** for review."),
            self._table_block(
                title="Review Summary",
                columns=[
                    {"key": "metric", "label": "Metric"},
                    {"key": "value", "label": "Value"},
                ],
                rows=[
                    {"metric": "Total line results", "value": str(total_results)},
                    {"metric": "Low confidence items", "value": str(low_conf)},
                    {"metric": "Overrides applied", "value": str(version.override_count)},
                    {"metric": "Status", "value": "In Review"},
                ],
            ),
        ]

        if low_conf > 0:
            content_blocks.append(
                self._text_block(f"**Note:** {low_conf} items have low confidence.")
            )

        content_blocks.append(
            self._panel_trigger(
                panel="review_dashboard",
                params={"version_id": version_id},
                label="Open Review Dashboard",
            )
        )

        return SkillResult.ok(
            message=f"Submitted {version.name} for review",
            data={"version_id": version_id, "status": "in_review"},
            content_blocks=content_blocks,
        )

    async def _approve(
        self, db: Session, params: dict[str, Any], context: SkillContext
    ) -> SkillResult:
        """Approve a forecast (requires can_review permission)."""
        from app.services.permissions import APPROVER_ROLES

        role = context.user_role
        allowed = (
            context.context_manager.has_permission("review")
            or role in APPROVER_ROLES
            or role == "manager"
        )
        if not allowed:
            return SkillResult.fail(
                "Only reviewers, publishers, and admins can approve forecasts. Your role: " + str(role)
            )

        version_id = params.get("version_id") or context.context_manager.get_active_version_id()
        if not version_id:
            return SkillResult.fail("No version specified.")

        version = db.query(ForecastVersion).filter(ForecastVersion.id == version_id).first()
        if not version:
            return SkillResult.fail(f"Version '{version_id}' not found.")

        if version.status != "in_review":
            return SkillResult.fail(
                f"Cannot approve a '{version.status}' forecast. Must be 'in_review'."
            )

        version.status = "approved"
        version.approved_at = datetime.now(timezone.utc)
        version.approved_by = context.user_id

        if params.get("publish", False):
            version.status = "published"
            version.published_at = datetime.now(timezone.utc)

        db.commit()
        final_status = version.status
        comments = params.get("comments", "")

        content_blocks = [
            self._text_block(f"**{version.name}** has been **{final_status}**."),
        ]
        if comments:
            content_blocks.append(self._text_block(f"Comments: _{comments}_"))

        return SkillResult.ok(
            message=f"{version.name} {final_status}",
            data={"version_id": version_id, "status": final_status},
            content_blocks=content_blocks,
        )

    async def _reject(
        self, db: Session, params: dict[str, Any], context: SkillContext
    ) -> SkillResult:
        """Reject a forecast and return to draft."""
        from app.services.permissions import APPROVER_ROLES

        role = context.user_role
        allowed = (
            context.context_manager.has_permission("review")
            or role in APPROVER_ROLES
            or role == "manager"
        )
        if not allowed:
            return SkillResult.fail("Only reviewers, publishers, and admins can reject forecasts.")

        version_id = params.get("version_id") or context.context_manager.get_active_version_id()
        if not version_id:
            return SkillResult.fail("No version specified.")

        version = db.query(ForecastVersion).filter(ForecastVersion.id == version_id).first()
        if not version:
            return SkillResult.fail(f"Version '{version_id}' not found.")

        comments = params.get("comments", "")
        if not comments:
            return SkillResult.fail("Comments are required when rejecting.")

        version.status = "draft"
        db.commit()

        return SkillResult.ok(
            message=f"{version.name} rejected: {comments}",
            data={"version_id": version_id, "status": "draft"},
            content_blocks=[
                self._text_block(f"**{version.name}** rejected and returned to draft."),
                self._text_block(f"Reason: _{comments}_"),
            ],
        )

    async def _review_queue(
        self, db: Session, params: dict[str, Any], context: SkillContext
    ) -> SkillResult:
        """Show a concise review queue — grouped by line item, not individual periods."""
        version_id = params.get("version_id") or context.context_manager.get_active_version_id()
        if not version_id:
            return SkillResult.fail("No active forecast version.")

        version = db.query(ForecastVersion).filter(ForecastVersion.id == version_id).first()
        if not version:
            return SkillResult.fail(f"Version '{version_id}' not found.")

        # Get items grouped by line item (only those needing attention)
        flagged = (
            db.query(ForecastLineResult)
            .join(LineItem)
            .filter(
                ForecastLineResult.version_id == version_id,
                ForecastLineResult.ai_recommendation.in_(["review", "flag", "override"]),
            )
            .all()
        )

        if not flagged:
            # Fall back to confidence-based if AI hasn't run
            flagged = (
                db.query(ForecastLineResult)
                .join(LineItem)
                .filter(
                    ForecastLineResult.version_id == version_id,
                    ForecastLineResult.confidence_level.in_(["low", "medium"]),
                )
                .all()
            )

        # Group by line item for concise output
        groups: dict[int, list[ForecastLineResult]] = {}
        for r in flagged:
            groups.setdefault(r.line_item_id, []).append(r)

        if not groups:
            return SkillResult.ok(
                message="No items require review.",
                content_blocks=[
                    self._text_block(f"**{version.name}** — all items pass quality checks.")
                ],
            )

        rows = []
        for li_id, group in sorted(groups.items(), key=lambda x: min(r.confidence_score for r in x[1])):
            worst = min(group, key=lambda x: x.confidence_score)
            li = worst.line_item
            rows.append({
                "item": li.name if li else "Unknown",
                "category": li.category if li else "?",
                "periods": str(len(group)),
                "conf": str(round(worst.confidence_score)),
                "finding": (worst.ai_reasoning or worst.confidence_level)[:80],
            })
            if len(rows) >= 15:
                break

        content_blocks = [
            self._text_block(
                f"**Review Queue for {version.name}**: {len(groups)} line items need attention"
            ),
            self._table_block(
                title="Items Requiring Review",
                columns=[
                    {"key": "item", "label": "Line Item"},
                    {"key": "category", "label": "Category"},
                    {"key": "periods", "label": "Periods"},
                    {"key": "conf", "label": "Conf"},
                    {"key": "finding", "label": "Finding"},
                ],
                rows=rows,
            ),
            self._panel_trigger(
                panel="review_dashboard",
                params={"version_id": version_id},
                label="Open Review Dashboard (Interactive)",
            ),
        ]

        return SkillResult.ok(
            message=f"Review queue: {len(groups)} line items need attention",
            data={"flagged_line_items": len(groups)},
            content_blocks=content_blocks,
        )

    async def _auto_approve_check(
        self, db: Session, params: dict[str, Any], context: SkillContext
    ) -> SkillResult:
        """Check if a forecast qualifies for auto-approval."""
        version_id = params.get("version_id") or context.context_manager.get_active_version_id()
        if not version_id:
            return SkillResult.fail("No active forecast version.")

        version = db.query(ForecastVersion).filter(ForecastVersion.id == version_id).first()
        if not version:
            return SkillResult.fail(f"Version '{version_id}' not found.")

        total = (
            db.query(ForecastLineResult)
            .filter(ForecastLineResult.version_id == version_id)
            .count()
        )
        low = (
            db.query(ForecastLineResult)
            .filter(
                ForecastLineResult.version_id == version_id,
                ForecastLineResult.confidence_level == "low",
            )
            .count()
        )
        overridden = (
            db.query(ForecastLineResult)
            .filter(
                ForecastLineResult.version_id == version_id,
                ForecastLineResult.is_overridden == True,
            )
            .count()
        )

        qualifies = low == 0 and overridden == 0 and total > 0
        high_pct = (version.high_confidence_count / total * 100) if total > 0 else 0

        reasons = []
        if low > 0:
            reasons.append(f"{low} low-confidence items")
        if overridden > 0:
            reasons.append(f"{overridden} overridden items")
        if total == 0:
            reasons.append("No forecast data")

        status_text = (
            "**Auto-approvable** — all items pass quality checks."
            if qualifies
            else f"**Manual review required** — {'; '.join(reasons)}"
        )

        content_blocks = [
            self._text_block(status_text),
            self._table_block(
                title="Auto-Approval Criteria",
                columns=[
                    {"key": "criterion", "label": "Criterion"},
                    {"key": "status", "label": "Status"},
                ],
                rows=[
                    {"criterion": "No low-confidence items", "status": "Pass" if low == 0 else f"Fail ({low})"},
                    {"criterion": "No manual overrides", "status": "Pass" if overridden == 0 else f"Fail ({overridden})"},
                    {"criterion": "Forecast data exists", "status": "Pass" if total > 0 else "Fail"},
                    {"criterion": "High confidence >= 80%", "status": f"{'Pass' if high_pct >= 80 else 'Fail'} ({high_pct:.0f}%)"},
                ],
            ),
        ]

        return SkillResult.ok(
            message=f"Auto-approve {'eligible' if qualifies else 'not eligible'}",
            data={"qualifies": qualifies, "high_pct": high_pct},
            content_blocks=content_blocks,
        )
