"""Instant forecast preview after an actuals upload (Phase E4).

A full baseline run (rolling-origin CV over the whole roster, the panel GBM,
calibration, reconciliation) takes minutes on the worker. Right after an
upload, one Chronos-2 call over every line gives a usable first look in
seconds. Nothing is persisted: it is a preview, labelled as such, while the
real run happens through the usual generate path.

Production task only (no CV), no driver covariates (an upload's drivers may
not be staged yet), sibling lines grouped by category.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any

import numpy as np
from sqlalchemy.orm import Session

from app.domain.engines.chronos2 import CHRONOS2_MIN_CONTEXT
from app.models.actuals import ActualsDataset, ActualsRecord
from app.models.line_item import LineItem

logger = logging.getLogger(__name__)

DEFAULT_HORIZON = 12
MAX_HORIZON = 24
HISTORY_POINTS_SHOWN = 12


def build_preview(db: Session, dataset: ActualsDataset, *, horizon: int = DEFAULT_HORIZON) -> dict[str, Any]:
    from app.domain.skills.generate_baseline import reindex_to_fiscal_grid
    from app.services.foundation_client import FoundationServiceUnavailable, forecast_batch
    from app.services.fx import MissingFxRateError, convert_series_values, get_reporting_currency
    from app.services.period_calendar import forecast_horizon_periods, get_calendar_config

    horizon = max(1, min(int(horizon), MAX_HORIZON))
    cal_cfg = get_calendar_config(db, business_unit_id=dataset.business_unit_id)
    ccy = get_reporting_currency(db, business_unit_id=dataset.business_unit_id)

    by_line: dict[int, list[ActualsRecord]] = defaultdict(list)
    for rec in (
        db.query(ActualsRecord)
        .filter(ActualsRecord.dataset_id == dataset.id)
        .order_by(ActualsRecord.period)
        .all()
    ):
        by_line[rec.line_item_id].append(rec)
    items = {
        li.id: li
        for li in db.query(LineItem).filter(LineItem.id.in_(list(by_line))).all()
        if not li.is_calculated and not li.is_subtotal
    }

    series: list[dict[str, Any]] = []
    meta: dict[int, dict[str, Any]] = {}
    skipped: list[dict[str, str]] = []
    for li_id, recs in by_line.items():
        li = items.get(li_id)
        if li is None:
            continue
        periods = [r.period for r in recs]
        raw = [float(r.value) for r in recs]
        try:
            raw, _ = convert_series_values(
                db, raw, [getattr(r, "currency", None) or ccy for r in recs], periods, ccy
            )
        except MissingFxRateError:
            skipped.append({"line_item": li.name, "reason": "missing FX rate"})
            continue
        periods, values, _missing = reindex_to_fiscal_grid(periods, list(raw), cal_cfg)
        values_arr = np.asarray(values, dtype=float)
        if len(values_arr) < CHRONOS2_MIN_CONTEXT or not np.all(np.isfinite(values_arr)):
            skipped.append({"line_item": li.name, "reason": f"fewer than {CHRONOS2_MIN_CONTEXT} periods"})
            continue
        if np.all(values_arr == 0):
            skipped.append({"line_item": li.name, "reason": "all zero"})
            continue
        series.append({
            "id": str(li_id),
            "target": [float(v) for v in values_arr],
            "prediction_length": horizon,
            "past_covariates": {},
            "future_covariates": {},
            "group_id": str(li.category or "_"),
        })
        meta[li_id] = {
            "line_item": li,
            "periods": [str(p) for p in periods],
            "values": values_arr,
            "future": forecast_horizon_periods(str(periods[-1]), horizon, cal_cfg),
        }

    base = {
        "dataset_id": dataset.id,
        "horizon": horizon,
        "reporting_currency": ccy,
        "preview": True,
        "skipped": skipped,
    }
    if not series:
        return {**base, "available": False, "reason": "no line has enough history", "lines": []}
    try:
        result = forecast_batch(series)
    except FoundationServiceUnavailable as e:
        return {**base, "available": False, "reason": f"forecast service unavailable ({e})", "lines": []}

    lines: list[dict[str, Any]] = []
    for li_id, m in meta.items():
        path = result.forecasts.get(str(li_id))
        if path is None:
            continue
        line: LineItem = m["line_item"]
        p10, p50, p90 = (np.asarray(x, dtype=float) for x in (path.p10, path.p50, path.p90))
        lo, hi = np.minimum(p10, p90), np.maximum(p10, p90)
        if not line.allow_negative:
            lo, p50, hi = np.maximum(lo, 0.0), np.maximum(p50, 0.0), np.maximum(hi, 0.0)
        hist_values = m["values"]
        last_year = float(hist_values[-12:].sum()) if len(hist_values) >= 12 else None
        k = min(len(p50), len(m["future"]))
        lines.append({
            "line_item_id": li_id,
            "line_item_name": line.name,
            "account_code": line.account_code,
            "category": line.category,
            "n_history": int(len(hist_values)),
            "history": [
                {"period": p, "actual": round(float(v), 2)}
                for p, v in zip(m["periods"][-HISTORY_POINTS_SHOWN:], hist_values[-HISTORY_POINTS_SHOWN:])
            ],
            "forecast": [
                {"period": m["future"][i], "p10": round(float(lo[i]), 2), "p50": round(float(p50[i]), 2),
                 "p90": round(float(hi[i]), 2)}
                for i in range(k)
            ],
            "total_p50": round(float(p50[:k].sum()), 2),
            "last_12_actual": round(last_year, 2) if last_year is not None else None,
        })
    lines.sort(key=lambda r: -abs(float(r["total_p50"])))
    return {
        **base,
        "available": True,
        "model": {k: result.provenance.get(k) for k in ("model_id", "model_revision") if result.provenance.get(k)},
        "lines": lines,
    }
