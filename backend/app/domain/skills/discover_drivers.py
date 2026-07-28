"""discover_drivers skill — Phase 9 statistical driver discovery."""

from __future__ import annotations

import logging
from typing import Any

from app.domain.base_skill import BaseSkill, SkillContext, SkillResult
from app.services.permissions import (
    resolve_skill_user,
    user_can_view_line_item,
    user_has_permission,
)

logger = logging.getLogger(__name__)


class DiscoverDriversSkill(BaseSkill):
    @property
    def name(self) -> str:
        return "discover_drivers"

    @property
    def description(self) -> str:
        return (
            "Statistically discover which causal drivers explain a P&L line item. "
            "Scans driver series over a lag grid, applies FDR correction, elasticity, "
            "sign-prior and placebo gates, and records candidate links for human "
            "review. Never activates a link — promotion stays a human decision."
        )

    @property
    def required_role(self) -> str:
        return "manage_drivers"

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["run", "get"],
                    "description": "run a new discovery scan, or get a prior run",
                    "default": "run",
                },
                "line_item_id": {
                    "type": "integer",
                    "description": "Line item to explain (required for action=run)",
                },
                "run_id": {
                    "type": "string",
                    "description": "Discovery run id (required for action=get)",
                },
                "max_lag": {
                    "type": "integer",
                    "description": "Highest driver lag to test (default min(6, n/6))",
                },
                "alpha": {
                    "type": "number",
                    "description": "FDR significance threshold (default 0.05)",
                },
                "max_survivors": {
                    "type": "integer",
                    "description": "Cap on candidate links written (default 3)",
                },
                "enable_placebo": {
                    "type": "boolean",
                    "description": "Run the block-bootstrap placebo gate (default true)",
                },
            },
            "required": ["action"],
        }

    async def execute(self, params: dict[str, Any], context: SkillContext) -> SkillResult:
        user = resolve_skill_user(context)
        if user is None:
            return SkillResult.fail("Authenticated user required.")
        if not user_has_permission(user, "manage_drivers"):
            return SkillResult.fail("Permission manage_drivers required.")

        action = (params.get("action") or "run").strip()
        if action == "get":
            return self._get(params, context, user)
        if action == "run":
            return self._run(params, context, user)
        return SkillResult.fail(f"Unknown action: {action}")

    def _run(self, params: dict, context: SkillContext, user) -> SkillResult:
        from app.models.line_item import LineItem
        from app.services.driver_discovery import discover_drivers_for_line

        line_item_id = params.get("line_item_id")
        if not line_item_id:
            return SkillResult.fail("line_item_id is required to run discovery.")
        line = (
            context.db.query(LineItem).filter(LineItem.id == int(line_item_id)).first()
        )
        if line is None or not user_can_view_line_item(user, line):
            return SkillResult.fail(f"Line item {line_item_id} not found.")

        config = {
            k: params.get(k)
            for k in ("max_lag", "alpha", "max_survivors", "enable_placebo")
            if params.get(k) is not None
        }
        try:
            run = discover_drivers_for_line(
                context.db, int(line_item_id), actor=user, config=config
            )
            context.db.commit()
        except ValueError as e:
            return SkillResult.fail(str(e))
        return self._render(run, context, line_label=f"{line.account_code} — {line.name}")

    def _get(self, params: dict, context: SkillContext, user) -> SkillResult:
        from app.models.driver import DriverDiscoveryRun
        from app.models.line_item import LineItem

        run_id = (params.get("run_id") or "").strip()
        if not run_id:
            return SkillResult.fail("run_id is required for action=get.")
        run = (
            context.db.query(DriverDiscoveryRun)
            .filter(DriverDiscoveryRun.id == run_id)
            .first()
        )
        if run is None:
            return SkillResult.fail(f"Discovery run {run_id} not found.")
        label = None
        if run.line_item_id is not None:
            line = (
                context.db.query(LineItem).filter(LineItem.id == run.line_item_id).first()
            )
            if line is not None:
                if not user_can_view_line_item(user, line):
                    return SkillResult.fail(f"Discovery run {run_id} not found.")
                label = f"{line.account_code} — {line.name}"
        return self._render(run, context, line_label=label)

    def _render(self, run, context: SkillContext, *, line_label: str | None) -> SkillResult:
        from app.models.driver import DriverLink

        summary = run.summary or {}
        links = (
            context.db.query(DriverLink)
            .filter(DriverLink.discovery_run_id == run.id)
            .order_by(DriverLink.id.asc())
            .all()
        )
        candidates = summary.get("candidates") or []
        n_survivors = int(summary.get("n_survivors") or 0)
        n_tests = int(summary.get("n_tests") or 0)

        lines = [
            f"**Driver discovery — {line_label or f'line {run.line_item_id}'}**",
            f"Status `{run.status}` · {n_tests} tests · {n_survivors} candidate link(s)",
        ]
        if run.status != "completed":
            lines.append(f"Reason: {summary.get('reason', 'unknown')}")
        for link, cand in zip(links, [c for c in candidates if c.get("passed")]):
            lines.append(
                f"- `{cand.get('driver_key')}` lag {link.lag} · β={link.coefficient:,.4g}"
                f" · adj p={cand.get('p_value_adj')} · n={link.n_obs}"
                f" → link {link.id} (**{link.status}**)"
            )
        rejected = [c for c in candidates if not c.get("passed")][:5]
        if rejected:
            lines.append("Rejected (top 5):")
            for c in rejected:
                lines.append(
                    f"- `{c.get('driver_key')}` lag {c.get('lag')} — {c.get('reject_reason')}"
                )
        if links:
            lines.append(
                "_Candidates only — promote a link to make it drive the forecast._"
            )

        return SkillResult.ok(
            message=(
                f"Discovery run {run.id} ({run.status}): {n_survivors} candidate link(s) "
                f"from {n_tests} tests."
            ),
            data={
                "run_id": run.id,
                "status": run.status,
                "line_item_id": run.line_item_id,
                "n_tests": n_tests,
                "n_survivors": n_survivors,
                "link_ids": [l.id for l in links],
                "summary": summary,
            },
            content_blocks=[self._text_block("\n".join(lines))],
            panel_payload={
                "panel": "drivers",
                "params": {"line_item_id": run.line_item_id, "discovery_run_id": run.id},
                "title": "Driver Series",
            },
        )
