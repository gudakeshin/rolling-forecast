"""Shared forecast confidence scoring — single source of truth.

Previously duplicated (and drifted) between generate_baseline and score_confidence.
"""

from __future__ import annotations

from typing import Protocol


# Model type base confidence scores (simpler models = lower base)
MODEL_BASE_SCORES: dict[str, float] = {
    "prophet": 65,
    "arima": 60,
    "theta": 58,
    "ets": 55,
    "tsb": 45,
    "croston": 45,
    "linear": 40,
    "seasonal_naive": 35,
    "naive": 30,
    "average": 25,
    "zero": 0,
}


class _ConfidenceInputs(Protocol):
    """Minimal shape shared by ForecastLineResult (and test doubles).

    Declared read-only (properties rather than plain attributes) so a supplier
    with narrower types — ForecastLineResult.p50 is non-nullable — still
    satisfies the protocol; mutable protocol attributes are invariant.
    """

    @property
    def model_mape(self) -> float | None: ...

    @property
    def p10(self) -> float | None: ...

    @property
    def p50(self) -> float | None: ...

    @property
    def p90(self) -> float | None: ...

    @property
    def model_r_squared(self) -> float | None: ...

    @property
    def model_type(self) -> str | None: ...


def _base_confidence(model_type: str | None) -> float:
    """Prefer registry capabilities; fall back to static map for average/zero."""
    key = model_type or "linear"
    try:
        from app.domain.engines.model_registry import get_model_registry

        model = get_model_registry().get(key)
        if model is not None:
            return float(model.capabilities.base_confidence)
    except Exception:
        pass
    return float(MODEL_BASE_SCORES.get(key, 50))


def compute_confidence_score(result: _ConfidenceInputs) -> float:
    """Compute composite confidence score (0-100) for a single forecast line.

    Blends CV MAPE (not in-sample fit residual) when available:
    - Model CV MAPE (40%): lower MAPE = higher confidence
    - Prediction interval width (25%): narrower = more confident
    - R-squared goodness of fit (20%): higher = better
    - Model type base score (15%): from ModelCapabilities / registry
    """
    scores: list[float] = []
    weights: list[float] = []

    if result.model_mape is not None and result.model_mape > 0:
        mape_score = max(0, 100 - result.model_mape * 5)  # 20% MAPE -> score 0
        scores.append(mape_score)
        weights.append(0.40)

    # `result.p50 != 0` alone is True when p50 is None, which used to reach
    # abs(None) and raise TypeError.
    if (
        result.p10 is not None
        and result.p90 is not None
        and result.p50 is not None
        and result.p50 != 0
    ):
        interval_width = abs(result.p90 - result.p10)
        relative_width = interval_width / (abs(result.p50) + 1e-10)
        width_score = max(0, 100 - relative_width * 100)
        scores.append(width_score)
        weights.append(0.25)

    if result.model_r_squared is not None:
        r2_score = max(0, result.model_r_squared * 100)
        scores.append(r2_score)
        weights.append(0.20)

    base = _base_confidence(result.model_type)
    scores.append(base)
    weights.append(0.15)

    if not scores:
        return 50.0

    total_weight = sum(weights)
    weighted_score = sum(s * w for s, w in zip(scores, weights)) / total_weight
    return round(max(0, min(100, weighted_score)), 1)


def classify_confidence(
    score: float,
    threshold_low: int = 50,
    threshold_medium: int = 70,
) -> str:
    """Classify confidence score into high / medium / low."""
    if score >= threshold_medium:
        return "high"
    if score >= threshold_low:
        return "medium"
    return "low"
