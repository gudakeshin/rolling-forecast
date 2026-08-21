"""
Centralized error handling for all 12 PRD Section 10 edge cases.

Edge Cases Covered:
1. Line item has < 12 months of history
2. Line item has all zeros in history
3. Actuals data pull fails or times out
4. Actuals are missing for most recent period
5. Circular dependency detected in P&L graph
6. Override creates impossible value
7. BU head submits driver input after hard deadline
8. Model selection produces ties
9. Statistical model generates negative values for non-negative line
10. Concurrent users overriding the same line item
11. Forecast generation exceeds 30-minute timeout
12. Structural break detected in historical data
"""

import logging
import asyncio
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Coroutine

import numpy as np
import pandas as pd
from sqlalchemy.orm import Session

from app.config import settings

logger = logging.getLogger(__name__)


# ============================================================
# Edge Case 1 & 2: Sparse / Zero History
# ============================================================

class HistoryAnalysis:
    """Analyze historical data for a line item and determine appropriate model strategy."""

    def __init__(self, values: pd.Series, dates: pd.DatetimeIndex, line_item_name: str = ""):
        self.values = values
        self.dates = dates
        self.line_item_name = line_item_name
        self.n_points = len(values)
        self.warnings: list[str] = []
        self.flags: list[str] = []
        # Memoised (detected, period, index) from the structural-break scan.
        self._break_cache: tuple[bool, str | None, int | None] | None = None

    @property
    def is_all_zeros(self) -> bool:
        """Edge Case 2: All zeros."""
        return bool(np.all(self.values == 0))

    @property
    def is_sparse(self) -> bool:
        """Edge Case 1: Less than min_history_months of data."""
        return self.n_points < settings.min_history_months

    @property
    def is_very_sparse(self) -> bool:
        """Fewer than 6 months -- cannot run any statistical model."""
        return self.n_points < 6

    def _detect_structural_break(self) -> tuple[bool, str | None, int | None]:
        """CUSUM on OLS residuals (statsmodels) with Chow fallback.

        Returns (detected, period_label, break_index). Truncation eligibility
        (≥12 post-break points) is decided by the caller.
        """
        if self.n_points < 12:
            return False, None, None
        if self._break_cache is not None:
            return self._break_cache

        y = self.values.values.astype(float)
        if float(np.std(y)) < 1e-12:
            self._break_cache = (False, None, None)
            return self._break_cache

        t = np.arange(len(y), dtype=float)
        break_idx: int | None = None
        detected = False

        try:
            import statsmodels.api as sm
            from statsmodels.stats.diagnostic import breaks_cusumolsresid

            X = sm.add_constant(t)
            ols = sm.OLS(y, X).fit()
            resid = np.asarray(ols.resid, dtype=float)
            if float(np.std(resid)) < 1e-12:
                self._break_cache = (False, None, None)
                return self._break_cache
            _stat, pval, _crit = breaks_cusumolsresid(resid, ddof=X.shape[1])
            if pval is not None and float(pval) < 0.05:
                detected = True
                # Locate break at max |scaled CUSUM of demeaned residuals|
                cumsum = np.cumsum(resid - np.mean(resid))
                break_idx = int(np.argmax(np.abs(cumsum)))
        except Exception:
            detected = False

        # Chow fallback at mid-sample candidates when CUSUM unavailable/insignificant
        if not detected and self.n_points >= 24:
            try:
                import statsmodels.api as sm

                best_p = 1.0
                best_i = None
                # Candidate split points with ≥6 obs each side
                for i in range(6, self.n_points - 6):
                    y1, y2 = y[:i], y[i:]
                    t1 = sm.add_constant(np.arange(len(y1), dtype=float))
                    t2 = sm.add_constant(np.arange(len(y2), dtype=float))
                    rss1 = float(sm.OLS(y1, t1).fit().ssr)
                    rss2 = float(sm.OLS(y2, t2).fit().ssr)
                    rss_p = float(sm.OLS(y, sm.add_constant(t)).fit().ssr)
                    k = 2
                    n = len(y)
                    num = (rss_p - rss1 - rss2) / k
                    den = (rss1 + rss2) / (n - 2 * k)
                    if den <= 0:
                        continue
                    from scipy import stats as scipy_stats

                    f_stat = num / den
                    p = float(scipy_stats.f.sf(f_stat, k, n - 2 * k))
                    if p < best_p:
                        best_p = p
                        best_i = i
                if best_p < 0.05 and best_i is not None:
                    detected = True
                    break_idx = best_i
            except Exception:
                pass

        period = None
        if detected and break_idx is not None and break_idx < len(self.dates):
            period = self.dates[break_idx].strftime("%Y-%m")
        self._break_cache = (detected, period, break_idx)
        return self._break_cache

    @property
    def has_structural_break(self) -> bool:
        """Edge Case 12: CUSUM / Chow structural break detection."""
        detected, _, _ = self._detect_structural_break()
        return detected

    @property
    def structural_break_period(self) -> str | None:
        """Find approximate period of the structural break."""
        _, period, _ = self._detect_structural_break()
        return period

    @property
    def structural_break_index(self) -> int | None:
        """0-based index of the detected break (or None)."""
        _, _, idx = self._detect_structural_break()
        return idx

    @property
    def recommended_model(self) -> str:
        """Recommend model based on data quality."""
        if self.is_all_zeros:
            return "zero"
        if self.is_very_sparse:
            return "average"
        if self.is_sparse:
            return "linear"
        return "auto"

    def analyze(self) -> dict[str, Any]:
        """Run full analysis and return recommendations."""
        result: dict[str, Any] = {
            "n_points": self.n_points,
            "recommended_model": self.recommended_model,
            "warnings": [],
            "flags": [],
            "confidence_override": None,
        }

        # Edge Case 2: All zeros
        if self.is_all_zeros:
            result["warnings"].append(
                f"No historical activity detected for '{self.line_item_name}' — "
                "manual input required if non-zero expected."
            )
            result["flags"].append("all_zeros")
            result["confidence_override"] = "low"

        # Edge Case 1: Sparse history
        elif self.is_sparse:
            result["warnings"].append(
                f"Only {self.n_points} months of history for '{self.line_item_name}' "
                f"(minimum recommended: {settings.min_history_months}). "
                "Using simpler model. Confidence set to Low."
            )
            result["flags"].append("sparse_data")
            result["confidence_override"] = "low"

        # Edge Case 12: Structural break
        if self.has_structural_break:
            break_period = self.structural_break_period
            result["warnings"].append(
                f"Structural break detected in {break_period or 'unknown period'} "
                f"for '{self.line_item_name}'. Using post-break data for forecast. "
                "Override if incorrect."
            )
            result["flags"].append("structural_break")
            result["break_period"] = break_period

        # Edge Case 4: Check recency
        if len(self.dates) > 0:
            last_date = self.dates[-1]
            now = pd.Timestamp.now()
            months_stale = (now.year - last_date.year) * 12 + (now.month - last_date.month)
            if months_stale > 2:
                result["warnings"].append(
                    f"Warning: most recent actuals are from {last_date.strftime('%Y-%m')}. "
                    "Forecast baseline may be stale."
                )
                result["flags"].append("stale_data")

        return result


