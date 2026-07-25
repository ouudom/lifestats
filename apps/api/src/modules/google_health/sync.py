from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import and_, or_, select, true, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import Settings
from src.core.time import utc_now
from src.modules.auth.models import User
from src.modules.google_health.client import GoogleHealthClient
from src.modules.google_health.models import (
    GoogleHealthConnection,
    GoogleHealthRecord,
    GoogleHealthSyncJob,
)
from src.modules.google_health.normalization import normalize_record
from src.modules.google_health.registry import (
    DATA_TYPE_REGISTRY,
    DATA_TYPES,
    DataType,
    FetchMethod,
    PollingTier,
)
from src.modules.google_health.scheduling import (
    daily_noon_retry,
    is_quiet_hour,
    next_allowed_poll,
    next_failure_poll,
    next_regular_poll,
    sync_range,
)

LEASE_DURATION = timedelta(minutes=15)
SOFT_DELETE_RETENTION = timedelta(days=30)
PAGE_TOKEN_RESET_FAILURE_THRESHOLD = 2


@dataclass(frozen=True, slots=True)
class SyncOutcome:
    user_id: int
    data_type: str
    record_count: int
    status: str


def scope_granted(scopes: list[str], required_scope: str) -> bool:
    normalized = {scope.rsplit("/", 1)[-1] for scope in scopes}
    return required_scope in normalized


async def seed_sync_jobs(
    db: AsyncSession,
    connection: GoogleHealthConnection,
    *,
    now: datetime | None = None,
) -> None:
    scheduled_at = (now or utc_now()).astimezone(UTC)
    for data_type in DATA_TYPES:
        enabled = scope_granted(connection.scopes, data_type.scope)
        await db.execute(
            update(GoogleHealthSyncJob)
            .where(
                GoogleHealthSyncJob.connection_id == connection.id,
                GoogleHealthSyncJob.data_type == data_type.endpoint_id,
                GoogleHealthSyncJob.fetch_method != data_type.fetch_method.value,
            )
            .values(
                enabled=False,
                status="completed",
                error="fetch_method_superseded",
                lease_until=None,
                next_page_token=None,
                range_start=None,
                range_end=None,
            )
        )
        statement = (
            insert(GoogleHealthSyncJob)
            .values(
                connection_id=connection.id,
                data_type=data_type.endpoint_id,
                fetch_method=data_type.fetch_method.value,
                enabled=enabled,
                poll_interval_minutes=data_type.poll_interval_minutes,
                initial_lookback_days=data_type.initial_lookback_days,
                incremental_overlap_minutes=data_type.incremental_overlap_minutes,
                page_size=data_type.page_size,
                priority=data_type.priority,
                next_poll_at=scheduled_at,
                status="queued",
                error=None if enabled else "scope_not_granted",
            )
            .on_conflict_do_nothing()
        )
        await db.execute(statement)
        scope_filter = (
            GoogleHealthSyncJob.connection_id == connection.id,
            GoogleHealthSyncJob.data_type == data_type.endpoint_id,
            GoogleHealthSyncJob.fetch_method == data_type.fetch_method.value,
        )
        if enabled:
            await db.execute(
                update(GoogleHealthSyncJob)
                .where(*scope_filter, GoogleHealthSyncJob.error == "scope_not_granted")
                .values(enabled=True, error=None)
            )
        else:
            await db.execute(
                update(GoogleHealthSyncJob)
                .where(*scope_filter)
                .values(enabled=False, error="scope_not_granted")
            )
    await db.commit()


async def claim_due_jobs(
    db: AsyncSession,
    *,
    now: datetime | None = None,
    limit: int = 20,
) -> list[tuple[UUID, str]]:
    claimed_at = (now or utc_now()).astimezone(UTC)
    query = (
        select(GoogleHealthSyncJob, User.timezone)
        .join(
            GoogleHealthConnection,
            GoogleHealthConnection.id == GoogleHealthSyncJob.connection_id,
        )
        .join(User, User.id == GoogleHealthConnection.user_id)
        .where(
            GoogleHealthSyncJob.enabled.is_(True),
            GoogleHealthConnection.status == "active",
            GoogleHealthSyncJob.next_poll_at <= claimed_at,
            or_(
                GoogleHealthSyncJob.lease_until.is_(None),
                GoogleHealthSyncJob.lease_until < claimed_at,
            ),
        )
        .order_by(GoogleHealthSyncJob.priority, GoogleHealthSyncJob.next_poll_at)
        .limit(limit)
        .with_for_update(of=GoogleHealthSyncJob, skip_locked=True)
    )
    rows = (await db.execute(query)).all()
    claimed: list[tuple[UUID, str]] = []
    for job, timezone in rows:
        if is_quiet_hour(claimed_at, timezone):
            job.next_poll_at = next_allowed_poll(claimed_at, timezone)
            job.lease_until = None
            continue
        job.status = "queued"
        job.lease_until = claimed_at + LEASE_DURATION
        claimed.append((job.connection_id, job.data_type))
    await db.commit()
    return claimed


