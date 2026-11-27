"""Second opinion (Phase E2): does the published forecast sit inside Chronos-2's band?

Every run already has Chronos-2's P10/P90 for each line (the range blend),
so a line whose selected P50 falls outside that band for most of the horizon
is one where two very different models disagree, and a reviewer should look.
This never changes a number: it adds a flag to the line's remediation, a
``second_opinion`` entry on its model metadata (shown in Why-this-number),
and puts the line in the review queue.
"""

from __future__ import annotations

from typing import Any

import numpy as np

# Line flag text; generate_baseline's remediation and the review queue key on
# this prefix.
DISAGREE_FLAG = "Models disagree"
# Share of horizon steps outside the band at which a line is flagged.
DISAGREE_SHARE = 0.5


def second_opinion(
    point: np.ndarray,
    fm_p10: np.ndarray,
    fm_p50: np.ndarray,
    fm_p90: np.ndarray,
    periods: list[str],
    *,
    share_threshold: float = DISAGREE_SHARE,
) -> dict[str, Any] | None:
    """Compare a published P50 path with Chronos-2's band, step by step."""
    point = np.asarray(point, dtype=float)
    n = min(len(point), len(fm_p10), len(fm_p50), len(fm_p90), len(periods))
    if n == 0:
        return None
    lo = np.minimum(np.asarray(fm_p10, dtype=float)[:n], np.asarray(fm_p90, dtype=float)[:n])
    hi = np.maximum(np.asarray(fm_p10, dtype=float)[:n], np.asarray(fm_p90, dtype=float)[:n])
    mid = np.asarray(fm_p50, dtype=float)[:n]
    if not (np.all(np.isfinite(lo)) and np.all(np.isfinite(hi)) and np.all(np.isfinite(mid))):
        return None
    p = point[:n]
    above = p > hi
    below = p < lo
    outside = above | below
    share = float(outside.mean())
    return {
        "model": "chronos2",
        "periods": list(periods[:n]),
        "p10": [round(float(v), 4) for v in lo],
        "p50": [round(float(v), 4) for v in mid],
        "p90": [round(float(v), 4) for v in hi],
        "outside_periods": [periods[i] for i in range(n) if outside[i]],
        "outside_share": round(share, 4),
        # Which way the published forecast sits relative to Chronos-2.
        "direction": (
            "above" if above.sum() > below.sum() else "below" if below.sum() > above.sum() else "mixed"
        )
        if outside.any()
        else None,
        "disagree": bool(share >= share_threshold),
    }


def disagree_flag(op: dict[str, Any]) -> str:
    """The line flag shown in remediation for a disagreeing second opinion."""
    n_out = len(op.get("outside_periods") or [])
    n = len(op.get("periods") or [])
    side = op.get("direction")
    where = f" ({side} its range)" if side in ("above", "below") else ""
    return (
        f"{DISAGREE_FLAG}: the forecast is outside Chronos-2's P10-P90{where} "
        f"in {n_out} of {n} periods"
    )


def cell_view(op: dict[str, Any] | None, period: str) -> dict[str, Any] | None:
    """The second opinion for one forecast cell (Why-this-number)."""
    if not isinstance(op, dict):
        return None
    periods = op.get("periods") or []
    if period not in periods:
        return None
    i = periods.index(period)
    return {
        "model": op.get("model"),
        "p10": op["p10"][i],
        "p50": op["p50"][i],
        "p90": op["p90"][i],
        "outside": period in (op.get("outside_periods") or []),
        "line_outside_share": op.get("outside_share"),
        "line_disagrees": op.get("disagree"),
        "direction": op.get("direction"),
    }
