"""Background tasks: slow request work moved onto its own arq queue.

Endpoints that pull from a warehouse/ERP, embed an uploaded document, email a
board pack, rescore every version or run a reflection pass call
``run_or_enqueue``. When jobs should run in the background (Redis configured,
or ``async_jobs_required``, or the caller passed ``async_job=true``) it records
a job and returns ``{"job_id", "status": "queued"}`` for ``GET /api/jobs/{id}``;
otherwise it runs the same handler inline, so both paths share one code path.

Tasks use their own queue (``TASK_QUEUE``) served by
``app.workers.arq_worker.TaskWorkerSettings`` with several slots, so a long
forecast on the single-slot forecast worker never blocks them.

A handler is ``async (db, actor, params) -> dict``. It runs with the actor's
tenant scope pushed (inline: by the request; in the worker: by
``execute_task``), and owns its own commits.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from sqlalchemy.orm import Session

from app.config import settings
from app.services.job_queue import _now, _save, redis_configured, update_job

logger = logging.getLogger(__name__)

TASK_QUEUE = "arq:rf-tasks"

TaskHandler = Callable[[Session, Any, dict[str, Any]], Awaitable[dict[str, Any]]]
_HANDLERS: dict[str, TaskHandler] = {}
# kind -> human label for notifications
_LABELS: dict[str, str] = {}


def task(kind: str, label: str) -> Callable[[TaskHandler], TaskHandler]:
    """Register a handler under ``kind``."""

    def register(fn: TaskHandler) -> TaskHandler:
        _HANDLERS[kind] = fn
        _LABELS[kind] = label
        return fn

    return register


def _handler(kind: str) -> TaskHandler:
    # Handlers live next to their domain code; importing the module registers them.
    import app.services.task_handlers  # noqa: F401

    handler = _HANDLERS.get(kind)
    if handler is None:
        raise KeyError(f"Unknown background task '{kind}'")
    return handler


def should_run_async(async_job: bool | None) -> bool:
    """Explicit choice wins; otherwise background whenever a queue exists or is required."""
    if async_job is not None:
        return async_job
    return bool(settings.async_jobs_required or redis_configured())


class TaskQueueUnavailable(RuntimeError):
    """Background requested but no queue to put it on."""


async def enqueue_task(kind: str, params: dict[str, Any], actor: Any) -> dict[str, Any]:
    _handler(kind)  # fail fast on an unknown kind
    if not redis_configured():
        raise TaskQueueUnavailable(
            "Background jobs need Redis and the task worker (REDIS_URL); "
            "retry with async_job=false to run it now."
        )
    job_id = str(uuid.uuid4())
    record = {
        "job_id": job_id,
        "kind": kind,
        "status": "queued",
        "params": params,
        "user_id": getattr(actor, "id", None),
        "created_at": _now(),
        "result": None,
        "error": None,
        "backend": "arq",
    }
    from arq import create_pool
    from arq.connections import RedisSettings

    pool = await create_pool(RedisSettings.from_dsn(settings.redis_url))
    try:
        await pool.enqueue_job(
            "run_background_task",
            job_id,
            kind,
            params,
            record["user_id"],
            _job_id=job_id,
            _queue_name=TASK_QUEUE,
        )
    finally:
        await pool.aclose()
    _save(record)
    return {"job_id": job_id, "kind": kind, "status": "queued"}


async def run_or_enqueue(
    kind: str,
    params: dict[str, Any],
    *,
    db: Session,
    actor: Any,
    async_job: bool | None,
) -> tuple[dict[str, Any], bool]:
    """(response body, queued?). Inline runs return the handler's own result."""
    if should_run_async(async_job):
        return await enqueue_task(kind, params, actor), True
    return await _handler(kind)(db, actor, params), False


async def execute_task(job_id: str, kind: str, params: dict[str, Any], user_id: str | None) -> dict[str, Any]:
    """Worker side: own session, the actor's tenant scope, status + notification."""
    from app.database import SessionLocal
    from app.models.user import User
    from app.services.notifications import create_notification
    from app.services.tenant_scope import (
        UNRESTRICTED,
        TenantScope,
        push_tenant_scope,
        reset_tenant_scope,
        scope_for_user,
    )

    db = SessionLocal()
    token = push_tenant_scope(UNRESTRICTED)
    try:
        actor = db.query(User).filter(User.id == user_id).first() if user_id else None
    finally:
        reset_tenant_scope(token)
    # A job whose user has vanished runs as nobody (fail closed), not as everybody.
    token = push_tenant_scope(
        scope_for_user(actor) if actor is not None else TenantScope(unrestricted=False)
    )
    label = _LABELS.get(kind, kind)
    update_job(job_id, status="running", step="Starting")
    try:
        result = await _handler(kind)(db, actor, params)
        update_job(job_id, status="completed", result=result, step="Done")
        if user_id:
            create_notification(
                db, user_id=user_id, kind="job_finished", title=f"{label} finished",
                entity_type="job", entity_id=job_id, dedup_key=f"job:{job_id}",
            )
            db.commit()
        return result
    except Exception as e:
        logger.exception("background task %s (%s) failed", job_id, kind)
        db.rollback()
        update_job(job_id, status="failed", error=str(e)[:500])
        if user_id:
            create_notification(
                db, user_id=user_id, kind="job_failed", title=f"{label} failed",
                body=str(e)[:300], entity_type="job", entity_id=job_id, dedup_key=f"job:{job_id}",
            )
            db.commit()
        raise
    finally:
        reset_tenant_scope(token)
        db.close()


async def dispatch_task(
    kind: str,
    params: dict[str, Any],
    *,
    db: Session,
    actor: Any,
    async_job: bool | None,
    response: Any,
) -> dict[str, Any]:
    """Endpoint glue for run_or_enqueue: 202 + job when queued, the handler's
    result when inline; 503 when a queue was required but none exists, 400 for
    a handler's ValueError."""
    from fastapi import HTTPException

    try:
        body, queued = await run_or_enqueue(kind, params, db=db, actor=actor, async_job=async_job)
    except TaskQueueUnavailable as e:
        raise HTTPException(503, str(e)) from e
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    if queued:
        response.status_code = 202
    return body
