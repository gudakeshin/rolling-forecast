"""Re-score existing forecasts' confidence."""


import logging

from fastapi import Depends, HTTPException, Query, Response
from sqlalchemy.orm import Session

from app.database import get_db
from app.domain.skills.generate_baseline import _generate_remediation
from app.models.forecast import ForecastLineResult
from app.models.line_item import LineItem
from app.models.user import User
from app.services.confidence import (
    classify_confidence as _classify_confidence,
)
from app.services.confidence import (
    compute_confidence_score as _compute_confidence_score,
)
from app.services.permissions import (
    get_accessible_forecast_version,
    require_permission,
)

# Scoring lives in services.confidence; remediation stays on the skill module.

logger = logging.getLogger(__name__)


from app.api.dashboard._common import router

# ──────────────────────────────────────────────────
# Utility: Re-score existing forecasts
# ──────────────────────────────────────────────────

@router.post("/rescore-forecasts/{version_id}")
async def rescore_forecasts(
    version_id: str,
    force: bool = False,
    current_user: User = Depends(require_permission("generate")),
    db: Session = Depends(get_db),
):
    """Re-score confidence and generate remediation for an existing forecast version.

    Honors the version's pinned ``selection_rule``. When the live
    ``settings.selection_metric`` disagrees, refuse unless ``force=true``
    (audited) so FVA trends are not restamped by a rule change.
    """
    from app.domain.engines.model_registry import effective_selection_rule
    from app.services.audit import record_audit

    version = get_accessible_forecast_version(db, current_user, version_id)

    live_rule = effective_selection_rule()
    pinned = version.selection_rule
    if pinned and pinned != live_rule and not force:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Version selection_rule={pinned!r} differs from live rule={live_rule!r}. "
                "Pass force=true to rescore under the live confidence formula anyway "
                "(does not re-run model selection)."
            ),
        )

    results = (
        db.query(ForecastLineResult)
        .join(LineItem)
        .filter(ForecastLineResult.version_id == version_id)
        .all()
    )

    if not results:
        raise HTTPException(status_code=404, detail="No forecast results found")

    high = medium = low = 0

    for r in results:
        # Re-compute confidence score
        score = _compute_confidence_score(r)
        level = _classify_confidence(score)
        r.confidence_score = score
        r.confidence_level = level

        if level == "high":
            high += 1
        elif level == "medium":
            medium += 1
        else:
            low += 1

        # Generate remediation
        remed = _generate_remediation(r)
        r.ai_recommendation = remed["action"]
        r.ai_reasoning = remed["reason"]
        r.ai_risk_score = (
            0.0 if remed["severity"] == "info"
            else 40.0 if remed["severity"] == "warning"
            else 75.0
        )

    # Update version counts
    version.high_confidence_count = high
    version.medium_confidence_count = medium
    version.low_confidence_count = low

    record_audit(
        db,
        action="forecast.rescore",
        entity_type="forecast_version",
        entity_id=version_id,
        actor_id=current_user.id,
        actor_username=current_user.username,
        details={
            "lines": len(results),
            "high": high,
            "medium": medium,
            "low": low,
            "forced": force,
            "pinned_rule": pinned,
            "live_rule": live_rule,
        },
    )
    db.commit()

    return {
        "success": True,
        "message": f"Re-scored {len(results)} forecast lines for {version.name}",
        "high_confidence": high,
        "medium_confidence": medium,
        "low_confidence": low,
        "selection_rule": pinned,
        "forced": force,
    }


@router.post("/rescore-all")
async def rescore_all_forecasts(
    response: Response,
    force: bool = False,
    async_job: bool | None = Query(None, description="Run in the background (default when a queue is configured)"),
    current_user: User = Depends(require_permission("generate")),
    db: Session = Depends(get_db),
):
    """Re-score all forecast versions at once.

    Skips versions whose pinned ``selection_rule`` disagrees with the live
    rule unless ``force=true`` (audited).
    """
    from app.services.background_tasks import dispatch_task

    return await dispatch_task(
        "rescore_all", {"force": force},
        db=db, actor=current_user, async_job=async_job, response=response,
    )


