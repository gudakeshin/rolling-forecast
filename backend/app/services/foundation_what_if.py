"""Driver-aware what-if through Chronos-2 (Phase E3).

The link path in ``scenario_what_if`` pushes a driver shock through one
coefficient per link: linear, no lag shape beyond the link's lag, and no
interaction with the line's own dynamics. Here Chronos-2 forecasts each
driver-linked line twice from the same history, once with the base driver
path and once with the shocked path as its known-future covariate. The
difference of the two P50 paths is the shock's effect, which is added to the
published forecast, so the baseline's level (overrides, reconciliation)
stays and only the driver effect comes from the foundation model.

Both forecasts go in one call to fm-service. Returns ``None`` per line when
the line has too little history, no usable driver covariate, or the service
is unavailable; the caller falls back to the link coefficients for those.
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np
import pandas as pd
from sqlalchemy.orm import Session

from app.domain.engines.chronos2 import CHRONOS2_MIN_POINTS
from app.models.actuals import ActualsRecord
from app.models.forecast import ForecastVersion

logger = logging.getLogger(__name__)

N_DRIVER_LINKS = 3


def _line_history(
    db: Session, version: ForecastVersion, line_item_id: int, cal_cfg: Any
) -> tuple[list[str], list[float]] | None:
    """The line's actuals on the fiscal grid, in the version's reporting currency."""
    from app.domain.skills.generate_baseline import reindex_to_fiscal_grid
    from app.services.fx import MissingFxRateError, convert_series_values

    if not version.actuals_dataset_id:
        return None
    records = (
        db.query(ActualsRecord)
        .filter(
            ActualsRecord.dataset_id == version.actuals_dataset_id,
            ActualsRecord.line_item_id == line_item_id,
        )
        .order_by(ActualsRecord.period)
        .all()
    )
    if not records:
        return None
    periods = [r.period for r in records]
    raw = [float(r.value) for r in records]
    ccy = version.reporting_currency
    if ccy:
        try:
            raw, _ = convert_series_values(
                db, raw, [getattr(r, "currency", None) or ccy for r in records], periods, ccy
            )
        except MissingFxRateError:
            return None
    periods, values, _missing = reindex_to_fiscal_grid(periods, list(raw), cal_cfg)
    return [str(p) for p in periods], [float(v) for v in values]


def foundation_driver_deltas(
    db: Session,
    *,
    version: ForecastVersion,
    line_item_ids: list[int],
    shocked_by_driver: dict[int, dict[str, float]],
    periods: list[str],
    cal_cfg: Any,
) -> tuple[dict[int, dict[str, float]], str | None]:
    """Per line, ``{period: P50 change}`` from re-forecasting under the shock.

    Returns ``(deltas, skip_reason)``; ``skip_reason`` is set when nothing
    could be computed at all (service down, no eligible line).
    """
    from app.services.driver_exog import build_exog_for_line
    from app.services.foundation_client import FoundationServiceUnavailable, forecast_batch
    from app.services.foundation_forecast import _finite_columns

    overlays = {
        int(d): pd.Series(path, dtype=float) for d, path in shocked_by_driver.items() if path
    }
    if not overlays:
        return {}, "no shocked driver path"

    series: list[dict[str, Any]] = []
    future_by_line: dict[int, list[str]] = {}
    for li_id in line_item_ids:
        hist = _line_history(db, version, li_id, cal_cfg)
        if hist is None:
            continue
        hist_periods, values = hist
        future = [p for p in periods if p > hist_periods[-1]]
        if len(values) < CHRONOS2_MIN_POINTS or not future or not np.all(np.isfinite(values)):
            continue
        try:
            base = build_exog_for_line(
                db, line_item_id=li_id, train_periods=hist_periods, future_periods=future,
                version_id=version.id, max_links=N_DRIVER_LINKS,
            )
            shocked = build_exog_for_line(
                db, line_item_id=li_id, train_periods=hist_periods, future_periods=future,
                version_id=version.id, max_links=N_DRIVER_LINKS, overlays=overlays,
            )
        except Exception as e:  # a bad driver series drops the line, not the scenario
            logger.debug("what-if exog failed for line %s: %s", li_id, e)
            continue
        if base is None or shocked is None:
            continue
        n, h = len(values), len(future)
        past_b, fut_b = _finite_columns(base.exog_train, base.exog_future, n, h)
        past_s, fut_s = _finite_columns(shocked.exog_train, shocked.exog_future, n, h)
        # The shock must reach a known-future column Chronos-2 will read.
        if not fut_s or fut_s == fut_b:
            continue
        target = [float(v) for v in values]
        for tag, past, fut in (("base", past_b, fut_b), ("shock", past_s, fut_s)):
            series.append({
                "id": f"{li_id}@{tag}",
                "target": target,
                "prediction_length": h,
                "past_covariates": past,
                "future_covariates": fut,
                "group_id": f"whatif-{tag}",
            })
        future_by_line[li_id] = future

    if not series:
        return {}, "no driver-linked line has enough history and a shocked future covariate"
    try:
        result = forecast_batch(series)
    except FoundationServiceUnavailable as e:
        return {}, f"foundation service unavailable ({e})"

    deltas: dict[int, dict[str, float]] = {}
    for li_id, future in future_by_line.items():
        base_path = result.forecasts.get(f"{li_id}@base")
        shock_path = result.forecasts.get(f"{li_id}@shock")
        if base_path is None or shock_path is None:
            continue
        b = np.asarray(base_path.p50, dtype=float)
        s = np.asarray(shock_path.p50, dtype=float)
        k = min(len(b), len(s), len(future))
        deltas[li_id] = {future[i]: float(s[i] - b[i]) for i in range(k)}
    return deltas, None if deltas else "foundation service returned no paths"
