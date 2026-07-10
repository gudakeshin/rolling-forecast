"""QueryForecast skill -- answers natural language questions about forecast data."""

import logging
from typing import Any

from sqlalchemy.orm import Session
from sqlalchemy import func

from app.domain.base_skill import BaseSkill, SkillContext, SkillResult
from app.models.forecast import ForecastVersion, ForecastLineResult
from app.models.line_item import LineItem
from app.models.override import Override
from app.services.permissions import line_item_scope_filter, resolve_skill_user

logger = logging.getLogger(__name__)


class QueryForecastSkill(BaseSkill):
    """Skill to answer natural language questions about forecast data."""

    def _scope(self, query, context: SkillContext):
        """Apply BU scope to a query already joined to LineItem."""
        user = resolve_skill_user(context)
        if user is None:
            return query
        return line_item_scope_filter(query, user, LineItem)

    @property
    def name(self) -> str:
        return "query_forecast"

    @property
    def description(self) -> str:
        return (
            "Query forecast data to answer questions. Can retrieve specific line items, "
            "summarize categories, show overrides, get confidence details, "
            "or provide aggregate statistics. Use this when the user asks questions like "
            "'what is the revenue forecast?', 'which lines have low confidence?', "
            "'show me the OpEx breakdown', 'how many overrides are there?', etc."
        )

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "query_type": {
                    "type": "string",
                    "description": "Type of query",
                    "enum": [
                        "line_item",
                        "category_summary",
                        "confidence_summary",
                        "overrides",
                        "version_summary",
                        "search",
                    ],
                },
                "version_id": {
                    "type": "string",
                    "description": "Version ID to query (uses active if not specified)",
                },
                "line_item_name": {
                    "type": "string",
                    "description": "Line item name or account code to look up",
                },
                "category": {
                    "type": "string",
                    "description": "Category to filter by (e.g., Revenue, COGS, OpEx)",
                },
                "search_term": {
                    "type": "string",
                    "description": "Search term to find matching line items",
                },
                "confidence_level": {
                    "type": "string",
                    "description": "Filter by confidence level",
                    "enum": ["high", "medium", "low"],
                },
            },
            "required": ["query_type"],
        }

    async def execute(self, params: dict[str, Any], context: SkillContext) -> SkillResult:
        db: Session = context.db

        version_id = params.get("version_id") or context.context_manager.get_active_version_id()
        if not version_id:
            return SkillResult.fail("No forecast version active. Generate or select a forecast first.")

        version = db.query(ForecastVersion).filter(ForecastVersion.id == version_id).first()
        if not version:
            return SkillResult.fail(f"Version '{version_id}' not found.")

        query_type = params.get("query_type", "version_summary")

        if query_type == "version_summary":
            return await self._version_summary(db, version, context)
        elif query_type == "category_summary":
            return await self._category_summary(db, version, params.get("category"), context)
        elif query_type == "line_item":
            return await self._line_item_detail(db, version, params.get("line_item_name", ""), context)
        elif query_type == "confidence_summary":
            return await self._confidence_summary(db, version, params.get("confidence_level"), context)
        elif query_type == "overrides":
            return await self._overrides_summary(db, version, context)
        elif query_type == "search":
            return await self._search(db, version, params.get("search_term", ""), context)
        else:
            return SkillResult.fail(f"Unknown query type: {query_type}")

    async def _version_summary(
        self, db: Session, version: ForecastVersion, context: SkillContext
    ) -> SkillResult:
        """High-level summary of the active forecast version."""
        # Get category totals (sum of first forecast month P50)
        first_period = (
            db.query(func.min(ForecastLineResult.period))
            .filter(ForecastLineResult.version_id == version.id)
            .scalar()
        )

        categories = (
            self._scope(
                db.query(
                    LineItem.category,
                    func.sum(ForecastLineResult.p50).label("total"),
                    func.count(ForecastLineResult.id).label("count"),
                    func.avg(ForecastLineResult.confidence_score).label("avg_confidence"),
                )
                .join(LineItem)
                .filter(
                    ForecastLineResult.version_id == version.id,
                    ForecastLineResult.period == first_period,
                ),
                context,
            )
            .group_by(LineItem.category)
            .all()
        )

        rows = [
            {
                "category": cat,
                "total": f"${total:,.0f}" if total else "$0",
                "lines": str(count),
                "avg_confidence": f"{avg_conf:.0f}" if avg_conf else "N/A",
            }
            for cat, total, count, avg_conf in categories
        ]

        content_blocks = [
            self._text_block(
                f"**{version.name}** summary for period {first_period}:"
            ),
            self._table_block(
                title="Category Summary",
                columns=[
                    {"key": "category", "label": "Category"},
                    {"key": "total", "label": f"Total ({first_period})"},
                    {"key": "lines", "label": "Lines"},
                    {"key": "avg_confidence", "label": "Avg Confidence"},
                ],
                rows=rows,
            ),
        ]

        return SkillResult.ok(
            message=f"Summary for {version.name}: {len(categories)} categories, {version.total_line_items} line items",
            data={"categories": rows, "first_period": first_period},
            content_blocks=content_blocks,
        )

    async def _category_summary(
        self,
        db: Session,
        version: ForecastVersion,
        category: str | None,
        context: SkillContext,
    ) -> SkillResult:
        """Summary for a specific P&L category."""
        query = self._scope(
            db.query(ForecastLineResult)
            .join(LineItem)
            .filter(ForecastLineResult.version_id == version.id),
            context,
        )
        if category:
            query = query.filter(LineItem.category.ilike(f"%{category}%"))

        results = query.order_by(LineItem.display_order, ForecastLineResult.period).all()

        if not results:
            return SkillResult.ok(
                message=f"No forecast data found for category '{category}'.",
                data={},
            )

        # Group by line item, show first period
        seen = set()
        rows = []
        for r in results:
            key = r.line_item_id
            if key not in seen:
                seen.add(key)
                rows.append({
                    "name": r.line_item.name,
                    "p50": f"${r.p50:,.0f}",
                    "confidence": f"{r.confidence_score:.0f}",
                    "model": r.model_type or "N/A",
                    "overridden": "Yes" if r.is_overridden else "No",
                })

        content_blocks = [
            self._table_block(
                title=f"{category or 'All'} Line Items",
                columns=[
                    {"key": "name", "label": "Line Item"},
                    {"key": "p50", "label": "Forecast (P50)"},
                    {"key": "confidence", "label": "Confidence"},
                    {"key": "model", "label": "Model"},
                    {"key": "overridden", "label": "Overridden"},
                ],
                rows=rows[:20],
            ),
        ]

        return SkillResult.ok(
            message=f"Found {len(rows)} line items in {category or 'all categories'}",
            data={"rows": rows},
            content_blocks=content_blocks,
        )

    async def _line_item_detail(
        self, db: Session, version: ForecastVersion, name: str, context: SkillContext
    ) -> SkillResult:
        """Detailed view of a specific line item across all forecast periods."""
        from app.services.permissions import scoped_line_items

        li = (
            scoped_line_items(db, resolve_skill_user(context))
            .filter(
                (LineItem.name.ilike(f"%{name}%"))
                | (LineItem.account_code.ilike(f"%{name}%"))
            )
            .first()
        )

        if not li:
            return SkillResult.ok(message=f"Line item '{name}' not found.", data={})

        results = (
            db.query(ForecastLineResult)
            .filter(
                ForecastLineResult.version_id == version.id,
                ForecastLineResult.line_item_id == li.id,
            )
            .order_by(ForecastLineResult.period)
            .all()
        )

        rows = [
            {
                "period": r.period,
                "p10": f"${r.p10:,.0f}" if r.p10 else "N/A",
                "p50": f"${r.p50:,.0f}",
                "p90": f"${r.p90:,.0f}" if r.p90 else "N/A",
                "confidence": f"{r.confidence_score:.0f}",
                "overridden": "Yes" if r.is_overridden else "No",
            }
            for r in results
        ]

        content_blocks = [
            self._text_block(
                f"**{li.name}** ({li.account_code}) - {li.category}"
            ),
            self._table_block(
                title=f"Forecast Detail: {li.name}",
                columns=[
                    {"key": "period", "label": "Period"},
                    {"key": "p10", "label": "P10"},
                    {"key": "p50", "label": "P50"},
                    {"key": "p90", "label": "P90"},
                    {"key": "confidence", "label": "Confidence"},
                    {"key": "overridden", "label": "Overridden"},
                ],
                rows=rows,
            ),
        ]

        return SkillResult.ok(
            message=f"{li.name}: {len(results)} periods forecasted, model={results[0].model_type if results else 'N/A'}",
            data={"line_item": li.name, "periods": len(results)},
            content_blocks=content_blocks,
        )

    async def _confidence_summary(
        self,
        db: Session,
        version: ForecastVersion,
        level: str | None,
        context: SkillContext,
    ) -> SkillResult:
        """Show items filtered by confidence level."""
        query = self._scope(
            db.query(ForecastLineResult)
            .join(LineItem)
            .filter(ForecastLineResult.version_id == version.id),
            context,
        )
        if level:
            query = query.filter(ForecastLineResult.confidence_level == level)

        results = query.order_by(ForecastLineResult.confidence_score.asc()).all()

        seen = set()
        rows = []
        for r in results:
            if r.line_item_id not in seen:
                seen.add(r.line_item_id)
                rows.append({
                    "name": r.line_item.name,
                    "category": r.line_item.category,
                    "score": f"{r.confidence_score:.0f}",
                    "level": r.confidence_level,
                    "model": r.model_type or "N/A",
                })

        content_blocks = [
            self._table_block(
                title=f"{'All' if not level else level.title()} Confidence Items ({len(rows)} lines)",
                columns=[
                    {"key": "name", "label": "Line Item"},
                    {"key": "category", "label": "Category"},
                    {"key": "score", "label": "Score"},
                    {"key": "level", "label": "Level"},
                    {"key": "model", "label": "Model"},
                ],
                rows=rows[:20],
            ),
        ]

        return SkillResult.ok(
            message=f"Found {len(rows)} {level or ''} confidence line items",
            data={"count": len(rows)},
            content_blocks=content_blocks,
        )

    async def _overrides_summary(
        self, db: Session, version: ForecastVersion, context: SkillContext
    ) -> SkillResult:
        """Show all overrides for the version."""
        from app.services.permissions import scoped_line_items

        allowed_ids = {
            li.id for li in scoped_line_items(db, resolve_skill_user(context)).all()
        }
        overrides = (
            db.query(Override)
            .filter(Override.version_id == version.id, Override.status == "active")
            .all()
        )
        overrides = [o for o in overrides if o.line_item_id in allowed_ids]

        if not overrides:
            return SkillResult.ok(
                message="No overrides applied to this forecast version.",
                data={"count": 0},
            )

        rows = [
            {
                "line": o.line_item.name,
                "period": o.period,
                "original": f"${o.original_model_value:,.0f}",
                "override": f"${o.override_value:,.0f}",
                "reason": o.reason[:50] + "..." if len(o.reason) > 50 else o.reason,
            }
            for o in overrides
        ]

        content_blocks = [
            self._table_block(
                title=f"Active Overrides ({len(overrides)})",
                columns=[
                    {"key": "line", "label": "Line Item"},
                    {"key": "period", "label": "Period"},
                    {"key": "original", "label": "Model Value"},
                    {"key": "override", "label": "Override"},
                    {"key": "reason", "label": "Reason"},
                ],
                rows=rows,
            ),
        ]

        return SkillResult.ok(
            message=f"{len(overrides)} active overrides on {version.name}",
            data={"count": len(overrides)},
            content_blocks=content_blocks,
        )

    async def _search(
        self, db: Session, version: ForecastVersion, term: str, context: SkillContext
    ) -> SkillResult:
        """Search for line items matching a term."""
        from app.services.permissions import scoped_line_items

        items = (
            scoped_line_items(db, resolve_skill_user(context))
            .filter(
                (LineItem.name.ilike(f"%{term}%"))
                | (LineItem.account_code.ilike(f"%{term}%"))
                | (LineItem.category.ilike(f"%{term}%"))
            )
            .limit(20)
            .all()
        )

        rows = [
            {
                "code": li.account_code,
                "name": li.name,
                "category": li.category,
                "bu": li.business_unit or "N/A",
            }
            for li in items
        ]

        return SkillResult.ok(
            message=f"Found {len(items)} line items matching '{term}'",
            data={"items": rows},
            content_blocks=[
                self._table_block(
                    title=f"Search Results for '{term}'",
                    columns=[
                        {"key": "code", "label": "Code"},
                        {"key": "name", "label": "Name"},
                        {"key": "category", "label": "Category"},
                        {"key": "bu", "label": "BU"},
                    ],
                    rows=rows,
                ),
            ],
        )