# ============================================================
# Edge Case 3: Actuals Data Pull Retry
# ============================================================

async def retry_with_backoff(
    func: Callable[..., Coroutine],
    *args,
    max_retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    **kwargs,
) -> Any:
    """
    Retry an async function with exponential backoff.
    Edge Case 3: Data pull retries.
    """
    last_error = None
    for attempt in range(max_retries + 1):
        try:
            return await func(*args, **kwargs)
        except Exception as e:
            last_error = e
            if attempt < max_retries:
                delay = min(base_delay * (2 ** attempt), max_delay)
                logger.warning(
                    f"Attempt {attempt + 1}/{max_retries + 1} failed: {e}. "
                    f"Retrying in {delay:.1f}s..."
                )
                await asyncio.sleep(delay)
            else:
                logger.error(
                    f"All {max_retries + 1} attempts failed. Last error: {e}"
                )
    raise last_error  # type: ignore


# ============================================================
# Edge Case 6: Override Validation (impossible values)
# ============================================================

class OverrideValidator:
    """Validate override values against expected ranges."""

    @staticmethod
    def validate(
        line_item_name: str,
        category: str,
        allow_negative: bool,
        original_value: float,
        override_value: float,
    ) -> list[dict[str, str]]:
        """
        Returns list of warnings (soft validations).
        These are shown to the user but do not block the override.
        """
        warnings = []

        # Negative revenue
        if not allow_negative and override_value < 0:
            warnings.append({
                "level": "warning",
                "message": (
                    f"Override value ${override_value:,.0f} is negative for '{line_item_name}' "
                    f"(category: {category}) which normally doesn't allow negative values. "
                    "Are you sure?"
                ),
            })

        # Extreme deviation (> 300% change)
        if original_value != 0:
            pct_change = abs((override_value - original_value) / original_value) * 100
            if pct_change > 300:
                warnings.append({
                    "level": "warning",
                    "message": (
                        f"Override represents a {pct_change:.0f}% change from the model value "
                        f"(${original_value:,.0f} → ${override_value:,.0f}). "
                        "This is outside the expected range."
                    ),
                })

        # Zero override on non-zero line
        if override_value == 0 and original_value != 0 and abs(original_value) > 1000:
            warnings.append({
                "level": "info",
                "message": (
                    f"Setting '{line_item_name}' to zero will eliminate "
                    f"${original_value:,.0f} from the forecast."
                ),
            })

        return warnings


