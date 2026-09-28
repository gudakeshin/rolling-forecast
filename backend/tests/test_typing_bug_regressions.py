"""Regressions for defects surfaced by turning the mypy gate back on.

Each test here corresponds to a bug that was reachable at runtime, not merely
an annotation complaint. The annotation-only fixes are covered by the gate
itself (`mypy app alembic` must exit 0).
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from app.domain.engines.base_model import ModelCapabilities, as_exog_model
from app.services.confidence import compute_confidence_score


@dataclass
class _Inputs:
    """Stands in for ForecastLineResult; matches the _ConfidenceInputs protocol."""

    model_mape: float | None = None
    p10: float | None = None
    p50: float | None = None
    p90: float | None = None
    model_r_squared: float | None = None
    model_type: str | None = "linear"


class TestConfidenceNoneGuard:
    """`result.p50 != 0` is True when p50 is None, so the interval-width branch
    used to reach abs(None) and raise TypeError."""

    def test_none_p50_with_interval_does_not_raise(self):
        score = compute_confidence_score(_Inputs(p10=10.0, p90=30.0, p50=None))
        assert isinstance(score, float)
        assert 0.0 <= score <= 100.0

    def test_none_p50_skips_the_interval_component(self):
        """With p50 unknown the relative width is undefined, so that component
        must be dropped rather than guessed."""
        without = compute_confidence_score(_Inputs(p10=10.0, p90=30.0, p50=None))
        base_only = compute_confidence_score(_Inputs(p50=None))
        assert without == base_only

    def test_zero_p50_still_skips_the_interval_component(self):
        assert compute_confidence_score(
            _Inputs(p10=10.0, p90=30.0, p50=0.0)
        ) == compute_confidence_score(_Inputs(p50=0.0))

    def test_normal_case_still_uses_the_interval(self):
        narrow = compute_confidence_score(_Inputs(p10=99.0, p90=101.0, p50=100.0))
        wide = compute_confidence_score(_Inputs(p10=10.0, p90=190.0, p50=100.0))
        assert narrow > wide


class _FakeModel:
    def __init__(self, supports_exog: bool):
        self._caps = ModelCapabilities(supports_exog=supports_exog)

    @property
    def name(self) -> str:
        return "fake"

    @property
    def capabilities(self) -> ModelCapabilities:
        return self._caps


class TestExogNarrowing:
    """A non-exog model used to raise TypeError deep inside a broad
    `except Exception`, scoring the fold as inf — a misconfigured exog run was
    indistinguishable from a model that simply forecast badly."""

    def test_rejects_a_model_without_exog_support(self):
        with pytest.raises(TypeError, match="does not support exogenous"):
            as_exog_model(_FakeModel(supports_exog=False))  # type: ignore[arg-type]

    def test_error_names_the_model(self):
        with pytest.raises(TypeError, match="'fake'"):
            as_exog_model(_FakeModel(supports_exog=False))  # type: ignore[arg-type]

    def test_passes_through_an_exog_capable_model(self):
        model = _FakeModel(supports_exog=True)
        assert as_exog_model(model) is model  # type: ignore[arg-type]

    def test_arima_is_exog_capable(self):
        """The production path depends on this being the one engine that is."""
        from app.domain.engines.model_registry import get_model_registry

        arima = get_model_registry().get("arima")
        assert arima is not None
        assert as_exog_model(arima) is arima


class TestCategoryCommentaryShape:
    """The empty-data branch returned a content-block dict into a list of
    markdown strings; the caller pipes each section through
    render_fact_placeholders(text: str) before wrapping it in a block."""

    def test_empty_detail_returns_plain_strings(self):
        from app.domain.skills.generate_commentary import GenerateCommentarySkill

        sections = GenerateCommentarySkill()._category_commentary({}, "neutral")
        assert sections, "expected an explanatory section"
        assert all(isinstance(s, str) for s in sections), (
            f"non-string section would break render_fact_placeholders: {sections}"
        )

    def test_populated_detail_also_returns_plain_strings(self):
        from app.domain.skills.generate_commentary import GenerateCommentarySkill

        ctx = {
            "first_period": "2026-01",
            "category_detail": [
                {
                    "name": "Revenue",
                    "value": 1000.0,
                    "confidence": 80.0,
                    "model": "arima",
                    "overridden": False,
                }
            ],
        }
        sections = GenerateCommentarySkill()._category_commentary(ctx, "neutral")
        assert sections and all(isinstance(s, str) for s in sections)

    def test_sections_survive_the_placeholder_renderer(self):
        """The actual failure mode: a dict reaching render_fact_placeholders."""
        from app.domain.skills.generate_commentary import GenerateCommentarySkill
        from app.services.numeric_grounding import render_fact_placeholders

        sections = GenerateCommentarySkill()._category_commentary({}, "neutral")
        for section in sections:
            assert isinstance(render_fact_placeholders(section, {}), str)


class TestFitMetricsAllowMissing:
    """AIC is genuinely unavailable for some engines, and 0.0 is a meaningful
    AIC — collapsing missing to 0.0 would corrupt model comparison."""

    def test_forecast_output_accepts_none_metrics(self):
        import numpy as np

        from app.domain.engines.base_model import ForecastOutput

        out = ForecastOutput(
            point_forecast=np.array([1.0]),
            lower_bound=np.array([0.0]),
            upper_bound=np.array([2.0]),
            periods=["2026-01"],
            model_type="ets",
            fit_metrics={"aic": None, "in_sample_mape": 5.0},
        )
        assert out.fit_metrics["aic"] is None
        assert out.fit_metrics["in_sample_mape"] == 5.0
