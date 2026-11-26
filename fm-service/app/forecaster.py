"""Batch Chronos-2 inference with group-aware batching.

``run_batch`` is pure orchestration over a ``predict_quantiles`` callable so
the grouping rules are unit-testable without torch. ``load_pipeline`` is the
only function that touches torch / chronos.

Chronos-2 requires every task in one ``predict`` call to share a covariate
schema and prediction length, so series are bucketed by
``(prediction_length, past keys, future keys)`` and then:

* series with a ``group_id`` are predicted per group with
  ``cross_learning=True`` (group attention across the group's members only);
* series without one are pooled into a single call with
  ``cross_learning=False`` (each task attends only to its own covariates).
"""

from __future__ import annotations

import os
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np

from app.schemas import QUANTILE_LEVELS, SeriesForecast, SeriesInput

PredictFn = Callable[..., tuple[list[Any], list[Any]]]


@dataclass(frozen=True)
class _Bucket:
    prediction_length: int
    past_keys: tuple[str, ...]
    future_keys: tuple[str, ...]
    group_id: str | None


def _as_array(values: Sequence[float | None]) -> np.ndarray:
    return np.asarray([np.nan if v is None else float(v) for v in values], dtype=np.float32)


def _task(s: SeriesInput) -> dict[str, Any]:
    task: dict[str, Any] = {"target": _as_array(s.target)}
    if s.past_covariates:
        task["past_covariates"] = {k: _as_array(v) for k, v in sorted(s.past_covariates.items())}
    if s.future_covariates:
        task["future_covariates"] = {k: _as_array(v) for k, v in sorted(s.future_covariates.items())}
    return task


def _bucket_key(s: SeriesInput) -> _Bucket:
    return _Bucket(
        prediction_length=s.prediction_length,
        past_keys=tuple(sorted(s.past_covariates)),
        future_keys=tuple(sorted(s.future_covariates)),
        group_id=s.group_id,
    )


def _to_numpy(x: Any) -> np.ndarray:
    if hasattr(x, "detach"):
        x = x.detach().cpu().numpy()
    return np.asarray(x, dtype=np.float64)


def run_batch(
    series: Sequence[SeriesInput],
    predict_quantiles: PredictFn,
    *,
    batch_size: int = 256,
) -> list[SeriesForecast]:
    """Forecast every series; output order matches input order."""
    buckets: dict[_Bucket, list[int]] = {}
    for idx, s in enumerate(series):
        buckets.setdefault(_bucket_key(s), []).append(idx)

    results: dict[int, SeriesForecast] = {}
    # Sorted so the call sequence (and therefore any batch-size effect) is a
    # pure function of the request, never of dict insertion order.
    for key in sorted(buckets, key=lambda b: (b.group_id or "", b.prediction_length, b.past_keys, b.future_keys)):
        idxs = buckets[key]
        tasks = [_task(series[i]) for i in idxs]
        quantiles, _mean = predict_quantiles(
            tasks,
            prediction_length=key.prediction_length,
            quantile_levels=list(QUANTILE_LEVELS),
            cross_learning=key.group_id is not None and len(idxs) > 1,
            batch_size=batch_size,
        )
        if len(quantiles) != len(idxs):
            raise RuntimeError(
                f"model returned {len(quantiles)} forecasts for {len(idxs)} series"
            )
        for i, q in zip(idxs, quantiles):
            arr = _to_numpy(q)
            # (n_variates, h, n_q) -> univariate target: (h, n_q)
            if arr.ndim == 3:
                arr = arr[0]
            if arr.shape != (key.prediction_length, len(QUANTILE_LEVELS)):
                raise RuntimeError(f"unexpected forecast shape {arr.shape} for series {series[i].id}")
            if not np.all(np.isfinite(arr)):
                raise RuntimeError(f"non-finite forecast for series {series[i].id}")
            # Quantile heads are not guaranteed monotone; sort per step so
            # P10 <= P50 <= P90 always holds for consumers.
            arr = np.sort(arr, axis=1)
            results[i] = SeriesForecast(
                id=series[i].id,
                p10=[float(v) for v in arr[:, 0]],
                p50=[float(v) for v in arr[:, 1]],
                p90=[float(v) for v in arr[:, 2]],
            )
    return [results[i] for i in range(len(series))]


def load_pipeline(model_dir: str) -> Any:
    """Load Chronos-2 from the weights baked into the image, CPU only.

    HF_HUB_OFFLINE is forced so a missing or corrupt local copy fails loudly
    at startup instead of silently downloading something else.
    """
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
    import torch
    from chronos import Chronos2Pipeline

    threads = int(os.environ.get("FM_TORCH_THREADS", "0") or 0)
    if threads > 0:
        torch.set_num_threads(threads)
    torch.manual_seed(0)
    torch.use_deterministic_algorithms(True, warn_only=True)
    return Chronos2Pipeline.from_pretrained(model_dir, device_map="cpu")
