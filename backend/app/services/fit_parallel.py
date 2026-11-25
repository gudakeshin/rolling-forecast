"""Off-event-loop, parallel line fitting for baseline runs.

A baseline run is CPU-bound: every line item runs rolling-origin CV over the
whole model roster. Run inline on the arq worker's event loop, that work
starved the loop — the progress-stall watchdog could not fire until the run
ended, arq heartbeats stalled, and ``max_jobs`` had to be 1.

This module provides:

* a process pool (``spawn`` context, so no forked SQLAlchemy connections or
  Redis sockets) that the arq worker creates at startup and registers with
  :func:`set_fit_executor`;
* :class:`LineFitTask` — a picklable description of one line fit; the child
  opens its own DB session and re-loads the line item by id;
* :func:`iter_line_forecasts` — yields one ``LineForecast`` per task, in
  input order, either serially (no executor: API path, tests) or from the
  pool. Both paths produce identical results for the same inputs;
* a cooperative cancel flag per job (:func:`request_cancel`), checked by the
  baseline loop between lines, so a stalled or timed-out job actually stops
  instead of running on after its awaiting task was cancelled.

Only the per-line fit runs in the pool. Preparation (actuals, FX, calendar
reindex) and every DB write stay in the parent, which owns the transaction.
"""

from __future__ import annotations

import logging
import multiprocessing
import os
import threading
from concurrent.futures import Executor, Future, ProcessPoolExecutor
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any, Callable, Iterator

if TYPE_CHECKING:
    from app.services.forecast_pipeline import LineForecast, LineForecastContext

logger = logging.getLogger(__name__)

# Below this many lines the pool's pickling overhead outweighs the speed-up.
PARALLEL_MIN_LINES = 4
_ENV_PROCESSES = "FORECAST_FIT_PROCESSES"

_executor: Executor | None = None
_cancelled: set[str] = set()
_cancel_lock = threading.Lock()


# ── pool lifecycle ───────────────────────────────────────────────────────


def fit_processes() -> int:
    """Worker processes for line fits: ``$FORECAST_FIT_PROCESSES`` or cores − 1."""
    raw = os.environ.get(_ENV_PROCESSES, "").strip()
    if raw:
        try:
            return max(1, int(raw))
        except ValueError:
            logger.warning("ignoring non-integer %s=%r", _ENV_PROCESSES, raw)
    return max(1, (os.cpu_count() or 2) - 1)


def create_fit_pool(processes: int | None = None) -> ProcessPoolExecutor:
    return ProcessPoolExecutor(
        max_workers=processes or fit_processes(),
        mp_context=multiprocessing.get_context("spawn"),
    )


def set_fit_executor(executor: Executor | None) -> None:
    global _executor
    _executor = executor


def get_fit_executor() -> Executor | None:
    return _executor


# ── cooperative cancellation ─────────────────────────────────────────────


def request_cancel(job_id: str) -> None:
    with _cancel_lock:
        _cancelled.add(job_id)


def clear_cancel(job_id: str) -> None:
    with _cancel_lock:
        _cancelled.discard(job_id)


def is_cancelled(job_id: str | None) -> bool:
    if not job_id:
        return False
    with _cancel_lock:
        return job_id in _cancelled


# ── line fit tasks ───────────────────────────────────────────────────────


@dataclass
class LineFitTask:
    """Everything one line fit needs, in a form that pickles across processes."""

    line_item_id: int
    values: list[float]
    dates: list[str]  # ISO timestamps
    periods: list[str]
    is_material: bool
    ctx: "LineForecastContext"
    tenant_scope: Any = None


def _current_tenant_scope() -> Any:
    # tenant_scope exposes push/reset but no getter; the child must run under
    # the same scope as the job that dispatched it.
    from app.services import tenant_scope

    return tenant_scope._current.get()


def make_task(
    *,
    line_item_id: int,
    values: list[float],
    dates: Any,
    periods: list[str],
    is_material: bool,
    ctx: "LineForecastContext",
) -> LineFitTask:
    return LineFitTask(
        line_item_id=line_item_id,
        values=[float(v) for v in values],
        dates=[str(d.isoformat()) for d in dates],
        periods=list(periods),
        is_material=bool(is_material),
        ctx=ctx,
        tenant_scope=_current_tenant_scope(),
    )


def _series(task: LineFitTask) -> tuple[Any, Any]:
    import pandas as pd

    return pd.Series(task.values), pd.DatetimeIndex([pd.Timestamp(d) for d in task.dates])


def run_task_with_session(db: Any, li: Any, task: LineFitTask) -> "LineForecast":
    """Fit one line on an existing session (the serial path)."""
    from app.services.forecast_pipeline import forecast_line_item

    values, dates = _series(task)
    ctx = replace(task.ctx, is_material=task.is_material)
    return forecast_line_item(db, li, values, dates, task.periods, ctx)


def fit_line_task(task: LineFitTask) -> "LineForecast":
    """Pool entry point: own session, own tenant scope and calendar."""
    from app.database import SessionLocal
    from app.models.line_item import LineItem
    from app.services.period_calendar import push_calendar, reset_calendar
    from app.services.tenant_scope import UNRESTRICTED, push_tenant_scope, reset_tenant_scope

    scope_token = push_tenant_scope(task.tenant_scope or UNRESTRICTED)
    cal_token = push_calendar(task.ctx.cal_cfg) if task.ctx.cal_cfg is not None else None
    db = SessionLocal()
    try:
        li = db.get(LineItem, task.line_item_id)
        if li is None:
            from app.services.forecast_pipeline import LineForecast

            return LineForecast(
                line_item_id=task.line_item_id,
                line_item_name=str(task.line_item_id),
                skipped=True,
                skip_reason="line_item_not_found",
            )
        out = run_task_with_session(db, li, task)
        # Detach ORM rows built in this session so they travel back cleanly.
        db.expunge_all()
        return out
    finally:
        db.close()
        if cal_token is not None:
            reset_calendar(cal_token)
        reset_tenant_scope(scope_token)


class LineForecastStream:
    """Ordered results for a batch of line fits; ``close()`` cancels the rest."""

    def __init__(
        self,
        tasks: list[LineFitTask],
        *,
        serial_fn: Callable[[LineFitTask], "LineForecast"],
        executor: Executor | None = None,
    ) -> None:
        self._tasks = tasks
        self._serial_fn = serial_fn
        self._executor = executor
        self._futures: list[Future] = []
        if executor is not None and tasks:
            self._futures = [executor.submit(fit_line_task, t) for t in tasks]
        self._iter = self._run()

    @property
    def parallel(self) -> bool:
        return self._executor is not None

    def _run(self) -> Iterator["LineForecast"]:
        if self._executor is None:
            for task in self._tasks:
                yield self._serial_fn(task)
            return
        for fut in self._futures:
            yield fut.result()

    def __iter__(self) -> "LineForecastStream":
        return self

    def __next__(self) -> "LineForecast":
        return next(self._iter)

    def close(self) -> None:
        for fut in self._futures:
            fut.cancel()

