"""arq worker entrypoint for long-running forecast jobs.

Start with:
  cd backend && REDIS_URL=redis://localhost:6379 arq app.workers.arq_worker.WorkerSettings
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


class ProgressStalledError(RuntimeError):
    """Raised when a job's progress marker stops advancing."""


class _StubContextManager:
    """Minimal context manager for worker-side skill execution."""

    def __init__(self, user_id: str):
        self._mem: dict[str, Any] = {}
        self._user_id = user_id

    def get_memory(self, key: str, default: Any = None) -> Any:
        return self._mem.get(key, default)

    def set_memory(self, key: str, value: Any) -> None:
        self._mem[key] = value

    def get_active_version_id(self) -> str | None:
        return self._mem.get("active_version_id")

    def set_active_version_id(self, version_id: str) -> None:
        self._mem["active_version_id"] = version_id


def _parse_iso(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None


async def _progress_watchdog(job_id: str, stall_minutes: float) -> None:
    """Fail when progress/step has not advanced for ``stall_minutes``."""
    from app.services.job_queue import get_job

    # Allow sub-second polls in tests; production uses 5–30s.
    poll_seconds = min(30.0, max(0.05, stall_minutes * 60 / 4))
    while True:
        await asyncio.sleep(poll_seconds)
        job = get_job(job_id) or {}
        status = job.get("status")
        if status in ("completed", "failed"):
            return
        marker = job.get("progress_updated_at") or job.get("updated_at") or job.get("created_at")
        last = _parse_iso(marker)
        if last is None:
            continue
        # Normalize naive timestamps to UTC for comparison
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        age_seconds = (datetime.now(timezone.utc) - last).total_seconds()
        if age_seconds > stall_minutes * 60:
            raise ProgressStalledError(
                f"Job {job_id} progress stalled for {stall_minutes:.0f} minutes "
                f"(last progress at {marker}, step={job.get('step')!r})"
            )


async def _execute_baseline(
    job_id: str,
    params: dict[str, Any],
    user_id: str | None,
) -> dict[str, Any]:
    from app.database import SessionLocal
    from app.domain.skills.generate_baseline import GenerateBaselineSkill
    from app.domain.base_skill import SkillContext
    from app.services.job_queue import update_job

    db = SessionLocal()
    try:
        uid = user_id or "system"
        skill = GenerateBaselineSkill()
        context = SkillContext(
            db=db,
            context_manager=_StubContextManager(uid),  # type: ignore[arg-type]
            user_id=uid,
            user_role="admin",
            conversation_id=f"job-{job_id}",
        )
        params = {**params, "async_job": False, "_job_id": job_id}
        result = await skill.execute(params, context)
        payload = {
            "success": result.success,
            "message": result.message,
            "data": result.data,
        }
        update_job(
            job_id,
            status="completed" if result.success else "failed",
            result=payload,
            error=None if result.success else result.message,
        )
        return payload
    finally:
        db.close()


async def run_generate_baseline(
    ctx: dict,
    job_id: str,
    params: dict[str, Any],
    user_id: str | None,
) -> dict[str, Any]:
    """Execute GenerateBaselineSkill inside the worker process with a stall watchdog."""
    from app.config import settings
    from app.services.job_queue import update_job

    update_job(job_id, status="running", progress=0.1, step="Starting")

    stall_minutes = float(settings.job_progress_stall_minutes)
    skill_task = asyncio.create_task(_execute_baseline(job_id, params, user_id))
    watchdog_task = asyncio.create_task(_progress_watchdog(job_id, stall_minutes))

    try:
        done, pending = await asyncio.wait(
            {skill_task, watchdog_task},
            return_when=asyncio.FIRST_COMPLETED,
        )

        if skill_task in done:
            for task in pending:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            return skill_task.result()

        # Watchdog finished first — stall or job already terminal
        for task in pending:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        exc = watchdog_task.exception()
        if isinstance(exc, ProgressStalledError):
            logger.error("%s", exc)
            update_job(job_id, status="failed", error=str(exc))
            raise exc
        if exc is not None:
            raise exc
        # Watchdog exited because job was already terminal; skill may still hold the result
        if skill_task.done() and not skill_task.cancelled():
            return skill_task.result()
        raise ProgressStalledError(f"Job {job_id} ended without a skill result")
    except ProgressStalledError:
        raise
    except Exception as e:
        logger.exception("generate_baseline job %s failed", job_id)
        update_job(job_id, status="failed", error=str(e))
        raise


class WorkerSettings:
    functions = [run_generate_baseline]
    redis_settings: Any = None
    # arq default is 300s — a full CoA baseline needs far longer, but raising
    # the timeout alone with max_jobs=1 blocks the only worker on a hang.
    # Pair with the progress stall watchdog above.
    job_timeout = 3600
    max_jobs = 1

    @staticmethod
    def on_startup(ctx: dict) -> None:
        from app.config import settings

        # Prefer settings so env can override without editing this module
        WorkerSettings.job_timeout = int(settings.job_timeout_seconds)
        logger.info(
            "arq worker started (job_timeout=%ss, stall=%smin, max_jobs=%s)",
            WorkerSettings.job_timeout,
            settings.job_progress_stall_minutes,
            WorkerSettings.max_jobs,
        )


def _configure_redis() -> None:
    from app.config import settings

    if not settings.redis_url:
        return
    from arq.connections import RedisSettings

    WorkerSettings.redis_settings = RedisSettings.from_dsn(settings.redis_url)
    WorkerSettings.job_timeout = int(settings.job_timeout_seconds)


_configure_redis()
