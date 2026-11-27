"""Working-day (trading-day) effects for monthly and 4-4-5 periods.

Many FP&A lines (revenue booked per business day, hourly payroll, usage)
scale with the number of working days in a period: 20 in one month, 23 in
the next. Month-of-year seasonality already captures the average pattern;
what it cannot capture is the same month having a different count from one
year to the next (Easter moving between March and April, leap years, a month
with five Mondays). This module

* counts working days per period: Mon–Fri inside the period's dates, minus
  public holidays of the company's country (``holiday_country``, ISO code,
  via the ``holidays`` package already pinned for Prophet);
* tests, per line, whether the line moves with that count: regress the
  seasonal difference of log values on the seasonal difference of log
  working days, so the month-of-year pattern drops out and only the
  year-to-year shifts remain; and
* adjusts a line only when the effect is significant: models then see
  ``y * (mean_wd / wd) ** beta`` and the forecast is scaled back by the
  future periods' counts.

A 4-4-5 calendar's periods are whole weeks, so without holidays every period
has the same weekday count and no line is ever adjusted.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, timedelta
from functools import lru_cache

import numpy as np

from app.services.period_calendar import (
    FiscalCalendarConfig,
    add_periods,
    get_calendar_config,
    period_to_date,
)

logger = logging.getLogger(__name__)

# Admission: elasticity t-statistic, and a ceiling on how far a line is
# assumed to move per unit of working days (beyond proportional is
# implausible for a calendar effect and usually confounded).
MIN_T_STAT = 2.0
MAX_BETA = 1.25
MIN_POINTS = 25


@lru_cache(maxsize=64)
def _holiday_dates(country: str, year: int) -> frozenset[date]:
    try:
        import holidays

        return frozenset(holidays.country_holidays(country, years=year).keys())
    except Exception as e:  # unknown code, package missing: weekdays only
        logger.warning("holiday calendar unavailable for %s %s: %s", country, year, e)
        return frozenset()


def period_bounds(period: str, cal: FiscalCalendarConfig) -> tuple[date, date]:
    start = period_to_date(period, cal)
    end = period_to_date(add_periods(period, 1, cal), cal) - timedelta(days=1)
    return start, end


def working_days(
    periods: list[str], cal: FiscalCalendarConfig | None = None, country: str | None = None
) -> np.ndarray:
    """Mon–Fri days in each period, minus the country's public holidays."""
    cal = cal or get_calendar_config()
    out = np.zeros(len(periods), dtype=float)
    for i, p in enumerate(periods):
        start, end = period_bounds(str(p), cal)
        n = 0
        d = start
        while d <= end:
            if d.weekday() < 5 and not (country and d in _holiday_dates(country, d.year)):
                n += 1
            d += timedelta(days=1)
        out[i] = n
    return out


@dataclass(frozen=True)
class WorkingDayEffect:
    beta: float
    t_stat: float
    mean_wd: float

    def adjust(self, values: np.ndarray, wd: np.ndarray) -> np.ndarray:
        return np.asarray(values, dtype=float) * (self.mean_wd / wd) ** self.beta

    def restore_factor(self, wd: np.ndarray) -> np.ndarray:
        return (np.asarray(wd, dtype=float) / self.mean_wd) ** self.beta


def estimate_effect(
    values: np.ndarray, wd: np.ndarray, *, season: int = 12
) -> WorkingDayEffect | None:
    """The line's working-day elasticity, or None when it is not significant.

    OLS with intercept of d log y on d log wd at lag ``season``. Needs
    strictly positive values, ``MIN_POINTS`` observations and some variation
    in the differenced count. Admitted when t > ``MIN_T_STAT`` and beta > 0;
    beta is capped at ``MAX_BETA``.
    """
    y = np.asarray(values, dtype=float)
    w = np.asarray(wd, dtype=float)
    n = len(y)
    if n < max(MIN_POINTS, season + 8) or len(w) != n:
        return None
    if not (np.all(np.isfinite(y)) and np.all(y > 0) and np.all(w > 0)):
        return None
    dy = np.log(y[season:]) - np.log(y[:-season])
    dx = np.log(w[season:]) - np.log(w[:-season])
    if float(np.std(dx)) < 1e-9 or len(dy) < 8:
        return None
    X = np.column_stack([np.ones_like(dx), dx])
    beta, *_ = np.linalg.lstsq(X, dy, rcond=None)
    resid = dy - X @ beta
    dof = len(dy) - 2
    s2 = float(resid @ resid) / dof
    try:
        se = float(np.sqrt(s2 * np.linalg.inv(X.T @ X)[1, 1]))
    except np.linalg.LinAlgError:
        return None
    b = float(beta[1])
    if se <= 1e-12 or b <= 0:
        return None
    t = b / se
    if t < MIN_T_STAT:
        return None
    return WorkingDayEffect(beta=min(b, MAX_BETA), t_stat=t, mean_wd=float(np.mean(w)))
