"""arq worker entrypoint for long-running forecast jobs.

Start with:
  cd backend && REDIS_URL=redis://localhost:6379 arq app.workers.arq_worker.WorkerSettings
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from arq import cron

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
        # user_id is None for system-triggered jobs (e.g. the nightly
        # scheduled-sync regeneration) — nobody to attribute those to.
        if user_id:
            from app.services.notifications import create_notification

            version_id = (result.data or {}).get("version_id") if result.data else None
            create_notification(
                db,
                user_id=user_id,
                kind="job_finished",
                title="Forecast generation finished" if result.success else "Forecast generation failed",
                body=result.message,
                entity_type="forecast_version" if version_id else None,
                entity_id=version_id,
                link_panel="forecast_table" if version_id else None,
                link_params={"version_id": version_id} if version_id else None,
                dedup_key=f"job_finished:{job_id}",
            )
            db.commit()
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


async def scheduled_integration_sync(ctx: dict) -> dict:
    """Nightly: pull every auto-pull-enabled connection, then queue a new
    draft baseline if new rows landed *and* one of the just-pulled datasets is
    actually the one that would be used (i.e. no manually uploaded dataset is
    pinned ahead of it — see app/services/actuals_resolution.py). Makes the
    'rolling' in Rolling Forecast real instead of manual-click-only, without
    silently overriding an analyst's fresh manual upload."""
    from app.config import settings
    from app.database import SessionLocal
    from app.services.actuals_resolution import resolve_current_dataset
    from app.services.notifications import notify_users, users_with_permission
    from app.services.scheduled_ingestion import sync_all_enabled_connections

    db = SessionLocal()
    try:
        summary = await sync_all_enabled_connections(db)
        logger.info(
            "scheduled_integration_sync: %s/%s connections synced, %s rows, %s errors",
            summary["connections_synced"],
            summary["connections_checked"],
            summary["total_rows"],
            len(summary["errors"]),
        )
        for err in summary["errors"]:
            logger.warning("scheduled_integration_sync error: %s", err)

        if summary["total_rows"] > 0 and settings.auto_regenerate_on_ingest:
            current = resolve_current_dataset(db)
            pulled_ids = {p["dataset_id"] for p in summary["pulled"] if p.get("dataset_id")}
            if current is not None and current.id in pulled_ids:
                from app.services.job_queue import enqueue_generate_baseline

                job = await enqueue_generate_baseline(
                    version_name="pending",
                    params={"model_type": "auto"},
                    user_id=None,
                    conversation_id="cron-scheduled-integration-sync",
                )
                summary["regenerate_job_id"] = job.get("job_id")
                logger.info(
                    "scheduled_integration_sync: queued baseline regeneration job %s",
                    job.get("job_id"),
                )
            else:
                # New data landed but a manually uploaded dataset remains
                # pinned as current -- don't silently regenerate off (or
                # displace) it. Let can_generate users know instead.
                names = ", ".join(p["connection_name"] for p in summary["pulled"])
                notify_users(
                    db,
                    users_with_permission(db, "can_generate"),
                    kind="actuals_pull_superseded",
                    title="New actuals pulled but not applied",
                    body=(
                        f"New actuals landed from {names}, but a manually uploaded "
                        f"dataset remains authoritative, so no forecast was "
                        f"regenerated. Unpin it (manage_actuals_dataset) to let "
                        f"scheduled data take over."
                    ),
                    entity_type="actuals_dataset",
                    entity_id=current.id if current else None,
                    dedup_key_prefix=f"actuals_pull_superseded:{','.join(sorted(pulled_ids))}",
                )
                db.commit()
                summary["regenerate_skipped_reason"] = "superseded_by_pinned_dataset"
                logger.info(
                    "scheduled_integration_sync: skipped baseline regeneration, "
                    "pinned dataset %s remains current",
                    current.id if current else None,
                )
        return summary
    finally:
        db.close()


async def scheduled_driver_freshness_sweep(ctx: dict) -> dict:
    """Weekly: flag drivers whose last ingest is past the staleness threshold."""
    from app.database import SessionLocal
    from app.models.driver import Driver
    from app.services.driver_ingest import drivers_freshness_batch

    db = SessionLocal()
    try:
        driver_ids = [d.id for d in db.query(Driver).all()]
        if not driver_ids:
            return {"drivers_checked": 0, "stale": []}
        fresh_map = drivers_freshness_batch(db, driver_ids)
        stale = [v for v in fresh_map.values() if v.get("stale")]
        if stale:
            logger.warning(
                "scheduled_driver_freshness_sweep: %s/%s drivers stale (ids=%s)",
                len(stale),
                len(driver_ids),
                [v.get("driver_id") for v in stale],
            )
        else:
            logger.info(
                "scheduled_driver_freshness_sweep: all %s drivers fresh", len(driver_ids)
            )
        return {"drivers_checked": len(driver_ids), "stale": stale}
    finally:
        db.close()


async def scheduled_accuracy_report(ctx: dict) -> dict:
    """Monthly: run the auto_accuracy_report skill for every published version."""
    from app.database import SessionLocal
    from app.domain.base_skill import SkillContext
    from app.domain.skills.auto_accuracy_report import AutoAccuracyReportSkill
    from app.models.forecast import ForecastVersion

    db = SessionLocal()
    try:
        versions = (
            db.query(ForecastVersion).filter(ForecastVersion.status == "published").all()
        )
        skill = AutoAccuracyReportSkill()
        results = []
        for v in versions:
            context = SkillContext(
                db=db,
                context_manager=_StubContextManager("system"),  # type: ignore[arg-type]
                user_id="system",
                user_role="admin",
                conversation_id=f"cron-accuracy-report-{v.id}",
            )
            result = await skill.execute(
                {"version_id": v.id, "report_type": "accuracy_summary"}, context
            )
            results.append({
                "version_id": v.id,
                "version_name": v.name,
                "success": result.success,
                "message": result.message,
            })
            log = logger.info if result.success else logger.warning
            log("scheduled_accuracy_report[%s]: %s", v.name, result.message)
        return {"versions_checked": len(versions), "results": results}
    finally:
        db.close()


