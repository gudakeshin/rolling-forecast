"""Vintage-band anomalies (Phase E5): an actual that landed outside the band
a previous forecast published for it.

``accuracy_snapshot`` already records, per (vintage, line, period), whether
the actual fell inside that vintage's P10-P90. A miss on the most recent
vintage that covered the period (the shortest horizon, so the best-informed
band) is the signal: either something unusual happened, or the band was too
narrow. Severity is the distance outside the band in band widths.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.models.fx import ForecastAccuracyRecord

MAX_PERIODS = 6


def _severity(distance_in_widths: float) -> str:
    if distance_in_widths > 1.0:
        return "critical"
    if distance_in_widths > 0.5:
        return "warning"
    return "info"


def vintage_band_misses(
    db: Session, line_item_ids: list[int], *, max_periods: int = MAX_PERIODS
) -> dict[int, list[dict[str, Any]]]:
    """Per line, findings for its last ``max_periods`` actuals that fell
    outside the closest prior vintage's P10-P90."""
    if not line_item_ids:
        return {}
    rows = (
        db.query(ForecastAccuracyRecord)
        .filter(
            ForecastAccuracyRecord.line_item_id.in_(line_item_ids),
            ForecastAccuracyRecord.predicted_p10.isnot(None),
            ForecastAccuracyRecord.predicted_p90.isnot(None),
            ForecastAccuracyRecord.within_p10_p90.isnot(None),
        )
        .all()
    )
    # Closest vintage per (line, period): shortest horizon, then the newest.
    closest: dict[tuple[int, str], ForecastAccuracyRecord] = {}
    for r in rows:
        key = (r.line_item_id, r.period)
        cur = closest.get(key)
        if cur is None or (r.horizon_offset, -_ts(r)) < (cur.horizon_offset, -_ts(cur)):
            closest[key] = r

    by_line: dict[int, list[ForecastAccuracyRecord]] = {}
    for (li_id, _), r in closest.items():
        by_line.setdefault(li_id, []).append(r)

    out: dict[int, list[dict[str, Any]]] = {}
    for li_id, recs in by_line.items():
        recent = sorted(recs, key=lambda r: r.period)[-max_periods:]
        findings = []
        for r in recent:
            if r.within_p10_p90:
                continue
            lo, hi = float(r.predicted_p10), float(r.predicted_p90)  # type: ignore[arg-type]
            lo, hi = min(lo, hi), max(lo, hi)
            width = hi - lo
            above = r.actual > hi
            gap = (r.actual - hi) if above else (lo - r.actual)
            if gap <= 0:
                continue  # recorded as a miss but on the edge; nothing to show
            dist = gap / width if width > 1e-9 else float("inf")
            findings.append({
                "type": "vintage_band_miss",
                "severity": _severity(dist),
                "period": r.period,
                "value": float(r.actual),
                "expected": float(r.predicted_p50),
                "p10": lo,
                "p90": hi,
                "horizon_offset": int(r.horizon_offset),
                "vintage_version_id": r.version_id,
                "direction": "above" if above else "below",
                "band_widths_outside": round(dist, 2) if dist != float("inf") else None,
            })
        if findings:
            out[li_id] = findings
    return out


def _ts(r: ForecastAccuracyRecord) -> float:
    return r.created_at.timestamp() if r.created_at is not None else 0.0
