"""Unit tests for the batching rules — no torch, a fake predict_quantiles."""

from __future__ import annotations

import numpy as np
import pytest

from app.forecaster import run_batch
from app.schemas import ForecastRequest, SeriesInput


def _fake(calls: list[dict]):
    def predict_quantiles(tasks, *, prediction_length, quantile_levels, cross_learning, batch_size):
        calls.append({"n": len(tasks), "h": prediction_length, "cross": cross_learning,
                      "keys": sorted(tasks[0].get("past_covariates", {}))})
        out = []
        for t in tasks:
            last = float(t["target"][-1])
            # deliberately NON-monotone heads (p90 < p10) to prove sorting
            q = np.array([[last + 1, last, last - 1]] * prediction_length)[None, ...]
            out.append(q)
        return out, [None] * len(tasks)
    return predict_quantiles


def test_buckets_by_schema_and_group_and_sorts_quantiles():
    series = [
        SeriesInput(id="a", target=[1, 2, 3], prediction_length=2),
        SeriesInput(id="b", target=[4, 5, 6], prediction_length=2),
        SeriesInput(id="c", target=[1, 2, 3], prediction_length=2,
                    past_covariates={"d": [1, 2, 3]}, future_covariates={"d": [4, 5]}),
        SeriesInput(id="g1", target=[1, 2], prediction_length=2, group_id="g"),
        SeriesInput(id="g2", target=[3, 4], prediction_length=2, group_id="g"),
    ]
    calls: list[dict] = []
    out = run_batch(series, _fake(calls))
    assert [f.id for f in out] == ["a", "b", "c", "g1", "g2"]
    for f in out:
        assert all(lo <= m <= hi for lo, m, hi in zip(f.p10, f.p50, f.p90))
    # solo plain (a,b) pooled, solo covariate (c) separate, group g joint
    assert sorted((c["n"], c["cross"]) for c in calls) == [(1, False), (2, False), (2, True)]


def test_future_covariate_needs_past():
    with pytest.raises(ValueError):
        SeriesInput(id="x", target=[1, 2], prediction_length=1, future_covariates={"d": [1]})


def test_duplicate_ids_rejected():
    with pytest.raises(ValueError):
        ForecastRequest(series=[
            SeriesInput(id="x", target=[1], prediction_length=1),
            SeriesInput(id="x", target=[1], prediction_length=1),
        ])