async def scheduled_driver_overdue_sweep(ctx: dict) -> dict:
    """Daily: notify BU input-providers whose form has no submission yet and
    is past its soft deadline (a submission itself only records is_late
    *after* it lands — this is the "nobody has submitted at all" case)."""
    from app.database import SessionLocal
    from app.models.driver_input import DriverFormConfig, DriverInput
    from app.models.forecast import ForecastVersion
    from app.services.error_handlers import check_driver_deadline
    from app.services.notifications import notify_users, users_in_business_unit_with_permission

    db = SessionLocal()
    try:
        versions = (
            db.query(ForecastVersion)
            .filter(ForecastVersion.status.in_(["draft", "in_review"]))
            .all()
        )
        forms = db.query(DriverFormConfig).filter(DriverFormConfig.is_active.is_(True)).all()
        overdue_count = 0
        for version in versions:
            submitted_form_ids = {
                row.form_config_id
                for row in db.query(DriverInput.form_config_id).filter(
                    DriverInput.version_id == version.id
                )
            }
            for form in forms:
                if form.id in submitted_form_ids:
                    continue
                deadline = check_driver_deadline(form.soft_deadline_days, form.hard_deadline_days)
                if not (deadline["is_past_soft"] or deadline["is_past_hard"]):
                    continue
                recipients = users_in_business_unit_with_permission(
                    db, form.business_unit, "can_input"
                )
                created = notify_users(
                    db,
                    recipients,
                    kind="driver_overdue",
                    title=f"Driver input overdue: {form.name}",
                    body=deadline["message"] or f"{form.business_unit} hasn't submitted {form.name} yet.",
                    entity_type="driver_form_config",
                    entity_id=str(form.id),
                    link_panel="driver_inputs",
                    link_params={"version_id": version.id},
                    dedup_key_prefix=f"driver_overdue:{version.id}:{form.id}",
                )
                overdue_count += len(created)
        db.commit()
        logger.info(
            "scheduled_driver_overdue_sweep: %s versions, %s forms, %s notifications",
            len(versions), len(forms), overdue_count,
        )
        return {"versions_checked": len(versions), "notifications_created": overdue_count}
    finally:
        db.close()


async def scheduled_anomaly_sweep(ctx: dict) -> dict:
    """Daily: notify reviewers about newly-detected critical anomalies on
    active versions. Reuses the existing (tested) get_anomaly_dashboard route
    logic verbatim under a seeded admin user (who can see every line item),
    so this adds zero new detection code / regression risk to that endpoint.
    Dismissal state reflects that admin's own dismissals, not each
    recipient's — a known simplification, not per-recipient-aware."""
    from app.database import SessionLocal
    from app.api.dashboard import get_anomaly_dashboard
    from app.models.forecast import ForecastVersion
    from app.models.user import Role, User
    from app.services.notifications import notify_users, users_with_permission

    db = SessionLocal()
    try:
        admin = (
            db.query(User)
            .join(Role)
            .filter(Role.name == "admin", User.is_active.is_(True))
            .first()
        )
        if not admin:
            logger.warning("scheduled_anomaly_sweep: no active admin user found, skipping")
            return {"versions_checked": 0, "notifications_created": 0}

        versions = (
            db.query(ForecastVersion)
            .filter(ForecastVersion.status.in_(["draft", "in_review"]))
            .all()
        )
        recipients = users_with_permission(db, "can_review")
        notified = 0
        for version in versions:
            result = await get_anomaly_dashboard(version_id=version.id, current_user=admin, db=db)
            for item in result.data.get("anomalies", []):
                if item.get("worst_severity") != "critical" or item.get("is_dismissed"):
                    continue
                headline = (item.get("findings") or [{}])[0].get("headline")
                created = notify_users(
                    db,
                    recipients,
                    kind="anomaly_detected",
                    title=f"Critical anomaly: {item.get('line_item_name', 'line item')}",
                    body=headline or f"Composite score {item.get('composite_score')}",
                    entity_type="forecast_line_result",
                    entity_id=str(item["id"]),
                    link_panel="anomaly_dashboard",
                    link_params={"version_id": version.id},
                    dedup_key_prefix=f"anomaly_detected:{item['id']}",
                )
                notified += len(created)
        db.commit()
        logger.info(
            "scheduled_anomaly_sweep: %s versions, %s notifications",
            len(versions), notified,
        )
        return {"versions_checked": len(versions), "notifications_created": notified}
    finally:
        db.close()


class WorkerSettings:
    functions = [run_generate_baseline]
    cron_jobs = [
        cron(scheduled_integration_sync, hour={2}, minute=0, run_at_startup=False),
        # Monday 03:00 — weekday follows datetime.weekday() (Mon=0 .. Sun=6)
        cron(scheduled_driver_freshness_sweep, weekday={0}, hour=3, minute=0, run_at_startup=False),
        # 1st of the month, 04:00
        cron(scheduled_accuracy_report, day={1}, hour=4, minute=0, run_at_startup=False),
        # Daily, 07:00 — ahead of the workday
        cron(scheduled_driver_overdue_sweep, hour=7, minute=0, run_at_startup=False),
        cron(scheduled_anomaly_sweep, hour=7, minute=30, run_at_startup=False),
    ]
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
