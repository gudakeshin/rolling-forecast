"""Populate forecast_accuracy_records when actuals arrive (vintage tracking)."""

from __future__ import annotations

import logging
import uuid
from typing import Iterable

from sqlalchemy.orm import Session

from app.models.actuals import ActualsRecord
from app.models.forecast import ForecastLineResult, ForecastVersion
from app.models.fx import ForecastAccuracyRecord
from app.services.period_calendar import add_periods, horizon_offset
from app.services.upsert import bulk_upsert

logger = logging.getLogger(__name__)


def _wape(predictions: list[float], actuals: list[float]) -> float | None:
    """Weighted Absolute Percentage Error: Σ|e| / Σ|a| × 100."""
    if not actuals:
        return None
    denom = sum(abs(a) for a in actuals)
    if denom <= 0:
        return None
    numer = sum(abs(p - a) for p, a in zip(predictions, actuals))
    return numer / denom * 100.0


def compute_fva(
    published: list[float],
    actuals: list[float],
    benchmark: list[float],
) -> float | None:
    """Forecast Value Add = benchmark WAPE − published WAPE (positive = better)."""
    pub = _wape(published, actuals)
    ben = _wape(benchmark, actuals)
    if pub is None or ben is None:
        return None
    return ben - pub


class AccuracySnapshotService:
    """Create/update accuracy records for all prior forecast versions covering a period."""

    def __init__(self, db: Session):
        self.db = db

    def on_actuals_ingested(
        self,
        dataset_id: str,
        line_item_ids: Iterable[int] | None = None,
    ) -> int:
        """Match actuals to prior forecasts via a single join (no per-row N+1)."""
        q = self.db.query(ActualsRecord).filter(ActualsRecord.dataset_id == dataset_id)
        if line_item_ids is not None:
            q = q.filter(ActualsRecord.line_item_id.in_(list(line_item_ids)))
        actuals = q.all()
        if not actuals:
            return 0

        actual_by_key: dict[tuple[int, str], ActualsRecord] = {
            (a.line_item_id, a.period): a for a in actuals
        }
        li_ids = list({a.line_item_id for a in actuals})
        periods = list({a.period for a in actuals})

        # Broader actuals history for naive / seasonal-naive benchmarks
        history = (
            self.db.query(ActualsRecord)
            .filter(ActualsRecord.line_item_id.in_(li_ids))
            .all()
        )
        hist_by_key = {(a.line_item_id, a.period): a.value for a in history}

        pairs = (
            self.db.query(ForecastLineResult, ForecastVersion)
            .join(ForecastVersion, ForecastVersion.id == ForecastLineResult.version_id)
            .filter(
                ForecastLineResult.line_item_id.in_(li_ids),
                ForecastLineResult.period.in_(periods),
            )
            .all()
        )

        existing_rows = (
            self.db.query(ForecastAccuracyRecord)
            .filter(
                ForecastAccuracyRecord.line_item_id.in_(li_ids),
                ForecastAccuracyRecord.period.in_(periods),
            )
            .all()
        )
        existing_by_key = {
            (r.version_id, r.line_item_id, r.period): r for r in existing_rows
        }

        to_insert: list[dict] = []
        created = 0
        for flr, version in pairs:
            actual = actual_by_key.get((flr.line_item_id, flr.period))
            if actual is None:
                continue
            base = version.base_period
            if not base:
                continue
            try:
                h = horizon_offset(base, actual.period)
            except Exception:
                continue
            if h < 1:
                continue

            published = float(flr.p50)
            model_p50 = float(flr.model_p50) if flr.model_p50 is not None else published
            abs_err = abs(published - actual.value)
            pct = None
            if actual.value != 0:
                pct = abs_err / abs(actual.value) * 100.0
            within = None
            if flr.p10 is not None and flr.p90 is not None:
                within = flr.p10 <= actual.value <= flr.p90

            naive_p50 = None
            seasonal_naive_p50 = None
            try:
                prior = add_periods(actual.period, -1)
                naive_p50 = hist_by_key.get((flr.line_item_id, prior))
            except Exception:
                pass
            try:
                lag12 = add_periods(actual.period, -12)
                seasonal_naive_p50 = hist_by_key.get((flr.line_item_id, lag12))
            except Exception:
                pass

            key = (version.id, actual.line_item_id, actual.period)
            existing = existing_by_key.get(key)
            if existing:
                existing.actual = actual.value
                existing.predicted_p10 = flr.p10
                existing.predicted_p50 = published
                existing.predicted_p90 = flr.p90
                existing.model_p50 = model_p50
                existing.naive_p50 = naive_p50
                existing.seasonal_naive_p50 = seasonal_naive_p50
                existing.absolute_error = abs_err
                existing.pct_error = pct
                existing.within_p10_p90 = within
                existing.horizon_offset = h
                existing.model_type = flr.model_type
            else:
                to_insert.append(
                    {
                        "id": str(uuid.uuid4()),
                        "version_id": version.id,
                        "line_item_id": actual.line_item_id,
                        "period": actual.period,
                        "horizon_offset": h,
                        "predicted_p10": flr.p10,
                        "predicted_p50": published,
                        "predicted_p90": flr.p90,
                        "model_p50": model_p50,
                        "naive_p50": naive_p50,
                        "seasonal_naive_p50": seasonal_naive_p50,
                        "actual": actual.value,
                        "absolute_error": abs_err,
                        "pct_error": pct,
                        "within_p10_p90": within,
                        "model_type": flr.model_type,
                    }
                )
                created += 1

        if to_insert:
            bulk_upsert(
                self.db,
                ForecastAccuracyRecord,
                to_insert,
                conflict_cols=("version_id", "line_item_id", "period"),
                update_cols=(
                    "horizon_offset",
                    "predicted_p10",
                    "predicted_p50",
                    "predicted_p90",
                    "model_p50",
                    "naive_p50",
                    "seasonal_naive_p50",
                    "actual",
                    "absolute_error",
                    "pct_error",
                    "within_p10_p90",
                    "model_type",
                ),
            )

        self.db.flush()
        return created
