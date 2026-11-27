"""Forecast-process diagnostics from closed vintages (Phase F, P2).

All three read ``ForecastAccuracyRecord`` rows, which ``accuracy_snapshot``
writes when an actual arrives for a period an earlier version forecast:

* **Bias and tracking signal per line** — the running sum of errors over
  the mean absolute error (Brown's tracking signal). |TS| > 4 means the
  forecast is consistently on one side, not just noisy.
* **Revision stability** — how much the forecast for one period moved from
  vintage to vintage before its actual arrived, and how often it reversed
  direction. Large or flip-flopping revisions are churn a planner pays for.
* **FVA by user and reason** — for each override that touched a period that
  has since closed, the model's absolute error minus the published one:
  positive means the override helped. Summed by who made it and by a
  keyword theme of the reason text.
"""

from __future__ import annotations

import math
import re
from collections import defaultdict
from typing import Any, Iterable

from sqlalchemy.orm import Session

from app.models.fx import ForecastAccuracyRecord
from app.models.override import Override

TRACKING_SIGNAL_LIMIT = 4.0
MIN_BIAS_OBSERVATIONS = 4

# Keyword themes for free-text override reasons, checked in order.
REASON_THEMES: list[tuple[str, re.Pattern[str]]] = [
    ("pricing", re.compile(r"\b(price|pricing|discount|rebate|promo\w*|margin)\b", re.I)),
    ("timing", re.compile(r"\b(timing|delay\w*|slip\w*|pull\w* forward|phas\w*|accrual\w*)\b", re.I)),
    ("pipeline / deals", re.compile(r"\b(pipeline|deal\w*|contract\w*|order\w*|customer\w*|win|won|lost)\b", re.I)),
    ("plan / budget", re.compile(r"\b(plan|budget|target\w*|guidance|forecast call)\b", re.I)),
    ("one-off", re.compile(r"\b(one[- ]?off|one[- ]?time|non[- ]?recurring|exception\w*|unusual)\b", re.I)),
]


def reason_theme(reason: str | None) -> str:
    for theme, pattern in REASON_THEMES:
        if reason and pattern.search(reason):
            return theme
    return "other"


def _closest_vintage(records: Iterable[ForecastAccuracyRecord]) -> dict[tuple[int, str], ForecastAccuracyRecord]:
    """Per (line, period), the shortest-horizon (best-informed) record."""
    out: dict[tuple[int, str], ForecastAccuracyRecord] = {}
    for r in records:
        key = (r.line_item_id, r.period)
        cur = out.get(key)
        if cur is None or r.horizon_offset < cur.horizon_offset:
            out[key] = r
    return out


def bias_signals(
    records: list[ForecastAccuracyRecord], *, min_obs: int = MIN_BIAS_OBSERVATIONS
) -> list[dict[str, Any]]:
    """Per line: mean bias (% of actual), tracking signal, and a flag."""
    by_line: dict[int, list[ForecastAccuracyRecord]] = defaultdict(list)
    for r in _closest_vintage(records).values():
        by_line[r.line_item_id].append(r)
    out: list[dict[str, Any]] = []
    for li_id, recs in by_line.items():
        recs.sort(key=lambda r: r.period)
        errors = [float(r.predicted_p50) - float(r.actual) for r in recs]
        if len(errors) < min_obs:
            continue
        mad = sum(abs(e) for e in errors) / len(errors)
        ts = sum(errors) / mad if mad > 1e-12 else 0.0
        actual_total = sum(abs(float(r.actual)) for r in recs)
        out.append({
            "line_item_id": li_id,
            "n": len(errors),
            "mean_error": round(sum(errors) / len(errors), 2),
            "bias_pct": round(sum(errors) / actual_total * 100, 2) if actual_total > 1e-12 else None,
            "tracking_signal": round(ts, 2),
            "biased": abs(ts) > TRACKING_SIGNAL_LIMIT,
            "direction": "over" if ts > 0 else "under" if ts < 0 else None,
        })
    out.sort(key=lambda d: -abs(float(d["tracking_signal"])))
    return out


