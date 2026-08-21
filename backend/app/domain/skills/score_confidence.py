"""ScoreConfidence skill -- scores forecast lines and prioritizes review."""

import logging
from typing import Any

from sqlalchemy.orm import Session

from app.domain.base_skill import BaseSkill, SkillContext, SkillResult
from app.models.forecast import ForecastVersion, ForecastLineResult
from app.config import settings
from app.services.confidence import compute_confidence_score, classify_confidence

logger = logging.getLogger(__name__)


class ScoreConfidenceSkill(BaseSkill):
    """Skill to score confidence for all forecast lines and categorize for review."""

    @property
    def name(self) -> str:
        return "score_confidence"

    @property
    def description(self) -> str:
        return (
            "Score each forecast line item by model confidence on a 0-100 scale. "
            "Categorizes lines as High Confidence (auto-approvable), Medium Confidence "
            "(review recommended), or Low Confidence (review required). "
            "Use this after generating a baseline forecast to identify lines needing review."
        )

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "version_id": {
                    "type": "string",
                    "description": "Forecast version ID to score (uses active version if not specified)",
                },
                "threshold_low": {
                    "type": "integer",
                    "description": "Score below this = Low Confidence (default: 50)",
                    "default": 50,
                },
                "threshold_medium": {
                    "type": "integer",
                    "description": "Score below this = Medium Confidence (default: 70)",
                    "default": 70,
                },
            },
        }

    async def execute(self, params: dict[str, Any], context: SkillContext) -> SkillResult:
        """Score confidence for all lines in a forecast version."""
        db: Session = context.db

        version_id = params.get("version_id") or context.context_manager.get_active_version_id()
        if not version_id:
            return SkillResult.fail("No forecast version specified or active.")

        version = db.query(ForecastVersion).filter(ForecastVersion.id == version_id).first()
        if not version:
            return SkillResult.fail(f"Forecast version '{version_id}' not found.")

        threshold_low = params.get("threshold_low", settings.confidence_threshold_low)
        threshold_medium = params.get("threshold_medium", settings.confidence_threshold_medium)

        # Get all line results (unique by line_item_id, take first period as representative)
        line_results = (
            db.query(ForecastLineResult)
            .filter(ForecastLineResult.version_id == version_id)
            .all()
        )

        if not line_results:
            return SkillResult.fail("No forecast results found for this version.")

        # Score each result
        high_count = 0
        medium_count = 0
        low_count = 0
        low_confidence_items = []

        for result in line_results:
            score = compute_confidence_score(result)
            result.confidence_score = score
            result.confidence_level = classify_confidence(
                score, threshold_low=threshold_low, threshold_medium=threshold_medium
            )

            if result.confidence_level == "high":
                high_count += 1
            elif result.confidence_level == "medium":
                medium_count += 1
            else:
                low_count += 1
                # Track for summary
                if result.line_item:
                    low_confidence_items.append({
                        "line_item": result.line_item.name,
                        "category": result.line_item.category,
                        "period": result.period,
                        "score": round(score, 1),
                        "model": result.model_type,
                    })

        # Update version summary (count unique line items per level)
        version.high_confidence_count = high_count
        version.medium_confidence_count = medium_count
        version.low_confidence_count = low_count

        db.commit()

        # Deduplicate low confidence items (show unique line items)
        seen = set()
        unique_low = []
        for item in sorted(low_confidence_items, key=lambda x: x["score"]):
            if item["line_item"] not in seen:
                seen.add(item["line_item"])
                unique_low.append(item)

        # Build response
        total = high_count + medium_count + low_count
        content_blocks = [
            self._text_block(
                f"Scored {total} forecast line-periods across {version.name}."
            ),
            self._table_block(
                title="Confidence Distribution",
                columns=[
                    {"key": "level", "label": "Confidence Level"},
                    {"key": "count", "label": "Count"},
                    {"key": "pct", "label": "%"},
                    {"key": "action", "label": "Action Required"},
                ],
                rows=[
                    {
                        "level": "High (70+)",
                        "count": str(high_count),
                        "pct": f"{high_count/total*100:.0f}%" if total > 0 else "0%",
                        "action": "Auto-approvable",
                    },
                    {
                        "level": "Medium (50-70)",
                        "count": str(medium_count),
                        "pct": f"{medium_count/total*100:.0f}%" if total > 0 else "0%",
                        "action": "Review recommended",
                    },
                    {
                        "level": "Low (<50)",
                        "count": str(low_count),
                        "pct": f"{low_count/total*100:.0f}%" if total > 0 else "0%",
                        "action": "Review required",
                    },
                ],
            ),
        ]

        if unique_low:
            low_rows = [
                {
                    "line": item["line_item"],
                    "category": item["category"],
                    "score": str(item["score"]),
                    "model": item["model"] or "N/A",
                }
                for item in unique_low[:10]
            ]
            content_blocks.append(
                self._table_block(
                    title=f"Top Low-Confidence Items ({len(unique_low)} total)",
                    columns=[
                        {"key": "line", "label": "Line Item"},
                        {"key": "category", "label": "Category"},
                        {"key": "score", "label": "Score"},
                        {"key": "model", "label": "Model"},
                    ],
                    rows=low_rows,
                )
            )

            content_blocks.append(
                self._panel_trigger(
                    panel="review_queue",
                    params={"version_id": version_id},
                    label="Open Review Queue",
                )
            )

        return SkillResult.ok(
            message=(
                f"Confidence scoring complete for {version.name}: "
                f"{high_count} high, {medium_count} medium, {low_count} low confidence lines."
            ),
            data={
                "version_id": version_id,
                "high_count": high_count,
                "medium_count": medium_count,
                "low_count": low_count,
                "low_confidence_items": unique_low[:10],
            },
            content_blocks=content_blocks,
        )

    def _compute_confidence_score(self, result: ForecastLineResult) -> float:
        """Delegate to shared scorer (kept for any subclass/test that calls it)."""
        return compute_confidence_score(result)
