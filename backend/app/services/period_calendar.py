"""Fiscal / calendar period helpers — single seam for period↔date conversions.

Supports:
  - gregorian_month (default): YYYY-MM labels, calendar months
  - fiscal_445: NRF-style 4-4-5 retail calendar with labels FY{yyyy}-P{nn}
"""

from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass
from datetime import date, timedelta
from enum import Enum

from dateutil.relativedelta import relativedelta
from sqlalchemy.orm import Session


class CalendarType(str, Enum):
    GREGORIAN_MONTH = "gregorian_month"
    FISCAL_445 = "fiscal_445"


@dataclass(frozen=True)
class FiscalCalendarConfig:
    calendar_type: CalendarType = CalendarType.GREGORIAN_MONTH
    # Month (1-12) whose first day starts the fiscal year for 4-4-5
    # (NRF often uses early February; default February = 2)
    fiscal_year_start_month: int = 2
    # Weekday the fiscal year starts on (0=Mon … 6=Sun). NRF uses Sunday=6.
    week_start: int = 6


_DEFAULT = FiscalCalendarConfig()
# Request/skill-scoped override so engines (no DB) use the tenant calendar
_active_calendar: ContextVar[FiscalCalendarConfig | None] = ContextVar(
    "rf_fiscal_calendar", default=None
)


def push_calendar(config: FiscalCalendarConfig) -> Token:
    """Bind calendar for the current async/task context (engines read via get_calendar_config)."""
    return _active_calendar.set(config)


def reset_calendar(token: Token) -> None:
    _active_calendar.reset(token)


def get_calendar_config(db: Session | None = None) -> FiscalCalendarConfig:
    """Load calendar: context override → system_settings → gregorian default."""
    ctx = _active_calendar.get()
    if ctx is not None:
        return ctx
    if db is None:
        return _DEFAULT
    try:
        from app.models.fx import SystemSetting

        row = db.query(SystemSetting).filter(SystemSetting.key == "fiscal_calendar").first()
        if not row or not row.value:
            return _DEFAULT
        import json

        raw = json.loads(row.value) if row.value.startswith("{") else {"calendar_type": row.value}
        ctype = CalendarType(raw.get("calendar_type", CalendarType.GREGORIAN_MONTH.value))
        return FiscalCalendarConfig(
            calendar_type=ctype,
            fiscal_year_start_month=int(raw.get("fiscal_year_start_month", 2)),
            week_start=int(raw.get("week_start", 6)),
        )
    except Exception:
        return _DEFAULT


def set_calendar_config(db: Session, config: FiscalCalendarConfig) -> None:
    import json

    from app.models.fx import SystemSetting

    payload = json.dumps(
        {
            "calendar_type": config.calendar_type.value,
            "fiscal_year_start_month": config.fiscal_year_start_month,
            "week_start": config.week_start,
        }
    )
    row = db.query(SystemSetting).filter(SystemSetting.key == "fiscal_calendar").first()
    if row:
        row.value = payload
    else:
        db.add(SystemSetting(key="fiscal_calendar", value=payload))
    db.flush()


def _nth_weekday_on_or_after(d: date, weekday: int) -> date:
    """Return the first date on/after d whose weekday matches (0=Mon…6=Sun)."""
    delta = (weekday - d.weekday()) % 7
    return d + timedelta(days=delta)


def fiscal_year_start(fy: int, config: FiscalCalendarConfig = _DEFAULT) -> date:
    """First day of fiscal year `fy` under the active calendar."""
    if config.calendar_type == CalendarType.GREGORIAN_MONTH:
        return date(fy, 1, 1)
    # 4-4-5: first `week_start` on/after the 1st of fiscal_year_start_month
    anchor = date(fy, config.fiscal_year_start_month, 1)
    return _nth_weekday_on_or_after(anchor, config.week_start)


def periods_in_year(config: FiscalCalendarConfig = _DEFAULT) -> int:
    return 12 if config.calendar_type == CalendarType.GREGORIAN_MONTH else 12


def _445_period_starts(fy: int, config: FiscalCalendarConfig) -> list[date]:
    """Return 12 period-start dates for fiscal year fy (4-4-5 weeks).

    Quarters are 4+4+5 weeks. A 53rd week (when present) is absorbed into
    the last period of Q4 (making it 6 weeks) — common retail practice.
    """
    start = fiscal_year_start(fy, config)
    next_start = fiscal_year_start(fy + 1, config)
    total_weeks = (next_start - start).days // 7
    # Pattern of weeks per period within each quarter
    q_pattern = (4, 4, 5)
    weeks_per_period: list[int] = []
    for _ in range(4):
        weeks_per_period.extend(q_pattern)
    # Absorb extra week into last period
    extra = total_weeks - 52
    if extra > 0:
        weeks_per_period[-1] += extra

    starts = [start]
    cursor = start
    for w in weeks_per_period[:-1]:
        cursor = cursor + timedelta(weeks=w)
        starts.append(cursor)
    return starts