def revision_stability(records: list[ForecastAccuracyRecord]) -> dict[str, Any]:
    """Vintage-to-vintage revisions of closed periods, pooled and per line.

    For each (line, period) with at least two vintages, ordered from the
    longest horizon to the shortest: mean absolute revision as a % of the
    actual, and the share of consecutive revisions that reversed direction.
    """
    by_cell: dict[tuple[int, str], list[ForecastAccuracyRecord]] = defaultdict(list)
    for r in records:
        by_cell[(r.line_item_id, r.period)].append(r)
    per_line: dict[int, dict[str, list[float]]] = defaultdict(lambda: {"rev": [], "flip": []})
    for (li_id, _), recs in by_cell.items():
        if len(recs) < 2:
            continue
        recs.sort(key=lambda r: -r.horizon_offset)
        actual = abs(float(recs[-1].actual))
        if actual <= 1e-12:
            continue
        p = [float(r.predicted_p50) for r in recs]
        revs = [b - a for a, b in zip(p, p[1:])]
        per_line[li_id]["rev"].extend(abs(x) / actual * 100 for x in revs)
        moves = [x for x in revs if abs(x) > 1e-9]
        per_line[li_id]["flip"].extend(
            1.0 if (a > 0) != (b > 0) else 0.0 for a, b in zip(moves, moves[1:])
        )
    lines: list[dict[str, Any]] = []
    all_rev: list[float] = []
    all_flip: list[float] = []
    for li_id, d in per_line.items():
        if not d["rev"]:
            continue
        all_rev.extend(d["rev"])
        all_flip.extend(d["flip"])
        lines.append({
            "line_item_id": li_id,
            "mean_abs_revision_pct": round(sum(d["rev"]) / len(d["rev"]), 2),
            "flip_rate": round(sum(d["flip"]) / len(d["flip"]), 3) if d["flip"] else None,
            "n_revisions": len(d["rev"]),
        })
    lines.sort(key=lambda d: -float(d["mean_abs_revision_pct"]))
    return {
        "mean_abs_revision_pct": round(sum(all_rev) / len(all_rev), 2) if all_rev else None,
        "flip_rate": round(sum(all_flip) / len(all_flip), 3) if all_flip else None,
        "n_revisions": len(all_rev),
        "lines": lines,
    }


def fva_by_user_and_reason(
    db: Session, records: list[ForecastAccuracyRecord]
) -> dict[str, list[dict[str, Any]]]:
    """Override value added on closed periods, by user and by reason theme."""
    rec_by_key = {(r.version_id, r.line_item_id, r.period): r for r in records}
    if not rec_by_key:
        return {"by_user": [], "by_reason": []}
    version_ids = {k[0] for k in rec_by_key}
    overrides = (
        db.query(Override)
        .filter(Override.version_id.in_(version_ids), Override.status == "active")
        .all()
    )
    users: dict[str, dict[str, Any]] = {}
    themes: dict[str, dict[str, Any]] = {}
    for o in overrides:
        r = rec_by_key.get((o.version_id, o.line_item_id, o.period))
        if r is None:
            continue
        model = float(r.model_p50) if r.model_p50 is not None else float(o.original_model_value)
        actual = float(r.actual)
        gain = abs(model - actual) - abs(float(r.predicted_p50) - actual)
        name = o.user.username if getattr(o, "user", None) is not None else str(o.user_id)
        for bucket, key in ((users, name), (themes, reason_theme(o.reason))):
            b = bucket.setdefault(key, {"n": 0, "helped": 0, "value_added": 0.0, "abs_actual": 0.0})
            b["n"] += 1
            b["helped"] += 1 if gain > 0 else 0
            b["value_added"] += gain
            b["abs_actual"] += abs(actual)

    def rows(bucket: dict[str, dict[str, Any]], label: str) -> list[dict[str, Any]]:
        out = []
        for key, b in bucket.items():
            out.append({
                label: key,
                "overrides": b["n"],
                "helped_share": round(b["helped"] / b["n"], 3),
                "value_added": round(b["value_added"], 2),
                # Value added as % of the actuals those overrides touched.
                "fva_pct": round(b["value_added"] / b["abs_actual"] * 100, 2)
                if b["abs_actual"] > 1e-12 else None,
            })
        out.sort(key=lambda d: -d["value_added"])
        return out

    return {"by_user": rows(users, "user"), "by_reason": rows(themes, "reason_theme")}


def diagnostics(db: Session, line_item_ids: list[int]) -> dict[str, Any]:
    """All three, over every closed vintage of the given lines."""
    if not line_item_ids:
        return {"bias_signals": [], "revision_stability": revision_stability([]),
                "fva_by_user": [], "fva_by_reason": []}
    records = (
        db.query(ForecastAccuracyRecord)
        .filter(ForecastAccuracyRecord.line_item_id.in_(line_item_ids))
        .all()
    )
    records = [r for r in records if math.isfinite(float(r.actual))]
    fva = fva_by_user_and_reason(db, records)
    return {
        "bias_signals": bias_signals(records),
        "revision_stability": revision_stability(records),
        "fva_by_user": fva["by_user"],
        "fva_by_reason": fva["by_reason"],
    }
