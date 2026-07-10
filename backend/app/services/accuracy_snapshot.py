"""Populate forecast_accuracy_records when actuals arrive (vintage tracking)."""

from __future__ import annotations

import logging
from typing import Iterable

from sqlalchemy.orm import Session

from app.models.actuals import ActualsRecord
from app.models.forecast import ForecastLineResult, ForecastVersion
from app.models.fx import ForecastAccuracyRecord
from app.services.period_calendar import horizon_offset

logger = logging.getLogger(__name__)


class AccuracySnapshotService:
    """Create/update accuracy records for all prior forecast versions covering a period."""

    def __init__(self, db: Session):
        self.db = db

    def on_actuals_ingested(
        self,
        dataset_id: str,
        line_item_ids: Iterable[int] | None = None,
    ) -> int:
        """For each actual in the dataset, match prior versions that forecasted that period."""
        q = self.db.query(ActualsRecord).filter(ActualsRecord.dataset_id == dataset_id)
        if line_item_ids is not None:
            q = q.filter(ActualsRecord.line_item_id.in_(list(line_item_ids)))
        actuals = q.all()
        created = 0
        for actual in actuals:
            created += self._record_for_actual(actual)
        self.db.flush()
        return created

    def _record_for_actual(self, actual: ActualsRecord) -> int:
        # Find forecast line results for this line+period across all versions
        results = (
            self.db.query(ForecastLineResult, ForecastVersion)
            .join(ForecastVersion, ForecastVersion.id == ForecastLineResult.version_id)
            .filter(
                ForecastLineResult.line_item_id == actual.line_item_id,
                ForecastLineResult.period == actual.period,
            )
            .all()
        )
        n = 0
        for flr, version in results:
            base = version.base_period
            if not base:
                continue
            try:
                h = horizon_offset(base, actual.period)
            except Exception:
                continue
            if h < 1:
                continue  # only forward-looking horizons

            existing = (
                self.db.query(ForecastAccuracyRecord)
                .filter(
                    ForecastAccuracyRecord.version_id == version.id,
                    ForecastAccuracyRecord.line_item_id == actual.line_item_id,
                    ForecastAccuracyRecord.period == actual.period,
                )
                .first()
            )
            abs_err = abs(flr.p50 - actual.value)
            pct = None
            if actual.value != 0:
                pct = abs_err / abs(actual.value) * 100.0
            within = None
            if flr.p10 is not None and flr.p90 is not None:
                within = flr.p10 <= actual.value <= flr.p90

            if existing:
                existing.actual = actual.value
                existing.predicted_p10 = flr.p10
                existing.predicted_p50 = flr.p50
                existing.predicted_p90 = flr.p90
                existing.absolute_error = abs_err
                existing.pct_error = pct
                existing.within_p10_p90 = within
                existing.horizon_offset = h
                existing.model_type = flr.model_type
            else:
                self.db.add(ForecastAccuracyRecord(
                    version_id=version.id,
                    line_item_id=actual.line_item_id,
                    period=actual.period,
                    horizon_offset=h,
                    predicted_p10=flr.p10,
                    predicted_p50=flr.p50,
                    predicted_p90=flr.p90,
                    actual=actual.value,
                    absolute_error=abs_err,
                    pct_error=pct,
                    within_p10_p90=within,
                    model_type=flr.model_type,
                ))
                n += 1
        return n
