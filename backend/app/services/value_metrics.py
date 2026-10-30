"""Is the product paying off? Per-version value metrics (enhancement plan 6.4).

For each recent version of one company:

- ``cycle_days``      created -> approved/published, in days (falling is good)
- ``override_rate``   share of forecast rows a human overrode (falling is good:
                      the baseline needs less correction)
- ``flagged_review_share``  of the rows reviewers acted on, the share the AI
                      had flagged (rising is good: attention goes where it's needed)
- ``fva_vs_naive``    naive WAPE minus published WAPE, in points, on the
                      version's closed actuals (positive = beats a naive forecast)

plus a first-to-latest trend for each, labelled better/worse by direction.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from sqlalchemy.orm import Session

from app.models.forecast import ForecastLineResult, ForecastVersion
from app.models.fx import ForecastAccuracyRecord
from app.services.accuracy_snapshot import compute_fva

# metric -> True when a higher value is better
METRICS: dict[str, bool] = {
    "cycle_days": False,
    "override_rate": False,
    "flagged_review_share": True,
    "fva_vs_naive": True,
}


def _ratio(numer: int, denom: int) -> float | None:
    return round(numer / denom, 4) if denom else None


def compute_value_metrics(db: Session, *, business_unit_id: str, limit: int = 12) -> dict[str, Any]:
    versions = (
        db.query(ForecastVersion)
        .filter(ForecastVersion.business_unit_id == business_unit_id)
        .order_by(ForecastVersion.created_at.desc())
        .limit(limit)
        .all()
    )[::-1]  # oldest first, for trends
    ids = [v.id for v in versions]

    rows: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    if ids:
        for version_id, is_overridden, review_status, ai_rec in (
            db.query(
                ForecastLineResult.version_id,
                ForecastLineResult.is_overridden,
                ForecastLineResult.review_status,
                ForecastLineResult.ai_recommendation,
            )
            .filter(ForecastLineResult.version_id.in_(ids))
            .all()
        ):
            r = rows[version_id]
            r["total"] += 1
            r["overridden"] += int(bool(is_overridden))
            if review_status:
                r["reviewed"] += 1
                r["reviewed_flagged"] += int(bool(ai_rec) and ai_rec != "approve")

    accuracy: dict[str, list[tuple[float, float, float]]] = defaultdict(list)
    if ids:
        for version_id, published, actual, naive in (
            db.query(
                ForecastAccuracyRecord.version_id,
                ForecastAccuracyRecord.predicted_p50,
                ForecastAccuracyRecord.actual,
                ForecastAccuracyRecord.naive_p50,
            )
            .filter(
                ForecastAccuracyRecord.version_id.in_(ids),
                ForecastAccuracyRecord.naive_p50.isnot(None),
            )
            .all()
        ):
            accuracy[version_id].append((float(published), float(actual), float(naive)))

    out: list[dict[str, Any]] = []
    for v in versions:
        r = rows.get(v.id, {})
        done_at = v.published_at or v.approved_at
        cycle = (
            round((done_at - v.created_at).total_seconds() / 86400, 1)
            if done_at and v.created_at
            else None
        )
        acc = accuracy.get(v.id)
        fva = (
            compute_fva([p for p, _, _ in acc], [a for _, a, _ in acc], [n for _, _, n in acc])
            if acc
            else None
        )
        out.append({
            "version_id": v.id,
            "version_name": v.name,
            "status": v.status,
            "created_at": v.created_at.isoformat() if v.created_at else None,
            "cycle_days": cycle,
            "override_rate": _ratio(r.get("overridden", 0), r.get("total", 0)),
            "flagged_review_share": _ratio(r.get("reviewed_flagged", 0), r.get("reviewed", 0)),
            "fva_vs_naive": round(fva, 2) if fva is not None else None,
        })

    trends: dict[str, dict[str, Any]] = {}
    for metric, higher_is_better in METRICS.items():
        series = [v[metric] for v in out if v[metric] is not None]
        if len(series) < 2:
            trends[metric] = {"latest": series[-1] if series else None, "change": None, "improving": None}
            continue
        change = round(series[-1] - series[0], 4)
        trends[metric] = {
            "latest": series[-1],
            "change": change,
            "improving": None if change == 0 else (change > 0) == higher_is_better,
        }
    return {"business_unit_id": business_unit_id, "versions": out, "trends": trends}