# ============================================================
# Edge Case 7: Late Driver Input Deadline Check
# ============================================================

def check_driver_deadline(
    soft_deadline_days: int,
    hard_deadline_days: int,
    cycle_start: datetime | None = None,
) -> dict[str, Any]:
    """
    Check if a driver input submission is late.
    Returns deadline status info.
    """
    now = datetime.now(timezone.utc)
    if cycle_start is None:
        # Assume first of current month as cycle start
        cycle_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    days_elapsed = (now - cycle_start).days

    result: dict[str, Any] = {
        "is_late": False,
        "is_past_soft": False,
        "is_past_hard": False,
        "days_remaining_soft": max(0, soft_deadline_days - days_elapsed),
        "days_remaining_hard": max(0, hard_deadline_days - days_elapsed),
        "message": "",
    }

    if days_elapsed > hard_deadline_days:
        result["is_late"] = True
        result["is_past_hard"] = True
        result["message"] = (
            f"Submission is {days_elapsed - hard_deadline_days} day(s) past the hard deadline. "
            "Values will be included in the next forecast refresh, not the current cycle."
        )
    elif days_elapsed > soft_deadline_days:
        result["is_past_soft"] = True
        result["message"] = (
            f"Submission is past the soft deadline ({soft_deadline_days} days). "
            f"Hard deadline in {result['days_remaining_hard']} day(s)."
        )

    return result


# ============================================================
# Edge Case 9: Negative Value Clamping
# ============================================================

def clamp_forecast_values(
    values: np.ndarray,
    allow_negative: bool,
    line_item_name: str = "",
) -> tuple[np.ndarray, list[str]]:
    """
    Clamp forecast values for non-negative lines.
    Returns (clamped_values, warnings).
    """
    warnings = []
    if not allow_negative:
        negative_mask = values < 0
        if np.any(negative_mask):
            n_clamped = int(np.sum(negative_mask))
            min_val = float(np.min(values[negative_mask]))
            warnings.append(
                f"Model produced negative forecast for '{line_item_name}' "
                f"({n_clamped} period(s), min=${min_val:,.0f}). "
                "Clamped to $0 — review recommended."
            )
            values = np.maximum(values, 0)
    return values, warnings


# ============================================================
# Edge Case 10: Concurrent Override Detection
# ============================================================

def check_concurrent_override(
    db: Session,
    version_id: str,
    line_item_id: int,
    period: str,
    current_user_id: str,
) -> dict[str, Any] | None:
    """
    Check if another user has recently overridden the same line item.
    Returns info about the concurrent override, or None.
    """
    from app.models.override import Override

    # Check for recent overrides by other users (within last hour).
    # Use timedelta — datetime.replace(hour=hour-1) crashes at 00:00–00:59 UTC.
    recent_cutoff = datetime.now(timezone.utc) - timedelta(hours=1)

    recent = (
        db.query(Override)
        .filter(
            Override.version_id == version_id,
            Override.line_item_id == line_item_id,
            Override.period == period,
            Override.user_id != current_user_id,
            Override.status == "active",
            Override.created_at >= recent_cutoff,
        )
        .order_by(Override.created_at.desc())
        .first()
    )

    if recent:
        from app.models.user import User
        other_user = db.query(User).filter(User.id == recent.user_id).first()
        return {
            "other_user": other_user.full_name if other_user else "Unknown",
            "override_value": recent.override_value,
            "created_at": recent.created_at.isoformat() if recent.created_at else "Unknown",
            "message": (
                f"{other_user.full_name if other_user else 'Another user'} also modified "
                f"this line item at {recent.created_at.strftime('%H:%M') if recent.created_at else 'unknown time'}. "
                "Last-write-wins — full audit trail preserved."
            ),
        }
    return None


# ============================================================
# Edge Case 11: Forecast Generation Timeout
# ============================================================

class ForecastTimeoutError(Exception):
    """Raised when forecast generation exceeds the configured timeout."""
    pass


def check_generation_timeout(start_time: float, context: str = "") -> None:
    """
    Check if forecast generation has exceeded the timeout.
    Should be called periodically during generation.
    """
    elapsed_minutes = (time.time() - start_time) / 60
    max_minutes = settings.max_forecast_generation_minutes

    if elapsed_minutes > max_minutes:
        raise ForecastTimeoutError(
            f"Forecast generation timed out after {elapsed_minutes:.1f} minutes "
            f"(limit: {max_minutes} minutes). {context}"
            "Consider reducing line item count or simplifying model configuration."
        )

    # Warn at 80% of timeout
    if elapsed_minutes > max_minutes * 0.8:
        logger.warning(
            f"Forecast generation at {elapsed_minutes:.1f}/{max_minutes} minutes ({context})"
        )
