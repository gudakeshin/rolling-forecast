"""Phase 8 helpers for driver-based exogenous regressors."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd
from sqlalchemy.orm import Session

from app.models.driver import Driver, DriverLink
from app.services.driver_series import materialize_driver_series


@dataclass
class ExogLinkSpec:
    driver_id: int
    driver_key: str
    relation: str
    lag: int
    coefficient: float | None


@dataclass
class ExogBundle:
    exog_train: pd.DataFrame
    exog_future: pd.DataFrame
    specs: list[ExogLinkSpec]


def _materialize_with_precedence(
    db: Session,
    *,
    driver_id: int,
    periods: list[str],
    version_id: str | None,
) -> pd.Series:
    """Compose period-indexed driver values with plan>scenario>forecast>actual precedence."""
    idx = pd.Index(periods, dtype=str)
    out = pd.Series(index=idx, dtype=float)

    # Base layer: historical actuals (version-less)
    actual = materialize_driver_series(db, driver_id=driver_id, value_type="actual")
    if not actual.empty:
        out.update(actual.astype(float))

    # Version-scoped overlays (highest precedence first)
    if version_id:
        for value_type in ("plan", "scenario", "forecast"):
            s = materialize_driver_series(
                db,
                driver_id=driver_id,
                value_type=value_type,
                version_id=version_id,
            )
            if not s.empty:
                out.update(s.astype(float))

    return out


def build_exog_for_line(
    db: Session,
    *,
    line_item_id: int,
    train_periods: list[str],
    future_periods: list[str],
    version_id: str | None,
    max_links: int = 3,
) -> ExogBundle | None:
    """Build aligned train/future exog matrices from active driver links."""
    links = (
        db.query(DriverLink, Driver)
        .join(Driver, Driver.id == DriverLink.driver_id)
        .filter(
            DriverLink.line_item_id == line_item_id,
            DriverLink.status == "active",
        )
        .order_by(DriverLink.id.asc())
        .limit(max_links)
        .all()
    )
    if not links:
        return None

    full_periods = list(train_periods) + list(future_periods)
    train_idx = pd.Index(train_periods, dtype=str)
    future_idx = pd.Index(future_periods, dtype=str)
    exog_all = pd.DataFrame(index=pd.Index(full_periods, dtype=str))
    specs: list[ExogLinkSpec] = []

    for link, driver in links:
        series = _materialize_with_precedence(
            db,
            driver_id=driver.id,
            periods=full_periods,
            version_id=version_id,
        )
        if series.empty:
            continue
        col = f"d{driver.id}_lag{int(link.lag)}"
        # driver(t-lag) predicts line(t)
        shifted = series.shift(int(link.lag))
        exog_all[col] = shifted.reindex(exog_all.index)
        specs.append(
            ExogLinkSpec(
                driver_id=driver.id,
                driver_key=driver.key,
                relation=link.relation,
                lag=int(link.lag),
                coefficient=link.coefficient,
            )
        )

    if exog_all.empty:
        return None

    # Conservative fill for sparse series; keep fully-missing columns out.
    exog_all = exog_all.ffill().bfill()
    exog_all = exog_all.dropna(axis=1, how="all")
    if exog_all.empty:
        return None

    exog_train = exog_all.reindex(train_idx)
    exog_future = exog_all.reindex(future_idx)
    if exog_train.empty or exog_future.empty:
        return None

    return ExogBundle(exog_train=exog_train, exog_future=exog_future, specs=specs)

