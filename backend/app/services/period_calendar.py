"""Fiscal / calendar period helpers — single seam for YYYY-MM assumptions.

Full 4-4-5 support is deferred; all period↔date conversions should funnel here.
"""

from __future__ import annotations

from datetime import date
from dateutil.relativedelta import relativedelta


def period_to_date(period: str) -> date:
    """Convert YYYY-MM to the first day of that month."""
    year, month = period.split("-")
    return date(int(year), int(month), 1)


def date_to_period(d: date) -> str:
    """Convert a date to YYYY-MM period label."""
    return f"{d.year}-{d.month:02d}"


def add_periods(period: str, n: int) -> str:
    """Add n months to a YYYY-MM period label."""
    return date_to_period(period_to_date(period) + relativedelta(months=n))


def period_range(start: str, end: str) -> list[str]:
    """Inclusive list of YYYY-MM periods from start to end."""
    cur = period_to_date(start)
    last = period_to_date(end)
    out: list[str] = []
    while cur <= last:
        out.append(date_to_period(cur))
        cur += relativedelta(months=1)
    return out


def horizon_offset(base_period: str, forecast_period: str) -> int:
    """Months between base_period and forecast_period (1 = next month)."""
    b = period_to_date(base_period)
    f = period_to_date(forecast_period)
    return (f.year - b.year) * 12 + (f.month - b.month)
