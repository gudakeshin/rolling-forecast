"""BranchForecast skill -- scenario branching, comparison, and merge."""

import logging
from typing import Any

from sqlalchemy.orm import Session
from sqlalchemy import func

from app.domain.base_skill import BaseSkill, SkillContext, SkillResult
from app.models.forecast import ForecastVersion, ForecastLineResult
from app.models.line_item import LineItem

logger = logging.getLogger(__name__)


class BranchForecastSkill(BaseSkill):
    """Skill to create and manage forecast scenario branches."""

    @property
    def name(self) -> str:
        return "branch_forecast"

    @property
    def description(self) -> str:
        return (
            "Create scenario branches from an existing forecast version. Supports "
            "what-if analysis by creating independent copies that can be modified "
            "without affecting the original."
        )

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "description": "Action: create_branch, list_branches, compare_to_base, merge_to_base",
                    "enum": ["create_branch", "list_branches", "compare_to_base", "merge_to_base"],
                },
                "source_version_id": {
                    "type": "string",
                    "description": "Source version to branch from (uses active version if not specified)",
                },
                "branch_name": {
                    "type": "string",
                    "description": "Name for the new branch",
                },
                "branch_version_id": {
                    "type": "string",
                    "description": "Branch version ID (for compare or merge actions)",
                },
                "adjustments": {
                    "type": "object",
                    "description": "Bulk adjustments: {category: pct_change}",
                },
                "description": {
                    "type": "string",
                    "description": "Description of the scenario",
                },
            },
            "required": ["action"],
        }

    @property
    def required_role(self) -> str:
        return "generate"

    async def execute(self, params: dict[str, Any], context: SkillContext) -> SkillResult:
        db: Session = context.db
        action = params.get("action", "list_branches")

        if action == "create_branch":
            return await self._create_branch(db, params, context)
        elif action == "list_branches":
            return await self._list_branches(db, params, context)
        elif action == "compare_to_base":
            return await self._compare_to_base(db, params, context)
        elif action == "merge_to_base":
            return await self._merge_to_base(db, params, context)
        else:
            return SkillResult.fail(f"Unknown action: {action}")

    async def _create_branch(
        self, db: Session, params: dict[str, Any], context: SkillContext
    ) -> SkillResult:
        """Create a scenario branch by deep-copying a forecast version."""
        source_id = params.get("source_version_id") or context.context_manager.get_active_version_id()
        if not source_id:
            return SkillResult.fail("No source version specified or active.")

        source = db.query(ForecastVersion).filter(ForecastVersion.id == source_id).first()
        if not source:
            return SkillResult.fail(f"Source version '{source_id}' not found.")

        branch_name = params.get("branch_name") or f"{source.name}-scenario"
        description = params.get("description", "")
        adjustments = params.get("adjustments", {})

        # Create the branch version
        branch = ForecastVersion(
            name=branch_name,
            status="draft",
            version_type="scenario",
            parent_version_id=source.id,
            actuals_dataset_id=source.actuals_dataset_id,
            actuals_hash=source.actuals_hash,
            horizon_months=source.horizon_months,
            base_period=source.base_period,
            random_seed=source.random_seed,
            total_line_items=source.total_line_items,
            high_confidence_count=source.high_confidence_count,
            medium_confidence_count=source.medium_confidence_count,
            low_confidence_count=source.low_confidence_count,
            notes=description,
        )
        db.add(branch)
        db.flush()

        # Deep-copy all line results
        source_results = (
            db.query(ForecastLineResult)
            .filter(ForecastLineResult.version_id == source.id)
            .all()
        )

        adjusted_count = 0
        for r in source_results:
            new_r = ForecastLineResult(
                version_id=branch.id,
                line_item_id=r.line_item_id,
                period=r.period,
                p10=r.p10,
                p50=r.p50,
                p90=r.p90,
                confidence_score=r.confidence_score,
                confidence_level=r.confidence_level,
                model_type=r.model_type,
                model_mape=r.model_mape,
                model_r_squared=r.model_r_squared,
                is_overridden=r.is_overridden,
                override_value=r.override_value,
            )

            # Apply bulk adjustments by category — leaf (non-calculated) items only
            if adjustments:
                li = db.query(LineItem).filter(LineItem.id == r.line_item_id).first()
                if li and not li.is_calculated:
                    for cat_pattern, pct_change in adjustments.items():
                        if cat_pattern.lower() in (li.category or "").lower():
                            factor = 1 + (pct_change / 100.0)
                            new_r.p50 = r.p50 * factor
                            if new_r.p10 is not None:
                                new_r.p10 = r.p10 * factor
                            if new_r.p90 is not None:
                                new_r.p90 = r.p90 * factor
                            new_r.is_overridden = True
                            adjusted_count += 1
                            break

            db.add(new_r)

        db.flush()

        # Recompute calculated lines (EBITDA, GM, etc.) for P&L coherence
        from app.services.dependency_graph import DependencyGraphManager
        recalc = DependencyGraphManager(db).recalculate_all(branch.id)
        db.commit()

        # Build response
        adj_text = ""
        if adjustments:
            adj_parts = [f"{cat}: {'+' if v > 0 else ''}{v}%" for cat, v in adjustments.items()]
            adj_text = f"\nAdjustments applied: {', '.join(adj_parts)} ({adjusted_count} line-periods adjusted)"

        content_blocks = [
            self._text_block(
                f"Created scenario branch **{branch.name}** from {source.name} "
                f"with {len(source_results)} line results copied."
                + adj_text
            ),
            self._table_block(
                title="Branch Details",
                columns=[
                    {"key": "field", "label": "Field"},
                    {"key": "value", "label": "Value"},
                ],
                rows=[
                    {"field": "Branch Name", "value": branch.name},
                    {"field": "Source", "value": source.name},
                    {"field": "Type", "value": "Scenario"},
                    {"field": "Lines Copied", "value": str(len(source_results))},
                    {"field": "Adjustments", "value": str(adjusted_count) if adjustments else "None"},
                    {"field": "Description", "value": description or "N/A"},
                ],
            ),
        ]

        return SkillResult.ok(
            message=f"Created scenario branch '{branch.name}' from {source.name} ({len(source_results)} lines, {adjusted_count} adjusted)",
            data={
                "branch_version_id": branch.id,
                "branch_name": branch.name,
                "source_version_id": source.id,
                "lines_copied": len(source_results),
                "lines_adjusted": adjusted_count,
            },
            content_blocks=content_blocks,
        )

    async def _list_branches(
        self, db: Session, params: dict[str, Any], context: SkillContext
    ) -> SkillResult:
        """List all scenario branches for a version."""
        source_id = params.get("source_version_id") or context.context_manager.get_active_version_id()

        if source_id:
            branches = (
                db.query(ForecastVersion)
                .filter(ForecastVersion.parent_version_id == source_id)
                .order_by(ForecastVersion.created_at.desc())
                .all()
            )
        else:
            branches = (
                db.query(ForecastVersion)
                .filter(ForecastVersion.version_type == "scenario")
                .order_by(ForecastVersion.created_at.desc())
                .limit(20)
                .all()
            )

        if not branches:
            return SkillResult.ok(
                message="No scenario branches found.",
                content_blocks=[self._text_block("No scenario branches exist. Create one to start what-if analysis.")],
            )

        rows = [
            {
                "name": b.name,
                "parent": b.parent_version_id[:8] + "..." if b.parent_version_id else "N/A",
                "status": b.status,
                "lines": str(b.total_line_items),
                "overrides": str(b.override_count),
                "created": b.created_at.strftime("%Y-%m-%d %H:%M") if b.created_at else "",
            }
            for b in branches
        ]

        return SkillResult.ok(
            message=f"Found {len(branches)} scenario branches.",
            data={"branches": [{"id": b.id, "name": b.name} for b in branches]},
            content_blocks=[
                self._table_block(
                    title=f"Scenario Branches ({len(branches)})",
                    columns=[
                        {"key": "name", "label": "Branch"},
                        {"key": "parent", "label": "Parent"},
                        {"key": "status", "label": "Status"},
                        {"key": "lines", "label": "Lines"},
                        {"key": "overrides", "label": "Overrides"},
                        {"key": "created", "label": "Created"},
                    ],
                    rows=rows,
                ),
            ],
        )

    async def _compare_to_base(
        self, db: Session, params: dict[str, Any], context: SkillContext
    ) -> SkillResult:
        """Compare a branch to its parent/base version."""
        branch_id = params.get("branch_version_id")
        if not branch_id:
            return SkillResult.fail("Branch version ID is required for comparison.")

        branch = db.query(ForecastVersion).filter(ForecastVersion.id == branch_id).first()
        if not branch:
            return SkillResult.fail(f"Branch '{branch_id}' not found.")

        base_id = branch.parent_version_id
        if not base_id:
            return SkillResult.fail("Branch has no parent version to compare against.")

        base = db.query(ForecastVersion).filter(ForecastVersion.id == base_id).first()
        if not base:
            return SkillResult.fail(f"Base version '{base_id}' not found.")

        # Aggregate by line item for both versions
        def _aggregate(version_id):
            return {
                row.line_item_id: float(row.total)
                for row in (
                    db.query(
                        ForecastLineResult.line_item_id,
                        func.sum(ForecastLineResult.p50).label("total"),
                    )
                    .filter(ForecastLineResult.version_id == version_id)
                    .group_by(ForecastLineResult.line_item_id)
                    .all()
                )
            }

        base_vals = _aggregate(base_id)
        branch_vals = _aggregate(branch_id)

        variances = []
        for li_id in set(base_vals.keys()) | set(branch_vals.keys()):
            base_v = base_vals.get(li_id, 0)
            branch_v = branch_vals.get(li_id, 0)
            diff = branch_v - base_v
            pct = (diff / abs(base_v) * 100) if base_v != 0 else 0

            if abs(pct) > 0.1:  # Only show meaningful changes
                li = db.query(LineItem).filter(LineItem.id == li_id).first()
                variances.append({
                    "line_item": li.name if li else "Unknown",
                    "category": li.category if li else "N/A",
                    "base": base_v,
                    "branch": branch_v,
                    "diff": diff,
                    "pct": pct,
                })

        variances.sort(key=lambda x: abs(x["pct"]), reverse=True)

        total_base = sum(base_vals.values())
        total_branch = sum(branch_vals.values())
        total_pct = ((total_branch - total_base) / abs(total_base) * 100) if total_base else 0

        rows = [
            {
                "line_item": v["line_item"],
                "category": v["category"],
                "base": f"${v['base']:,.0f}",
                "branch": f"${v['branch']:,.0f}",
                "change": f"{'+' if v['pct'] > 0 else ''}{v['pct']:.1f}%",
            }
            for v in variances[:20]
        ]

        content_blocks = [
            self._text_block(
                f"**Scenario Comparison: {branch.name} vs {base.name}**\n"
                f"Overall change: **{'+' if total_pct > 0 else ''}{total_pct:.1f}%** "
                f"(${total_base:,.0f} → ${total_branch:,.0f})"
            ),
            self._table_block(
                title=f"Top Variances ({len(variances)} changed items)",
                columns=[
                    {"key": "line_item", "label": "Line Item"},
                    {"key": "category", "label": "Category"},
                    {"key": "base", "label": base.name},
                    {"key": "branch", "label": branch.name},
                    {"key": "change", "label": "% Change"},
                ],
                rows=rows,
            ),
        ]

        return SkillResult.ok(
            message=f"Comparison: {branch.name} vs {base.name} — {'+' if total_pct > 0 else ''}{total_pct:.1f}% overall change",
            data={
                "base_version_id": base_id,
                "branch_version_id": branch_id,
                "total_change_pct": total_pct,
                "variance_count": len(variances),
            },
            content_blocks=content_blocks,
        )

    async def _merge_to_base(
        self, db: Session, params: dict[str, Any], context: SkillContext
    ) -> SkillResult:
        """Promote a branch's values into a new version (doesn't overwrite original)."""
        branch_id = params.get("branch_version_id")
        if not branch_id:
            return SkillResult.fail("Branch version ID is required for merge.")

        branch = db.query(ForecastVersion).filter(ForecastVersion.id == branch_id).first()
        if not branch:
            return SkillResult.fail(f"Branch '{branch_id}' not found.")

        # Create a new merged version
        merged = ForecastVersion(
            name=f"{branch.name}-merged",
            status="draft",
            version_type="scheduled",
            actuals_dataset_id=branch.actuals_dataset_id,
            actuals_hash=branch.actuals_hash,
            horizon_months=branch.horizon_months,
            base_period=branch.base_period,
            random_seed=branch.random_seed,
            notes=f"Merged from scenario branch {branch.name}",
        )
        db.add(merged)
        db.flush()

        # Copy all results from branch
        branch_results = (
            db.query(ForecastLineResult)
            .filter(ForecastLineResult.version_id == branch.id)
            .all()
        )

        for r in branch_results:
            new_r = ForecastLineResult(
                version_id=merged.id,
                line_item_id=r.line_item_id,
                period=r.period,
                p10=r.p10, p50=r.p50, p90=r.p90,
                confidence_score=r.confidence_score,
                confidence_level=r.confidence_level,
                model_type=r.model_type,
                model_mape=r.model_mape,
                model_r_squared=r.model_r_squared,
                is_overridden=r.is_overridden,
                override_value=r.override_value,
            )
            db.add(new_r)

        merged.total_line_items = len(set(r.line_item_id for r in branch_results))
        db.commit()

        context.context_manager.set_active_version_id(merged.id)

        return SkillResult.ok(
            message=f"Merged branch '{branch.name}' into new version '{merged.name}'",
            data={
                "merged_version_id": merged.id,
                "merged_name": merged.name,
                "branch_name": branch.name,
                "lines_merged": len(branch_results),
            },
            content_blocks=[
                self._text_block(
                    f"Branch **{branch.name}** merged into new version **{merged.name}** "
                    f"with {len(branch_results)} line results. "
                    f"The merged version is now the active forecast."
                ),
            ],
        )