class SyncService:
    def __init__(self, db: AsyncSession, settings: Settings, connection_id: UUID) -> None:
        self.db = db
        self.settings = settings
        self.connection_id = connection_id

    async def sync_type(
        self,
        data_type_id: str,
        *,
        trigger: str = "scheduled",
        requested_start: datetime | None = None,
        requested_end: datetime | None = None,
    ) -> SyncOutcome:
        data_type = DATA_TYPE_REGISTRY.get(data_type_id)
        if data_type is None:
            raise ValueError(f"Unsupported Google Health data type: {data_type_id}")
        connection = await self.db.get(GoogleHealthConnection, self.connection_id)
        if connection is None:
            raise RuntimeError("Google Health connection not found")
        if connection.status != "active":
            raise RuntimeError("Google Health connection is not active")
        await seed_sync_jobs(self.db, connection)
        key = (self.connection_id, data_type.endpoint_id, data_type.fetch_method.value)
        job = await self.db.scalar(
            select(GoogleHealthSyncJob)
            .where(
                GoogleHealthSyncJob.connection_id == self.connection_id,
                GoogleHealthSyncJob.data_type == data_type.endpoint_id,
                GoogleHealthSyncJob.fetch_method == data_type.fetch_method.value,
            )
            .with_for_update()
        )
        if job is None:
            raise RuntimeError("Google Health sync job was not seeded")
        if not job.enabled:
            return SyncOutcome(connection.user_id, data_type.endpoint_id, 0, "disabled")
        now = utc_now()
        if job.status == "running" and job.lease_until and job.lease_until > now:
            return SyncOutcome(connection.user_id, data_type.endpoint_id, 0, "leased")
        job.status = "running"
        job.lease_until = now + LEASE_DURATION
        await self.db.commit()

        timezone = await self.db.scalar(
            select(User.timezone)
            .join(GoogleHealthConnection, GoogleHealthConnection.user_id == User.id)
            .where(GoogleHealthConnection.id == self.connection_id)
        )
        if not timezone:
            timezone = self.settings.app_timezone
        if trigger == "scheduled" and is_quiet_hour(now, timezone):
            job.next_poll_at = next_allowed_poll(now, timezone)
            job.lease_until = None
            await self.db.commit()
            return SyncOutcome(connection.user_id, data_type.endpoint_id, 0, "deferred")

        if job.next_page_token and job.range_start and job.range_end:
            range_start, range_end = job.range_start, job.range_end
        else:
            range_start, desired_end = sync_range(
                now,
                initial_lookback_days=job.initial_lookback_days,
                incremental_overlap_minutes=job.incremental_overlap_minutes,
                last_succeeded_at=job.last_succeeded_at,
                requested_start=requested_start,
                requested_end=requested_end,
            )
            range_end = min(
                desired_end,
                range_start + timedelta(days=data_type.maximum_range_days - 1),
            )

        client: GoogleHealthClient | None = None
        try:
            client = GoogleHealthClient(self.db, self.settings, connection.user_id)
            count = await self._sync_window(client, job, data_type, range_start, range_end)
        except Exception as exc:
            await self.db.rollback()
            job = await self.db.get(GoogleHealthSyncJob, key)
            if job is not None:
                job.status = "failed"
                job.error = str(exc)
                job.lease_until = None
                job.consecutive_failures += 1
                _reset_stuck_page(job)
                job.next_poll_at = next_failure_poll(utc_now(), job.consecutive_failures, timezone)
                await self.db.commit()
            raise
        finally:
            if client is not None:
                await client.close()

        caught_up = range_end >= (requested_end or now) - timedelta(seconds=1)
        job.status = "completed"
        job.error = None
        job.lease_until = None
        job.consecutive_failures = 0
        job.record_count = count
        job.last_succeeded_at = range_end
        job.range_start = None
        job.range_end = None
        job.next_page_token = None
        noon_retry = (
            daily_noon_retry(now, timezone)
            if caught_up and count == 0 and data_type.polling_tier is PollingTier.DAILY
            else None
        )
        job.next_poll_at = noon_retry or (
            next_regular_poll(now, data_type, timezone) if caught_up else utc_now()
        )
        await self.db.commit()
        return SyncOutcome(connection.user_id, data_type.endpoint_id, count, "completed")

    async def _sync_window(
        self,
        client: GoogleHealthClient,
        job: GoogleHealthSyncJob,
        data_type: DataType,
        range_start: datetime,
        range_end: datetime,
    ) -> int:
        resumed = bool(job.next_page_token and job.range_start and job.range_end)
        run_started = job.last_attempted_at if resumed else utc_now()
        if run_started is None:
            run_started = utc_now()
        job.status = "running"
        job.range_start = range_start
        job.range_end = range_end
        job.last_attempted_at = run_started
        job.lease_until = utc_now() + LEASE_DURATION
        await self.db.commit()

        timezone = await self.db.scalar(
            select(User.timezone)
            .join(GoogleHealthConnection, GoogleHealthConnection.user_id == User.id)
            .where(GoogleHealthConnection.id == self.connection_id)
        )
        zone = timezone or self.settings.app_timezone
        start_date = range_start.astimezone(ZoneInfo(zone)).date()
        end_date = range_end.astimezone(ZoneInfo(zone)).date()
        count = 0
        async for points, next_page_token in client.point_pages(
            data_type,
            start_date,
            end_date,
            page_token=job.next_page_token,
        ):
            for point in points:
                await self._upsert(data_type, point, utc_now())
                count += 1
            job.next_page_token = next_page_token
            job.lease_until = utc_now() + LEASE_DURATION
            await self.db.commit()

        await self._soft_delete_missing(
            data_type,
            range_start,
            range_end,
            start_date,
            end_date,
            run_started,
        )
        await self.db.commit()
        return count

    async def _upsert(
        self,
        data_type: DataType,
        point: dict[str, Any],
        synced_at: datetime,
    ) -> None:
        normalized = normalize_record(data_type, point)
        record_type = (
            "rollup" if data_type.fetch_method is FetchMethod.DAILY_ROLLUP else "data_point"
        )
        statement = insert(GoogleHealthRecord).values(
            connection_id=self.connection_id,
            data_type=data_type.endpoint_id,
            record_type=record_type,
            fetch_method=data_type.fetch_method.value,
            provider_name=normalized.provider_name,
            identity_hash=normalized.identity_hash,
            payload_hash=normalized.payload_hash,
            record_date=normalized.record_date,
            started_at=normalized.started_at,
            ended_at=normalized.ended_at,
            source_family=normalized.source_family,
            raw_payload=normalized.raw_payload,
            provider_updated_at=normalized.provider_updated_at,
            first_synced_at=synced_at,
            last_synced_at=synced_at,
            deleted_at=None,
        )
        excluded = statement.excluded
        statement = statement.on_conflict_do_update(
            constraint="uq_gh_records_identity",
            set_={
                "provider_name": excluded.provider_name,
                "payload_hash": excluded.payload_hash,
                "record_date": excluded.record_date,
                "started_at": excluded.started_at,
                "ended_at": excluded.ended_at,
                "source_family": excluded.source_family,
                "raw_payload": excluded.raw_payload,
                "provider_updated_at": excluded.provider_updated_at,
                "last_synced_at": synced_at,
                "deleted_at": None,
            },
        )
        await self.db.execute(statement)

    async def _soft_delete_missing(
        self,
        data_type: DataType,
        range_start: datetime,
        range_end: datetime,
        start_date: date,
        end_date: date,
        run_started: datetime,
    ) -> None:
        in_window = or_(
            and_(
                GoogleHealthRecord.record_date >= start_date,
                GoogleHealthRecord.record_date <= end_date,
            ),
            and_(
                GoogleHealthRecord.started_at >= range_start,
                GoogleHealthRecord.started_at <= range_end,
            ),
        )
        if data_type.filter_field is None:
            in_window = true()
        statement = (
            update(GoogleHealthRecord)
            .where(
                GoogleHealthRecord.connection_id == self.connection_id,
                GoogleHealthRecord.data_type == data_type.endpoint_id,
                GoogleHealthRecord.fetch_method == data_type.fetch_method.value,
                GoogleHealthRecord.last_synced_at < run_started,
                GoogleHealthRecord.deleted_at.is_(None),
                in_window,
            )
            .values(deleted_at=utc_now())
        )
        await self.db.execute(statement)


def _reset_stuck_page(job: GoogleHealthSyncJob) -> None:
    if job.next_page_token and job.consecutive_failures >= PAGE_TOKEN_RESET_FAILURE_THRESHOLD:
        job.next_page_token = None
        job.range_start = None
        job.range_end = None


async def purge_soft_deleted_records(
    db: AsyncSession,
    *,
    now: datetime | None = None,
) -> int:
    from sqlalchemy import delete

    cutoff = (now or utc_now()) - SOFT_DELETE_RETENTION
    result = await db.execute(
        delete(GoogleHealthRecord).where(GoogleHealthRecord.deleted_at < cutoff)
    )
    await db.commit()
    return int(result.rowcount or 0)  # type: ignore[attr-defined]
