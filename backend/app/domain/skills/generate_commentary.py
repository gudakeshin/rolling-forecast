"""GenerateCommentary skill -- AI-powered narrative commentary for forecast results."""

import logging
from typing import Any

from sqlalchemy.orm import Session
from sqlalchemy import func

from app.domain.base_skill import BaseSkill, SkillContext, SkillResult
from app.models.forecast import ForecastVersion, ForecastLineResult
from app.models.line_item import LineItem
from app.models.override import Override
from app.config import settings

logger = logging.getLogger(__name__)


class GenerateCommentarySkill(BaseSkill):
    """Skill to generate AI-powered executive commentary for forecasts."""

    @property
    def name(self) -> str:
        return "generate_commentary"

    @property
    def description(self) -> str:
        return (
            "Generate AI-powered natural language commentary for forecast results. "
            "Produces executive-ready narrative explaining key drivers, variances, "
            "risks, and recommendations."
        )

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "version_id": {
                    "type": "string",
                    "description": "Forecast version to commentate (uses active version if not specified)",
                },
                "scope": {
                    "type": "string",
                    "description": "Scope: executive, category, or line_item",
                    "enum": ["executive", "category", "line_item"],
                    "default": "executive",
                },
                "category": {
                    "type": "string",
                    "description": "Category to focus on (for category scope)",
                },
                "line_item_name": {
                    "type": "string",
                    "description": "Specific line item (for line_item scope)",
                },
                "include_risks": {
                    "type": "boolean",
                    "description": "Include risk assessment",
                    "default": True,
                },
                "include_recommendations": {
                    "type": "boolean",
                    "description": "Include actionable recommendations",
                    "default": True,
                },
                "tone": {
                    "type": "string",
                    "description": "Commentary tone: formal, concise, analytical",
                    "enum": ["formal", "concise", "analytical"],
                    "default": "formal",
                },
            },
        }

    async def execute(self, params: dict[str, Any], context: SkillContext) -> SkillResult:
        db: Session = context.db

        version_id = params.get("version_id") or context.context_manager.get_active_version_id()
        if not version_id:
            return SkillResult.fail("No active forecast version.")

        version = db.query(ForecastVersion).filter(ForecastVersion.id == version_id).first()
        if not version:
            return SkillResult.fail(f"Version '{version_id}' not found.")

        scope = params.get("scope", "executive")
        tone = params.get("tone", "formal")
        include_risks = params.get("include_risks", True)
        include_recommendations = params.get("include_recommendations", True)

        # Gather data context
        data_context = self._gather_data_context(db, version, scope, params)

        # Generate commentary using the gathered data
        commentary = self._generate_commentary(
            data_context, scope, tone, include_risks, include_recommendations, version
        )

        from app.services.numeric_grounding import (
            facts_from_context,
            render_fact_placeholders,
            validate_numeric_claims,
        )

        facts = facts_from_context(data_context)
        # Also expose common totals as facts
        total_forecast = sum(c.get("total", 0) for c in data_context.get("categories", []))
        facts.setdefault("revenue_total", total_forecast)
        facts.setdefault("total_forecast", total_forecast)
        allowed = {str(v) for v in facts.values()}
        for v in list(facts.values()):
            if isinstance(v, float):
                allowed.add(f"{v:,.0f}")
                allowed.add(f"{v:,.1f}")
            elif isinstance(v, int):
                allowed.add(f"{v:,}")

        content_blocks = []
        verified_total = 0
        claims_total = 0
        for section in commentary:
            rendered = render_fact_placeholders(section, facts)
            cleaned, verified, total = validate_numeric_claims(rendered, allowed)
            verified_total += verified
            claims_total += total
            content_blocks.append(self._text_block(cleaned))

        figures_meta = {
            "figures_verified": verified_total,
            "figures_total": claims_total,
            "figures_verified_ratio": (
                f"{verified_total}/{claims_total}" if claims_total else "0/0"
            ),
        }
        content_blocks.insert(
            0,
            self._status_block(
                label=f"Figures verified {figures_meta['figures_verified_ratio']}",
                progress=1.0 if claims_total == 0 or verified_total == claims_total else verified_total / max(claims_total, 1),
                step="numeric_grounding",
                is_complete=True,
            ),
        )

        return SkillResult.ok(
            message=(
                f"Generated {scope} commentary for {version.name} ({tone} tone) "
                f"— figures verified {figures_meta['figures_verified_ratio']}"
            ),
            data={
                "version_id": version_id,
                "scope": scope,
                "tone": tone,
                "sections": len(commentary),
                **figures_meta,
            },
            content_blocks=content_blocks,
        )

    def _gather_data_context(
        self, db: Session, version: ForecastVersion, scope: str, params: dict
    ) -> dict[str, Any]:
        """Gather all relevant data for commentary generation."""
        ctx: dict[str, Any] = {
            "version_name": version.name,
            "status": version.status,
            "horizon": version.horizon_months,
            "total_lines": version.total_line_items,
            "high_conf": version.high_confidence_count,
            "med_conf": version.medium_confidence_count,
            "low_conf": version.low_confidence_count,
            "overrides": version.override_count,
        }

        # Category summaries
        first_period = (
            db.query(func.min(ForecastLineResult.period))
            .filter(ForecastLineResult.version_id == version.id)
            .scalar()
        )

        categories = (
            db.query(
                LineItem.category,
                func.sum(ForecastLineResult.p50).label("total"),
                func.count(ForecastLineResult.id).label("count"),
                func.avg(ForecastLineResult.confidence_score).label("avg_conf"),
            )
            .join(LineItem)
            .filter(
                ForecastLineResult.version_id == version.id,
                ForecastLineResult.period == first_period,
            )
            .group_by(LineItem.category)
            .all()
        )

        ctx["categories"] = [
            {
                "name": cat,
                "total": float(total) if total else 0,
                "count": count,
                "avg_confidence": float(avg_conf) if avg_conf else 0,
            }
            for cat, total, count, avg_conf in categories
        ]
        ctx["first_period"] = first_period

        # Low confidence items
        low_conf_items = (
            db.query(ForecastLineResult, LineItem)
            .join(LineItem)
            .filter(
                ForecastLineResult.version_id == version.id,
                ForecastLineResult.confidence_level == "low",
                ForecastLineResult.period == first_period,
            )
            .order_by(ForecastLineResult.confidence_score)
            .limit(5)
            .all()
        )
        ctx["low_confidence_items"] = [
            {
                "name": li.name,
                "category": li.category,
                "value": float(r.p50),
                "confidence": float(r.confidence_score),
                "model": r.model_type,
            }
            for r, li in low_conf_items
        ]

        # Override details
        overrides = (
            db.query(Override, LineItem)
            .join(LineItem, LineItem.id == Override.line_item_id)
            .filter(Override.version_id == version.id, Override.status == "active")
            .limit(10)
            .all()
        )
        ctx["override_details"] = [
            {
                "line_item": li.name,
                "original": float(o.original_model_value),
                "override": float(o.override_value),
                "reason": o.reason,
                "change_pct": ((o.override_value - o.original_model_value) / abs(o.original_model_value) * 100)
                if o.original_model_value != 0 else 0,
            }
            for o, li in overrides
        ]

        # Scope-specific data
        if scope == "category" and params.get("category"):
            cat_filter = params["category"]
            cat_items = (
                db.query(ForecastLineResult, LineItem)
                .join(LineItem)
                .filter(
                    ForecastLineResult.version_id == version.id,
                    LineItem.category.ilike(f"%{cat_filter}%"),
                    ForecastLineResult.period == first_period,
                )
                .order_by(ForecastLineResult.p50.desc())
                .all()
            )
            ctx["category_detail"] = [
                {
                    "name": li.name,
                    "value": float(r.p50),
                    "confidence": float(r.confidence_score),
                    "model": r.model_type,
                    "overridden": r.is_overridden,
                }
                for r, li in cat_items
            ]

        return ctx

    def _generate_commentary(
        self,
        ctx: dict[str, Any],
        scope: str,
        tone: str,
        include_risks: bool,
        include_recommendations: bool,
        version: ForecastVersion,
    ) -> list[str]:
        """Generate structured commentary — prefers LLM with citations, falls back to rules."""
        llm_sections = self._llm_commentary(
            ctx, scope, tone, include_risks, include_recommendations, version
        )
        if llm_sections:
            return llm_sections

        sections = []
        if scope == "executive":
            sections.extend(self._executive_commentary(ctx, tone, version))
        elif scope == "category":
            sections.extend(self._category_commentary(ctx, tone))
        elif scope == "line_item":
            sections.extend(self._line_item_commentary(ctx, tone))
        if include_risks:
            sections.extend(self._risk_commentary(ctx, tone))
        if include_recommendations:
            sections.extend(self._recommendation_commentary(ctx, tone))
        return sections

    def _llm_commentary(
        self,
        ctx: dict[str, Any],
        scope: str,
        tone: str,
        include_risks: bool,
        include_recommendations: bool,
        version: ForecastVersion,
    ) -> list[str] | None:
        """Call Anthropic for CFO-grade narrative with override/doc citations."""

        if not settings.anthropic_api_key:
            return None
        try:
            import json
            from anthropic import Anthropic

            client = Anthropic(api_key=settings.anthropic_api_key)
            citations = []
            for o in ctx.get("overrides", [])[:15]:
                citations.append(
                    f"- Override on {o.get('line_item')}: {o.get('reason')} "
                    f"({o.get('change_pct', 0):+.1f}%)"
                )
            prompt = (
                f"You are an FP&A CFO briefing writer. Tone: {tone}. Scope: {scope}.\n"
                f"Forecast: {version.name} (status={version.status}).\n"
                f"Data context (JSON):\n{json.dumps(ctx, default=str)[:8000]}\n\n"
                f"Key override citations:\n" + ("\n".join(citations) or "(none)") + "\n\n"
                "Write 3-6 short markdown sections suitable for a board pack. "
                "IMPORTANT: Do NOT invent numbers. Reference figures only as placeholders "
                "like {fact:total_forecast} or {fact:revenue_total} — they will be "
                "substituted programmatically from the data context. "
                "Cite specific overrides/reasons inline. "
                f"{'Include risks. ' if include_risks else ''}"
                f"{'Include recommendations. ' if include_recommendations else ''}"
                "Return plain markdown only."
            )
            resp = client.messages.create(
                model=settings.anthropic_model,
                max_tokens=1200,
                messages=[{"role": "user", "content": prompt}],
            )
            text = "".join(
                block.text for block in resp.content if getattr(block, "type", None) == "text"
            ).strip()
            if not text:
                return None
            # Split into sections on markdown headings or double newlines
            parts = [p.strip() for p in text.split("\n\n") if p.strip()]
            return parts or [text]
        except Exception as e:
            logger.warning("LLM commentary failed, using rules: %s", e)
            return None

    def _executive_commentary(self, ctx: dict, tone: str, version: ForecastVersion) -> list[str]:
        """Generate executive-level commentary."""
        sections = []

        # Overview
        total_forecast = sum(c["total"] for c in ctx["categories"])
        conf_pct = (
            (ctx["high_conf"] or 0) / max(1, (ctx["high_conf"] or 0) + (ctx["med_conf"] or 0) + (ctx["low_conf"] or 0))
        ) * 100

        if tone == "concise":
            overview = (
                f"**Forecast Overview: {version.name}**\n"
                f"- Total forecast value: **${total_forecast:,.0f}** ({ctx['horizon']}-month horizon)\n"
                f"- {ctx['total_lines']} line items across {len(ctx['categories'])} categories\n"
                f"- Confidence: {conf_pct:.0f}% high confidence\n"
                f"- Overrides: {ctx['overrides']} manual adjustments applied"
            )
        else:
            overview = (
                f"**Executive Summary: {version.name}**\n\n"
                f"The rolling forecast for the upcoming {ctx['horizon']}-month period projects "
                f"a total value of **${total_forecast:,.0f}** across {ctx['total_lines']} P&L line items "
                f"organized in {len(ctx['categories'])} categories. "
                f"The forecast achieves a **{conf_pct:.0f}%** high-confidence rate, "
                f"with {ctx['overrides']} manual override(s) applied to incorporate business judgment."
            )
        sections.append(overview)

        # Category breakdown
        cat_lines = []
        for cat in sorted(ctx["categories"], key=lambda c: c["total"], reverse=True):
            pct_of_total = (cat["total"] / total_forecast * 100) if total_forecast else 0
            cat_lines.append(
                f"- **{cat['name']}**: ${cat['total']:,.0f} "
                f"({pct_of_total:.1f}% of total, {cat['count']} lines, "
                f"avg confidence {cat['avg_confidence']:.0f})"
            )

        if cat_lines:
            sections.append("**Category Breakdown**\n" + "\n".join(cat_lines))

        # Key drivers (overrides indicate manual intervention / key decisions)
        if ctx["override_details"]:
            driver_lines = []
            for o in ctx["override_details"][:5]:
                direction = "increase" if o["change_pct"] > 0 else "decrease"
                driver_lines.append(
                    f"- **{o['line_item']}**: {direction} of {abs(o['change_pct']):.1f}% "
                    f"(${o['original']:,.0f} → ${o['override']:,.0f}). _{o['reason']}_"
                )
            sections.append("**Key Adjustments & Drivers**\n" + "\n".join(driver_lines))

        return sections

    def _category_commentary(self, ctx: dict, tone: str) -> list[str]:
        """Generate category-level commentary."""
        sections = []
        detail = ctx.get("category_detail", [])

        if not detail:
            return [self._text_block("No data found for the specified category.")]

        total = sum(d["value"] for d in detail)
        overridden = sum(1 for d in detail if d["overridden"])
        avg_conf = sum(d["confidence"] for d in detail) / len(detail) if detail else 0

        sections.append(
            f"**Category Analysis** (Period: {ctx.get('first_period', 'N/A')})\n\n"
            f"Total: **${total:,.0f}** across {len(detail)} line items. "
            f"Average confidence: **{avg_conf:.0f}**. "
            f"Overridden items: **{overridden}**."
        )

        # Top items
        top_lines = []
        for d in detail[:8]:
            override_tag = " _(overridden)_" if d["overridden"] else ""
            top_lines.append(
                f"- {d['name']}: **${d['value']:,.0f}** "
                f"(conf: {d['confidence']:.0f}, model: {d['model']}){override_tag}"
            )
        if top_lines:
            sections.append("**Line Item Details**\n" + "\n".join(top_lines))

        return sections

    def _line_item_commentary(self, ctx: dict, tone: str) -> list[str]:
        """Generate line-item-level commentary."""
        # This is a simplified version; in production would include trend analysis
        return [
            f"**Line Item Analysis** for {ctx['version_name']}\n\n"
            f"Use the `query_forecast` skill with query_type=line_item for detailed "
            f"period-by-period data, then this commentary skill can provide deeper analysis."
        ]

    def _risk_commentary(self, ctx: dict, tone: str) -> list[str]:
        """Generate risk assessment."""
        risks = []

        # Low confidence risk
        if ctx.get("low_confidence_items"):
            items_text = ", ".join(
                f"{i['name']} ({i['confidence']:.0f})"
                for i in ctx["low_confidence_items"][:3]
            )
            risks.append(
                f"- **Low Confidence Lines**: {ctx.get('low_conf', 0)} items have low model confidence, "
                f"including: {items_text}. These may require additional manual review or data refresh."
            )

        # Override concentration
        if len(ctx.get("override_details", [])) > 5:
            risks.append(
                f"- **Override Concentration**: {len(ctx['override_details'])} overrides applied. "
                f"High override counts may indicate model-data misalignment or changing business conditions."
            )

        # Model diversity
        if ctx.get("categories"):
            low_conf_cats = [c for c in ctx["categories"] if c["avg_confidence"] < 50]
            if low_conf_cats:
                cat_names = ", ".join(c["name"] for c in low_conf_cats)
                risks.append(
                    f"- **Category Risk**: {cat_names} categories show below-average confidence. "
                    f"Consider reviewing data quality and model assumptions for these areas."
                )

        if not risks:
            return ["**Risk Assessment**\n\nNo significant risks identified. The forecast appears stable with adequate confidence levels."]

        return ["**Risk Assessment**\n" + "\n".join(risks)]

    def _recommendation_commentary(self, ctx: dict, tone: str) -> list[str]:
        """Generate actionable recommendations."""
        recs = []

        if ctx.get("low_conf", 0) > 0:
            recs.append(
                "- Run **ensemble modeling** on low-confidence items to potentially improve accuracy"
            )

        if ctx.get("overrides", 0) == 0:
            recs.append(
                "- Consider reviewing high-value line items with business stakeholders for potential adjustments"
            )

        if len(ctx.get("categories", [])) > 3:
            recs.append(
                "- Schedule category-level review sessions with BU heads before final approval"
            )

        recs.append("- Generate the **audit trail** before submission for SOX compliance readiness")

        if tone == "concise":
            return ["**Recommendations**\n" + "\n".join(recs)]
        else:
            return [
                "**Recommendations**\n\n"
                "Based on the analysis above, we suggest the following actions:\n" +
                "\n".join(recs)
            ]
