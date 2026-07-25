import asyncio
import logging
from collections.abc import Awaitable
from datetime import datetime
from uuid import UUID

from celery import Celery

from src.core.config import get_settings
from src.core.database import SessionFactory, engine
from src.core.logging import configure_logging
from src.modules.google_health.events import publish_sync_completed
from src.modules.google_health.sync import (
    SyncService,
    claim_due_jobs,
    purge_soft_deleted_records,
)
from src.modules.google_health.webhook_processor import process_webhook_event

settings = get_settings()
configure_logging(settings.app_env, settings.log_level)
logger = logging.getLogger(__name__)
celery_app = Celery("lifestats", broker=settings.redis_url, backend=settings.redis_url)
celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    worker_hijack_root_logger=False,
    beat_schedule={
        "dispatch-google-health-every-five-minutes": {
            "task": "lifestats.dispatch_google_health",
            "schedule": 300,
        },
        "purge-google-health-soft-deletes-daily": {
            "task": "lifestats.purge_google_health_soft_deleted",
            "schedule": 86_400,
        },
    },
)


@celery_app.task(name="lifestats.sync_google_health_type")  # type: ignore[untyped-decorator]
def sync_google_health_type(
    connection_id: str,
    data_type: str,
    trigger: str = "scheduled",
    range_start: str | None = None,
    range_end: str | None = None,
) -> None:
    _run_async_task(
        _sync_google_health_type(
            UUID(connection_id),
            data_type,
            trigger,
            _parse_datetime(range_start),
            _parse_datetime(range_end),
        )
    )


@celery_app.task(name="lifestats.dispatch_google_health")  # type: ignore[untyped-decorator]
def dispatch_google_health() -> None:
    _run_async_task(_dispatch_google_health())


@celery_app.task(name="lifestats.purge_google_health_soft_deleted")  # type: ignore[untyped-decorator]
def purge_google_health_soft_deleted() -> None:
    _run_async_task(_purge_google_health_soft_deleted())


@celery_app.task(  # type: ignore[untyped-decorator]
    name="lifestats.process_google_health_webhook",
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_jitter=True,
    max_retries=7,
)
def process_google_health_webhook(event_id: str) -> None:
    _run_async_task(_process_google_health_webhook(UUID(event_id)))


def _run_async_task[T](awaitable: Awaitable[T]) -> T:
    return asyncio.run(_run_and_dispose_engine(awaitable))


async def _run_and_dispose_engine[T](awaitable: Awaitable[T]) -> T:
    try:
        return await awaitable
    finally:
        # Celery reuses worker processes, but asyncio.run() creates a new loop for
        # every task. Dispose pooled asyncpg connections before that loop closes.
        await engine.dispose()


async def _sync_google_health_type(
    connection_id: UUID,
    data_type: str,
    trigger: str,
    range_start: datetime | None,
    range_end: datetime | None,
) -> None:
    logger.info(
        "Google Health sync started",
        extra={
            "event": "google_health_sync_started",
            "connection_id": str(connection_id),
            "data_type": data_type,
            "trigger": trigger,
        },
    )
    if trigger not in {"scheduled", "manual", "webhook"}:
        raise ValueError("Unknown Google Health sync trigger")
    try:
        async with SessionFactory() as db:
            outcome = await SyncService(db, settings, connection_id).sync_type(
                data_type,
                trigger=trigger,
                requested_start=range_start,
                requested_end=range_end,
            )
    except Exception:
        logger.exception(
            "Google Health sync failed",
            extra={
                "event": "google_health_sync_failed",
                "connection_id": str(connection_id),
                "data_type": data_type,
                "trigger": trigger,
            },
        )
        raise
    logger.info(
        "Google Health sync completed",
        extra={
            "event": "google_health_sync_completed",
            "connection_id": str(connection_id),
            "data_type": data_type,
            "trigger": trigger,
            "record_count": outcome.record_count,
            "status": outcome.status,
        },
    )
    if outcome.status == "completed":
        try:
            await publish_sync_completed(
                settings.redis_url,
                user_id=outcome.user_id,
                data_type=outcome.data_type,
                record_count=outcome.record_count,
                trigger=trigger,
            )
        except Exception:
            logger.exception(
                "Google Health sync event publication failed",
                extra={
                    "event": "google_health_sync_event_publish_failed",
                    "connection_id": str(connection_id),
                    "data_type": data_type,
                    "trigger": trigger,
                },
            )


async def _dispatch_google_health() -> None:
    async with SessionFactory() as db:
        jobs = await claim_due_jobs(db)
    logger.info(
        "Google Health jobs dispatched",
        extra={
            "event": "google_health_jobs_dispatched",
            "job_count": len(jobs),
        },
    )
    for connection_id, data_type in jobs:
        sync_google_health_type.delay(str(connection_id), data_type, "scheduled", None, None)


async def _process_google_health_webhook(event_id: UUID) -> None:
    logger.info(
        "Google Health webhook processing started",
        extra={"event": "google_health_webhook_started", "event_id": str(event_id)},
    )
    try:
        async with SessionFactory() as db:
            await process_webhook_event(db, event_id)
    except Exception:
        logger.exception(
            "Google Health webhook processing failed",
            extra={"event": "google_health_webhook_failed", "event_id": str(event_id)},
        )
        raise
    logger.info(
        "Google Health webhook processing completed",
        extra={"event": "google_health_webhook_completed", "event_id": str(event_id)},
    )


async def _purge_google_health_soft_deleted() -> None:
    async with SessionFactory() as db:
        count = await purge_soft_deleted_records(db)
    logger.info(
        "Google Health soft-deleted records purged",
        extra={
            "event": "google_health_soft_deletes_purged",
            "purged_count": count,
        },
    )


def _parse_datetime(value: str | None) -> datetime | None:
    if value is None:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Sync range timestamps must include a UTC offset")
    return parsed
