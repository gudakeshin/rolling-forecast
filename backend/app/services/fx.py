"""FX conversion at series-construction time."""

from __future__ import annotations

import hashlib
import logging
from typing import Iterable

from sqlalchemy.orm import Session

from app.models.fx import FxRate, SystemSetting

logger = logging.getLogger(__name__)


class MissingFxRateError(Exception):
    def __init__(self, pairs: list[tuple[str, str, str]]):
        self.pairs = pairs
        msg = ", ".join(f"{a}->{b}@{p}" for a, b, p in pairs)
        super().__init__(f"Missing FX rates for: {msg}")


def get_reporting_currency(db: Session, default: str = "USD") -> str:
    row = db.query(SystemSetting).filter(SystemSetting.key == "reporting_currency").first()
    return (row.value if row else default).upper()


def set_reporting_currency(db: Session, currency: str) -> None:
    currency = currency.upper()
    row = db.query(SystemSetting).filter(SystemSetting.key == "reporting_currency").first()
    if row:
        row.value = currency
    else:
        db.add(SystemSetting(key="reporting_currency", value=currency))
    db.flush()


def _lookup_rate(db: Session, frm: str, to: str, period: str) -> float | None:
    frm, to = frm.upper(), to.upper()
    if frm == to:
        return 1.0
    row = (
        db.query(FxRate)
        .filter(
            FxRate.from_currency == frm,
            FxRate.to_currency == to,
            FxRate.period == period,
        )
        .first()
    )
    if row:
        return float(row.rate)
    # Try inverse
    inv = (
        db.query(FxRate)
        .filter(
            FxRate.from_currency == to,
            FxRate.to_currency == frm,
            FxRate.period == period,
        )
        .first()
    )
    if inv and inv.rate:
        return 1.0 / float(inv.rate)
    return None


def convert_series_values(
    db: Session,
    values: list[float],
    currencies: list[str],
    periods: list[str],
    reporting_currency: str,
) -> tuple[list[float], str]:
    """Convert mixed-currency series to reporting currency.

    Returns (converted_values, fx_rate_set_hash).
    Raises MissingFxRateError listing missing pairs — never silently uses 1.0.
    """
    reporting_currency = reporting_currency.upper()
    missing: list[tuple[str, str, str]] = []
    used: list[str] = []
    out: list[float] = []

    for val, ccy, period in zip(values, currencies, periods):
        ccy = (ccy or reporting_currency).upper()
        if ccy == reporting_currency:
            out.append(float(val))
            continue
        rate = _lookup_rate(db, ccy, reporting_currency, period)
        if rate is None:
            missing.append((ccy, reporting_currency, period))
            out.append(val)
        else:
            out.append(val * rate)
            used.append(f"{ccy}:{reporting_currency}:{period}:{rate}")

    if missing:
        # Dedupe
        uniq = list(dict.fromkeys(missing))
        raise MissingFxRateError(uniq)

    digest = hashlib.sha256("|".join(sorted(used)).encode()).hexdigest() if used else "identity"
    return out, digest


def fx_rate_set_hash_for_pairs(
    db: Session, pairs: Iterable[tuple[str, str, str]]
) -> str:
    parts = []
    for frm, to, period in pairs:
        rate = _lookup_rate(db, frm, to, period)
        parts.append(f"{frm}:{to}:{period}:{rate}")
    return hashlib.sha256("|".join(sorted(parts)).encode()).hexdigest()
