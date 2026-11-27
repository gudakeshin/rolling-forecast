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
    overlay: pd.Series | None = None,
) -> pd.Series:
    """Compose period-indexed driver values: actual history first, then plan>scenario>forecast.

    Recorded actuals are history and always win for the periods they cover —
    a version's plan (budget) or scenario must never rewrite what actually
    happened, or the regression trains on the plan instead of the outcome.
    Version layers only fill periods with no actual, with plan taking
    precedence over scenario over forecast.

    ``overlay`` (if provided) wins for any periods it covers — used by fold CV to inject
    origin-restricted driver forecasts over holdout actuals without writing the DB.
    """
    idx = pd.Index(periods, dtype=str)
    out = pd.Series(index=idx, dtype=float)

    actual = materialize_driver_series(db, driver_id=driver_id, value_type="actual")
    if not actual.empty:
        out = out.combine_first(actual.astype(float)).reindex(idx)

    if version_id:
        for value_type in ("plan", "scenario", "forecast"):
            s = materialize_driver_series(
                db,
                driver_id=driver_id,
                value_type=value_type,
                version_id=version_id,
            )
            if not s.empty:
                out = out.combine_first(s.astype(float)).reindex(idx)

    if overlay is not None and not overlay.empty:
        out.update(overlay.astype(float))

    return out


def _lead_periods(first_period: str, n: int) -> list[str]:
    """The ``n`` fiscal periods immediately before ``first_period``, oldest first."""
    if n <= 0:
        return []
    from app.services.period_calendar import add_periods, get_calendar_config

    cfg = get_calendar_config()
    try:
        return [add_periods(first_period, -k, cfg) for k in range(n, 0, -1)]
    except Exception:
        return []


def build_exog_for_line(
    db: Session,
    *,
    line_item_id: int,
    train_periods: list[str],
    future_periods: list[str],
    version_id: str | None,
    max_links: int = 3,
    overlays: dict[int, pd.Series] | None = None,
) -> ExogBundle | None:
    """Build aligned train/future exog matrices from active driver links.

    ``overlays`` maps ``driver_id → series`` of origin-restricted forecasts used for
    Phase 8 CV ``exog_mode=forecast`` holdouts.
    """
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
    max_lag = max((max(int(link.lag), 0) for link, _driver in links), default=0)
    # Lagged regressors at the start of the window read genuine earlier
    # driver history instead of being left empty (and later backfilled).
    lead = _lead_periods(full_periods[0], max_lag) if full_periods else []
    window = lead + full_periods

    for link, driver in links:
        series = _materialize_with_precedence(
            db,
            driver_id=driver.id,
            periods=window,
            version_id=version_id,
            overlay=(overlays or {}).get(int(driver.id)),
        )
        if series.empty or series.isna().all():
            continue
        col = f"d{driver.id}_lag{int(link.lag)}"
        # driver(t-lag) predicts line(t). Where t-lag precedes the driver's
        # history, fall back to the latest value known at t itself — never a
        # value from after t.
        shifted = series.shift(int(link.lag)).ffill().combine_first(series.ffill())
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

    # Carry the last known value forward only. Backfilling would copy a later
    # observation into the start of the window — a look-ahead leak into every
    # CV fold whose origin precedes it. A driver whose history starts after
    # the window does not cover the training span and is dropped.
    exog_all = exog_all.ffill()
    train_block = exog_all.reindex(train_idx)
    uncovered = [c for c in exog_all.columns if train_block[c].isna().any()]
    if uncovered:
        exog_all = exog_all.drop(columns=uncovered)
        dropped = set(uncovered)
        specs = [sp for sp in specs if f"d{sp.driver_id}_lag{sp.lag}" not in dropped]
    if exog_all.empty:
        return None

    exog_train = exog_all.reindex(train_idx)
    exog_future = (
        exog_all.reindex(future_idx) if len(future_periods) else pd.DataFrame(index=future_idx)
    )
    if exog_train.empty or exog_train.isna().all().all():
        return None
    # Allow empty future when caller only needs the training design matrix (fold CV).
    if len(future_periods) and (exog_future.empty or exog_future.isna().all().all()):
        return None

    return ExogBundle(exog_train=exog_train, exog_future=exog_future, specs=specs)

