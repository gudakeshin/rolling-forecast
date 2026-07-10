"""arq worker entrypoint for long-running forecast jobs.

Start with:
  cd backend && REDIS_URL=redis://localhost:6379 arq app.workers.arq_worker.WorkerSettings
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


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


async def run_generate_baseline(
    ctx: dict,
    job_id: str,
    params: dict[str, Any],
    user_id: str | None,
) -> dict[str, Any]:
    """Execute GenerateBaselineSkill inside the worker process."""
    from app.database import SessionLocal
    from app.domain.skills.generate_baseline import GenerateBaselineSkill
    from app.domain.base_skill import SkillContext
    from app.services.job_queue import update_job

    update_job(job_id, status="running")
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
        params = {**params, "async_job": False}
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
    except Exception as e:
        logger.exception("generate_baseline job %s failed", job_id)
        update_job(job_id, status="failed", error=str(e))
        raise
    finally:
        db.close()


class WorkerSettings:
    functions = [run_generate_baseline]
    redis_settings = None

    @staticmethod
    def on_startup(ctx: dict) -> None:
        logger.info("arq worker started")


def _configure_redis() -> None:
    from app.config import settings

    if not settings.redis_url:
        return
    from arq.connections import RedisSettings

    WorkerSettings.redis_settings = RedisSettings.from_dsn(settings.redis_url)


_configure_redis()