def period_to_date(period: str, config: FiscalCalendarConfig = _DEFAULT) -> date:
    """Convert a period label to the first day of that period."""
    if config.calendar_type == CalendarType.FISCAL_445 or period.startswith("FY"):
        # FY2026-P01 … FY2026-P12
        body = period[2:] if period.startswith("FY") else period
        if "-P" in body.upper():
            fy_s, p_s = body.upper().replace("P", "").split("-")
            fy, p = int(fy_s), int(p_s)
            starts = _445_period_starts(fy, config if config.calendar_type == CalendarType.FISCAL_445 else FiscalCalendarConfig(CalendarType.FISCAL_445))
            if not 1 <= p <= len(starts):
                raise ValueError(f"Invalid 4-4-5 period: {period}")
            return starts[p - 1]
        # fall through if malformed
    year, month = period.split("-")
    return date(int(year), int(month), 1)


def date_to_period(d: date, config: FiscalCalendarConfig = _DEFAULT) -> str:
    """Convert a date to a period label under the active calendar."""
    if config.calendar_type == CalendarType.GREGORIAN_MONTH:
        return f"{d.year}-{d.month:02d}"

    # Find fiscal year: the FY whose start is on/before d
    fy = d.year if d >= fiscal_year_start(d.year, config) else d.year - 1
    # Handle edge: date before FY start of calendar year might belong to prior FY
    if d < fiscal_year_start(fy, config):
        fy -= 1
    starts = _445_period_starts(fy, config)
    next_start = fiscal_year_start(fy + 1, config)
    for i, s in enumerate(starts):
        end = starts[i + 1] if i + 1 < len(starts) else next_start
        if s <= d < end:
            return f"FY{fy}-P{i + 1:02d}"
    return f"FY{fy}-P12"


def add_periods(period: str, n: int, config: FiscalCalendarConfig = _DEFAULT) -> str:
    """Add n periods to a period label."""
    if config.calendar_type == CalendarType.GREGORIAN_MONTH and not period.startswith("FY"):
        return date_to_period(period_to_date(period, config) + relativedelta(months=n), config)

    # 4-4-5 arithmetic via period index
    d0 = period_to_date(period, config)
    # Walk n periods by converting through date_to_period after jumping weeks
    # More reliably: parse FY/P and add modulo 12
    label = date_to_period(d0, config) if not period.startswith("FY") else period
    body = label[2:]
    fy_s, p_s = body.upper().replace("P", "").split("-")
    fy, p = int(fy_s), int(p_s)
    idx = (fy * 12 + (p - 1)) + n
    new_fy, new_p0 = divmod(idx, 12)
    return f"FY{new_fy}-P{new_p0 + 1:02d}"


def period_range(start: str, end: str, config: FiscalCalendarConfig = _DEFAULT) -> list[str]:
    """Inclusive list of periods from start to end."""
    out: list[str] = []
    cur = start
    # Guard against infinite loops
    for _ in range(500):
        out.append(cur)
        if cur == end:
            break
        nxt = add_periods(cur, 1, config)
        # Detect overshoot
        try:
            if period_to_date(nxt, config) > period_to_date(end, config):
                break
        except Exception:
            break
        cur = nxt
    return out


def horizon_offset(base_period: str, forecast_period: str, config: FiscalCalendarConfig = _DEFAULT) -> int:
    """Periods between base and forecast (1 = next period)."""
    if config.calendar_type == CalendarType.GREGORIAN_MONTH and not base_period.startswith("FY"):
        b = period_to_date(base_period, config)
        f = period_to_date(forecast_period, config)
        return (f.year - b.year) * 12 + (f.month - b.month)

    # Count steps
    if base_period == forecast_period:
        return 0
    n = 0
    cur = base_period
    for _ in range(500):
        cur = add_periods(cur, 1, config)
        n += 1
        if cur == forecast_period:
            return n
        try:
            if period_to_date(cur, config) > period_to_date(forecast_period, config):
                return n
        except Exception:
            return n
    return n


def forecast_horizon_periods(last_period: str, horizon: int, config: FiscalCalendarConfig = _DEFAULT) -> list[str]:
    """Return `horizon` period labels after last_period."""
    return [add_periods(last_period, i, config) for i in range(1, horizon + 1)]
