"""HTTP client for the fm-service Chronos-2 sidecar, with a circuit breaker.

The backend never imports torch: foundation-model inference happens in the
separate ``fm-service`` container (see ``fm-service/``). This module is the
only place that talks to it.

Resilience contract: every failure surfaces as ``FoundationServiceUnavailable``
with a short human-readable reason. Callers turn that into a *skipped model*
(with the reason recorded in ModelMetadata), never a failed forecast run.

Circuit breaker: after ``fm_circuit_failure_threshold`` consecutive failures
the circuit opens and calls fail fast, without touching the network, for
``fm_circuit_reset_seconds``; then one probe call is let through
(half-open). A success closes it again. State is per process.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# Must stay <= fm-service's MAX_SERIES_PER_REQUEST.
MAX_SERIES_PER_REQUEST = 5000
# Test seam: an httpx transport used when forecast_batch gets none (tests
# install an httpx.MockTransport here to stand in for the sidecar).
_DEFAULT_TRANSPORT: httpx.BaseTransport | None = None


class FoundationServiceUnavailable(RuntimeError):
    """The foundation-model service could not produce forecasts."""


@dataclass(frozen=True)
class QuantilePath:
    p10: tuple[float, ...]
    p50: tuple[float, ...]
    p90: tuple[float, ...]


@dataclass
class FoundationBatchResult:
    provenance: dict[str, Any]
    forecasts: dict[str, QuantilePath] = field(default_factory=dict)


class CircuitBreaker:
    def __init__(self, *, failure_threshold: int, reset_seconds: float):
        self.failure_threshold = max(1, int(failure_threshold))
        self.reset_seconds = float(reset_seconds)
        self._failures = 0
        self._opened_at: float | None = None
        self._lock = threading.Lock()

    def before_call(self) -> None:
        with self._lock:
            if self._opened_at is None:
                return
            if time.monotonic() - self._opened_at >= self.reset_seconds:
                return  # half-open: let this one probe through
            raise FoundationServiceUnavailable(
                f"circuit open after {self._failures} consecutive failures; "
                f"retrying in {self.reset_seconds - (time.monotonic() - self._opened_at):.0f}s"
            )

    def record_success(self) -> None:
        with self._lock:
            self._failures = 0
            self._opened_at = None

    def record_failure(self) -> None:
        with self._lock:
            self._failures += 1
            if self._failures >= self.failure_threshold:
                self._opened_at = time.monotonic()

    @property
    def is_open(self) -> bool:
        with self._lock:
            return self._opened_at is not None


_breaker: CircuitBreaker | None = None
_breaker_lock = threading.Lock()


def get_circuit_breaker() -> CircuitBreaker:
    global _breaker
    with _breaker_lock:
        if _breaker is None:
            from app.config import settings

            _breaker = CircuitBreaker(
                failure_threshold=settings.fm_circuit_failure_threshold,
                reset_seconds=settings.fm_circuit_reset_seconds,
            )
        return _breaker


def reset_circuit_breaker() -> None:
    """Forget breaker state (tests, or after reconfiguring the service)."""
    global _breaker
    with _breaker_lock:
        _breaker = None


def _parse_response(payload: dict[str, Any], want: set[str]) -> FoundationBatchResult:
    model = payload.get("model") or {}
    provenance = {
        "model_id": model.get("model_id"),
        "model_revision": model.get("model_revision"),
        "service_version": model.get("service_version"),
        "library_version": model.get("library_version"),
    }
    if not provenance["model_id"] or not provenance["model_revision"]:
        raise FoundationServiceUnavailable("response is missing model provenance")
    out: dict[str, QuantilePath] = {}
    for f in payload.get("forecasts") or []:
        sid = str(f["id"])
        path = QuantilePath(
            p10=tuple(float(v) for v in f["p10"]),
            p50=tuple(float(v) for v in f["p50"]),
            p90=tuple(float(v) for v in f["p90"]),
        )
        if not (len(path.p10) == len(path.p50) == len(path.p90)):
            raise FoundationServiceUnavailable(f"ragged quantiles for series {sid}")
        out[sid] = path
    missing = want - set(out)
    if missing:
        raise FoundationServiceUnavailable(f"response is missing {len(missing)} series")
    return FoundationBatchResult(provenance=provenance, forecasts=out)


def forecast_batch(
    series: Sequence[dict[str, Any]],
    *,
    base_url: str | None = None,
    timeout_seconds: float | None = None,
    transport: httpx.BaseTransport | None = None,
) -> FoundationBatchResult:
    """POST every series to ``/v1/forecast`` and return P10/P50/P90 by id.

    One request per run in practice; only a panel above
    ``MAX_SERIES_PER_REQUEST`` series is split. Raises
    ``FoundationServiceUnavailable`` on any transport, HTTP, or contract
    failure, or while the circuit breaker is open.
    """
    from app.config import settings

    if not series:
        raise FoundationServiceUnavailable("no series to forecast")
    breaker = get_circuit_breaker()
    breaker.before_call()

    url = (base_url or settings.fm_service_url).rstrip("/") + "/v1/forecast"
    timeout = float(timeout_seconds if timeout_seconds is not None else settings.fm_service_timeout_seconds)
    result: FoundationBatchResult | None = None
    try:
        with httpx.Client(timeout=timeout, transport=transport or _DEFAULT_TRANSPORT) as client:
            for start in range(0, len(series), MAX_SERIES_PER_REQUEST):
                chunk = list(series[start : start + MAX_SERIES_PER_REQUEST])
                resp = client.post(url, json={"series": chunk})
                if resp.status_code != 200:
                    raise FoundationServiceUnavailable(
                        f"fm-service returned HTTP {resp.status_code}: {resp.text[:200]}"
                    )
                part = _parse_response(resp.json(), {str(s["id"]) for s in chunk})
                if result is None:
                    result = part
                else:
                    if part.provenance != result.provenance:
                        raise FoundationServiceUnavailable("model changed between request chunks")
                    result.forecasts.update(part.forecasts)
    except FoundationServiceUnavailable:
        breaker.record_failure()
        raise
    except httpx.TimeoutException as e:
        breaker.record_failure()
        raise FoundationServiceUnavailable(f"fm-service timed out after {timeout:.0f}s") from e
    except httpx.HTTPError as e:
        breaker.record_failure()
        raise FoundationServiceUnavailable(f"fm-service unreachable: {type(e).__name__}") from e
    except (ValueError, KeyError, TypeError) as e:
        breaker.record_failure()
        raise FoundationServiceUnavailable(f"malformed fm-service response: {e}"[:200]) from e
    if result is None:  # unreachable: series is non-empty
        raise FoundationServiceUnavailable("no response")
    breaker.record_success()
    return result
