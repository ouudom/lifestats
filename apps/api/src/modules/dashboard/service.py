from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.capabilities import app_capabilities
from src.core.config import Settings
from src.modules.auth.models import User
from src.modules.google_health.models import (
    GoogleHealthConnection,
    GoogleHealthRecord,
    GoogleHealthSyncJob,
)
from src.modules.google_health.normalization import extract_number
from src.modules.timeline.service import TimelineService


class DashboardService:
    def __init__(self, db: AsyncSession, settings: Settings) -> None:
        self.db = db
        self.settings = settings

    async def get(self, user_id: int, day: date | None) -> dict[str, object]:
        timezone_name = await self.db.scalar(select(User.timezone).where(User.id == user_id))
        timezone = ZoneInfo(timezone_name or self.settings.app_timezone)
        selected_day = day or datetime.now(timezone).date()
        return {
            "capabilities": app_capabilities(self.settings),
            "date": selected_day.isoformat(),
            "timezone": timezone.key,
            "metrics": await self._metrics(user_id, selected_day, timezone),
            "sleep": await self._sleep_detail(user_id, selected_day, timezone),
            "timeline": [
                {
                    "id": item.id,
                    "kind": item.kind,
                    "title": item.title,
                    "occurredAt": item.occurred_at.isoformat(),
                    "source": item.source,
                    "detail": item.detail,
                    "freshness": item.freshness,
                }
                for item in await TimelineService(self.db).for_day(
                    user_id,
                    selected_day,
                    timezone,
                )
            ],
            "sync": await self._sync(user_id),
        }

    async def get_insights(
        self,
        user_id: int,
        start: date,
        end: date,
    ) -> dict[str, object]:
        timezone_name = await self.db.scalar(select(User.timezone).where(User.id == user_id))
        timezone = ZoneInfo(timezone_name or self.settings.app_timezone)
        local_start = datetime.combine(start, time.min, timezone)
        local_end = datetime.combine(end + timedelta(days=1), time.min, timezone)
        utc_start = local_start.astimezone(UTC)
        utc_end = local_end.astimezone(UTC)
        query = (
            select(GoogleHealthRecord)
            .join(
                GoogleHealthConnection,
                GoogleHealthConnection.id == GoogleHealthRecord.connection_id,
            )
            .where(
                GoogleHealthConnection.user_id == user_id,
                GoogleHealthRecord.data_type.in_(("steps", "sleep", "hydration-log")),
                GoogleHealthRecord.deleted_at.is_(None),
                or_(
                    GoogleHealthRecord.record_date.between(start, end),
                    (
                        (GoogleHealthRecord.started_at >= utc_start)
                        & (GoogleHealthRecord.started_at < utc_end)
                    ),
                    (
                        (GoogleHealthRecord.data_type == "sleep")
                        & (GoogleHealthRecord.ended_at >= utc_start)
                        & (GoogleHealthRecord.ended_at < utc_end)
                    ),
                ),
            )
        )
        records = list((await self.db.scalars(query)).all())
        return _insights_from_records(records, start, end, timezone)

    async def _metrics(
        self, user_id: int, day: date, timezone: ZoneInfo
    ) -> list[dict[str, object]]:
        records = await self._records_for_day(
            user_id,
            day,
            timezone,
            ("steps", "sleep", "hydration-log"),
        )
        step_records = [record for record in records if record.data_type == "steps"]
        water_records = [record for record in records if record.data_type == "hydration-log"]
        steps = sum(
            value
            for record in step_records
            if (value := extract_number(record.raw_payload)) is not None
        )
        sleep_row = next(
            (
                record
                for record in sorted(
                    (item for item in records if item.data_type == "sleep"),
                    key=lambda item: (
                        item.ended_at or datetime.min.replace(tzinfo=UTC),
                        item.last_synced_at,
                    ),
                    reverse=True,
                )
            ),
            None,
        )
        sleep_minutes = None
        sleep_updated = None
        if sleep_row:
            payload = sleep_row.raw_payload
            sleep = payload.get("sleep", payload)
            if isinstance(sleep, dict):
                summary = sleep.get("summary", {})
                if isinstance(summary, dict):
                    sleep_minutes = summary.get("minutesAsleep")
            sleep_updated = sleep_row.last_synced_at
        return [
            _metric(
                "Steps",
                steps if step_records else None,
                "steps",
                day,
                max(
                    (record.last_synced_at for record in step_records),
                    default=None,
                ),
            ),
            _metric(
                "Sleep",
                float(sleep_minutes) / 60 if sleep_minutes is not None else None,
                "h",
                day,
                sleep_updated,
            ),
            _metric(
                "Water",
                sum(
                    value
                    for record in water_records
                    if (value := _hydration_milliliters(record.raw_payload)) is not None
                )
                if water_records
                else None,
                "ml",
                day,
                max(
                    (record.last_synced_at for record in water_records),
                    default=None,
                ),
            ),
        ]

    async def _records_for_day(
        self,
        user_id: int,
        day: date,
        timezone: ZoneInfo,
        data_types: tuple[str, ...],
    ) -> list[GoogleHealthRecord]:
        local_start = datetime.combine(day, time.min, timezone)
        utc_start = local_start.astimezone(UTC)
        utc_end = (local_start + timedelta(days=1)).astimezone(UTC)
        query = (
            select(GoogleHealthRecord)
            .join(
                GoogleHealthConnection,
                GoogleHealthConnection.id == GoogleHealthRecord.connection_id,
            )
            .where(
                GoogleHealthConnection.user_id == user_id,
                GoogleHealthRecord.data_type.in_(data_types),
                GoogleHealthRecord.deleted_at.is_(None),
                or_(
                    GoogleHealthRecord.record_date == day,
                    (
                        (GoogleHealthRecord.started_at >= utc_start)
                        & (GoogleHealthRecord.started_at < utc_end)
                    ),
                    (
                        (GoogleHealthRecord.data_type == "sleep")
                        & (GoogleHealthRecord.ended_at >= utc_start)
                        & (GoogleHealthRecord.ended_at < utc_end)
                    ),
                ),
            )
        )
        return list((await self.db.scalars(query)).all())

    async def _sleep_detail(
        self,
        user_id: int,
        day: date,
        timezone: ZoneInfo,
    ) -> dict[str, object] | None:
        records = await self._records_for_day(user_id, day, timezone, ("sleep",))
        row = next(
            (
                record
                for record in sorted(
                    records,
                    key=lambda item: (
                        item.ended_at or datetime.min.replace(tzinfo=UTC),
                        item.last_synced_at,
                    ),
                    reverse=True,
                )
            ),
            None,
        )
        if row is None:
            return None
        detail = _sleep_detail_from_record(row)
        if detail is None:
            return None

        start_at = detail["startAt"]
        end_at = detail["endAt"]
        assert isinstance(start_at, datetime)
        assert isinstance(end_at, datetime)
        heart_rate_query = (
            select(GoogleHealthRecord)
            .join(
                GoogleHealthConnection,
                GoogleHealthConnection.id == GoogleHealthRecord.connection_id,
            )
            .where(
                GoogleHealthConnection.user_id == user_id,
                GoogleHealthRecord.data_type == "heart-rate",
                GoogleHealthRecord.deleted_at.is_(None),
                GoogleHealthRecord.started_at >= start_at,
                GoogleHealthRecord.started_at <= end_at,
            )
            .order_by(GoogleHealthRecord.started_at)
        )
        heart_rate_records = list((await self.db.scalars(heart_rate_query)).all())
        heart_rate_job_query = (
            select(GoogleHealthSyncJob)
            .join(
                GoogleHealthConnection,
                GoogleHealthConnection.id == GoogleHealthSyncJob.connection_id,
            )
            .where(
                GoogleHealthConnection.user_id == user_id,
                GoogleHealthSyncJob.data_type == "heart-rate",
            )
            .order_by(
                GoogleHealthSyncJob.enabled.desc(),
                GoogleHealthSyncJob.updated_at.desc(),
            )
            .limit(1)
        )
        heart_rate_job = await self.db.scalar(heart_rate_job_query)
        resting_query = (
            select(GoogleHealthRecord)
            .join(
                GoogleHealthConnection,
                GoogleHealthConnection.id == GoogleHealthRecord.connection_id,
            )
            .where(
                GoogleHealthConnection.user_id == user_id,
                GoogleHealthRecord.data_type == "daily-resting-heart-rate",
                GoogleHealthRecord.deleted_at.is_(None),
                GoogleHealthRecord.record_date == day,
            )
            .order_by(GoogleHealthRecord.last_synced_at.desc())
            .limit(1)
        )
        resting_record = await self.db.scalar(resting_query)
        detail.update(
            _sleep_heart_rate_detail(
                heart_rate_records,
                resting_record,
                heart_rate_job,
            )
        )
        return detail

    async def _sync(self, user_id: int) -> list[dict[str, object]]:
        query = (
            select(GoogleHealthSyncJob)
            .join(
                GoogleHealthConnection,
                GoogleHealthConnection.id == GoogleHealthSyncJob.connection_id,
            )
            .where(GoogleHealthConnection.user_id == user_id)
            .order_by(GoogleHealthSyncJob.data_type)
        )
        rows = (await self.db.scalars(query)).all()
        return [
            {
                "dataType": row.data_type,
                "status": row.status,
                "lastSyncedAt": (
                    row.last_succeeded_at.isoformat() if row.last_succeeded_at else None
                ),
                "recordCount": row.record_count,
                "error": row.error,
            }
            for row in rows
        ]


def _metric(
    label: str,
    value: float | None,
    unit: str,
    day: date,
    updated_at: datetime | None,
) -> dict[str, object]:
    return {
        "key": label.lower(),
        "label": label,
        "value": value,
        "unit": unit,
        "source": "Google Health",
        "observedAt": day.isoformat(),
        "freshness": _record_freshness(updated_at),
        "availability": "available" if value is not None else "not-synced",
    }


def _sleep_detail_from_record(record: GoogleHealthRecord) -> dict[str, object] | None:
    payload = record.raw_payload
    sleep = payload.get("sleep", payload)
    if not isinstance(sleep, dict):
        return None

    interval = sleep.get("interval", {})
    if not isinstance(interval, dict):
        interval = {}
    start_at = _payload_datetime(interval.get("startTime")) or record.started_at
    end_at = _payload_datetime(interval.get("endTime")) or record.ended_at
    if start_at is None or end_at is None:
        return None

    summary = sleep.get("summary", {})
    if not isinstance(summary, dict):
        summary = {}
    minutes_in_period = _payload_int(summary.get("minutesInSleepPeriod"))
    minutes_asleep = _payload_int(summary.get("minutesAsleep"))

    stage_summaries: dict[str, dict[str, object]] = {}
    raw_stage_summaries = summary.get("stagesSummary", [])
    if isinstance(raw_stage_summaries, list):
        for item in raw_stage_summaries:
            if not isinstance(item, dict) or not isinstance(item.get("type"), str):
                continue
            stage_type = item["type"].upper()
            minutes = _payload_int(item.get("minutes"))
            count = _payload_int(item.get("count"))
            if stage_type not in stage_summaries and minutes is not None and count is not None:
                stage_summaries[stage_type] = {
                    "type": stage_type,
                    "minutes": minutes,
                    "count": count,
                }

    stage_order = ("AWAKE", "RESTLESS", "ASLEEP", "REM", "LIGHT", "DEEP")
    ordered_summaries = [
        stage_summaries[stage_type] for stage_type in stage_order if stage_type in stage_summaries
    ]
    ordered_summaries.extend(
        stage_summary
        for stage_type, stage_summary in stage_summaries.items()
        if stage_type not in stage_order
    )

    stages: list[dict[str, object]] = []
    raw_stages = sleep.get("stages", [])
    if isinstance(raw_stages, list):
        for item in raw_stages:
            if not isinstance(item, dict) or not isinstance(item.get("type"), str):
                continue
            stage_start = _payload_datetime(item.get("startTime"))
            stage_end = _payload_datetime(item.get("endTime"))
            if stage_start is None or stage_end is None or stage_end <= stage_start:
                continue
            stages.append(
                {
                    "type": item["type"].upper(),
                    "startAt": stage_start,
                    "endAt": stage_end,
                }
            )

    efficiency = None
    if minutes_asleep is not None and minutes_in_period:
        efficiency = round(minutes_asleep / minutes_in_period * 100, 1)

    return {
        "sessionId": str(record.id),
        "startAt": start_at,
        "endAt": end_at,
        "minutesInSleepPeriod": minutes_in_period,
        "minutesAsleep": minutes_asleep,
        "minutesAwake": _payload_int(summary.get("minutesAwake")),
        "minutesToFallAsleep": _payload_int(summary.get("minutesToFallAsleep")),
        "minutesAfterWakeUp": _payload_int(summary.get("minutesAfterWakeUp")),
        "sleepEfficiency": efficiency,
        "stages": stages,
        "stageSummary": ordered_summaries,
        **_sleep_heart_rate_detail([], None),
        "source": "Google Health",
        "freshness": _record_freshness(record.last_synced_at),
        "availability": "available",
        "derivation": (
            "Sleep efficiency is calculated by LifeStats from Google Health minutes asleep "
            "and minutes in the sleep period."
        ),
        "lastSyncedAt": record.last_synced_at,
    }


def _sleep_heart_rate_detail(
    records: list[GoogleHealthRecord],
    resting_record: GoogleHealthRecord | None,
    sync_job: GoogleHealthSyncJob | None = None,
) -> dict[str, object]:
    samples_by_source: dict[str, dict[datetime, tuple[float, datetime]]] = {}
    for record in records:
        if record.started_at is None:
            continue
        beats_per_minute = extract_number(record.raw_payload)
        if beats_per_minute is None or beats_per_minute <= 0:
            continue
        source_family = getattr(record, "source_family", None)
        source = (
            source_family.strip()
            if isinstance(source_family, str) and source_family.strip()
            else "Google Health"
        )
        source_samples = samples_by_source.setdefault(source, {})
        existing = source_samples.get(record.started_at)
        if existing is None or record.last_synced_at >= existing[1]:
            source_samples[record.started_at] = (beats_per_minute, record.last_synced_at)

    selected_source = "Google Health"
    selected_samples: dict[datetime, tuple[float, datetime]] = {}
    if samples_by_source:
        selected_source, selected_samples = max(
            samples_by_source.items(),
            key=lambda item: (
                _sample_coverage_seconds(
                    [(observed_at, value[0]) for observed_at, value in item[1].items()]
                ),
                len(item[1]),
                max(value[1] for value in item[1].values()),
                item[0],
            ),
        )
    samples = sorted((observed_at, value[0]) for observed_at, value in selected_samples.items())

    resting_heart_rate = (
        extract_number(resting_record.raw_payload) if resting_record is not None else None
    )
    if resting_heart_rate is not None and resting_heart_rate <= 0:
        resting_heart_rate = None

    weighted_beats = 0.0
    weighted_seconds = 0.0
    above_seconds = 0.0
    below_seconds = 0.0
    for (observed_at, beats_per_minute), (next_observed_at, _) in zip(
        samples, samples[1:], strict=False
    ):
        duration = (next_observed_at - observed_at).total_seconds()
        if duration <= 0 or duration > 15 * 60:
            continue
        weighted_beats += beats_per_minute * duration
        weighted_seconds += duration
        if resting_heart_rate is not None:
            if beats_per_minute > resting_heart_rate:
                above_seconds += duration
            else:
                below_seconds += duration
    average = (
        round(weighted_beats / weighted_seconds, 1)
        if weighted_seconds
        else (round(samples[0][1], 1) if len(samples) == 1 else None)
    )
    compared_seconds = above_seconds + below_seconds
    percent_above = round(above_seconds / compared_seconds * 100, 1) if compared_seconds else None
    percent_below = round(below_seconds / compared_seconds * 100, 1) if compared_seconds else None
    last_synced_at = max(
        (value[1] for value in selected_samples.values()),
        default=None,
    )
    availability = _heart_rate_availability(bool(samples), sync_job)

    return {
        "heartRateSamples": [
            {"observedAt": observed_at, "beatsPerMinute": beats_per_minute}
            for observed_at, beats_per_minute in samples
        ],
        "averageSleepingHeartRate": average,
        "restingHeartRate": resting_heart_rate,
        "percentAboveResting": percent_above,
        "percentBelowResting": percent_below,
        "heartRateAvailability": availability,
        "heartRateFreshness": _record_freshness(last_synced_at),
        "heartRateSource": selected_source,
        "heartRateDerivation": (
            "Calculated by LifeStats from one Google Health source across the sleep period. "
            "Duplicate timestamps are removed. Average and resting comparison are "
            "time-weighted; gaps over 15 minutes are excluded."
        ),
    }


def _sample_coverage_seconds(samples: list[tuple[datetime, float]]) -> float:
    ordered = sorted(samples)
    return sum(
        duration
        for (observed_at, _), (next_observed_at, _) in zip(ordered, ordered[1:], strict=False)
        if 0 < (duration := (next_observed_at - observed_at).total_seconds()) <= 15 * 60
    )


def _heart_rate_availability(
    has_samples: bool,
    sync_job: GoogleHealthSyncJob | None,
) -> str:
    if has_samples:
        return "available"
    if sync_job is None:
        return "not-synced"
    if not sync_job.enabled and sync_job.error == "scope_not_granted":
        return "permission-missing"
    if sync_job.status == "failed":
        return "failed"
    if sync_job.status in {"queued", "running"}:
        return "syncing"
    return "not-synced"


def _payload_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(UTC) if parsed.tzinfo else None


def _payload_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _record_freshness(updated_at: datetime | None) -> str:
    if updated_at is None:
        return "unknown"
    age = datetime.now(UTC) - updated_at.astimezone(UTC)
    return "stale" if age > timedelta(days=1) else "fresh"


def _insights_from_records(
    records: list[GoogleHealthRecord],
    start: date,
    end: date,
    timezone: ZoneInfo,
) -> dict[str, object]:
    steps_by_day: dict[date, int] = {}
    step_buckets: dict[datetime, int] = {}
    water_by_day: dict[date, float] = {}
    water_entries: list[dict[str, object]] = []
    sleep_by_day: dict[date, dict[str, object]] = {}

    for record in records:
        if record.data_type == "steps":
            value = extract_number(record.raw_payload)
            if value is None:
                continue
            observed = record.record_date
            if observed is None and record.started_at is not None:
                observed = record.started_at.astimezone(timezone).date()
            if observed is None or not start <= observed <= end:
                continue
            integer_value = round(value)
            steps_by_day[observed] = steps_by_day.get(observed, 0) + integer_value
            if record.started_at is not None and observed == end:
                local_start = record.started_at.astimezone(timezone)
                bucket = local_start.replace(minute=0, second=0, microsecond=0)
                step_buckets[bucket] = step_buckets.get(bucket, 0) + integer_value
            continue

        if record.data_type == "hydration-log":
            water_value = _hydration_milliliters(record.raw_payload)
            if water_value is None:
                continue
            observed = record.record_date
            if observed is None and record.started_at is not None:
                observed = record.started_at.astimezone(timezone).date()
            if observed is None or not start <= observed <= end:
                continue
            water_by_day[observed] = water_by_day.get(observed, 0) + water_value
            if record.started_at is not None:
                water_entries.append(
                    {
                        "startedAt": record.started_at.astimezone(timezone),
                        "value": water_value,
                    }
                )
            continue

        if record.data_type != "sleep":
            continue
        detail = _sleep_detail_from_record(record)
        if detail is None:
            continue
        end_at = detail["endAt"]
        if not isinstance(end_at, datetime):
            continue
        observed = end_at.astimezone(timezone).date()
        if not start <= observed <= end:
            continue
        current = sleep_by_day.get(observed)
        current_end = current.get("endAt") if current else None
        if current is None or (isinstance(current_end, datetime) and end_at > current_end):
            raw_stage_summary = detail["stageSummary"]
            stage_minutes = (
                {
                    item["type"]: item["minutes"]
                    for item in raw_stage_summary
                    if isinstance(item, dict)
                }
                if isinstance(raw_stage_summary, list)
                else {}
            )
            sleep_by_day[observed] = {
                "date": observed.isoformat(),
                "minutesAsleep": detail["minutesAsleep"],
                "minutesInSleepPeriod": detail["minutesInSleepPeriod"],
                "minutesAwake": detail["minutesAwake"],
                "sleepEfficiency": detail["sleepEfficiency"],
                "minutesDeep": stage_minutes.get("DEEP"),
                "minutesLight": stage_minutes.get("LIGHT"),
                "minutesRem": stage_minutes.get("REM"),
                "startAt": detail["startAt"],
                "endAt": end_at,
            }

    last_synced_at = max((record.last_synced_at for record in records), default=None)
    available = bool(steps_by_day or water_by_day or sleep_by_day)
    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "timezone": timezone.key,
        "source": "Google Health",
        "derivation": "LifeStats projection",
        "freshness": _record_freshness(last_synced_at),
        "availability": "available" if available else "not-synced",
        "steps": [
            {"date": observed.isoformat(), "value": value}
            for observed, value in sorted(steps_by_day.items())
        ],
        "stepBuckets": [
            {"startedAt": started_at, "value": value}
            for started_at, value in sorted(step_buckets.items())
        ],
        "water": [
            {"date": observed.isoformat(), "value": value}
            for observed, value in sorted(water_by_day.items())
        ],
        "waterEntries": sorted(water_entries, key=lambda entry: str(entry["startedAt"])),
        "sleep": [point for _, point in sorted(sleep_by_day.items())],
    }


def _hydration_milliliters(value: Any) -> float | None:
    if not isinstance(value, dict):
        return None
    for key, nested in value.items():
        if key == "milliliters" and not isinstance(nested, bool):
            try:
                amount = float(nested)
            except (TypeError, ValueError):
                return None
            return amount if amount >= 0 else None
        if isinstance(nested, dict):
            nested_amount = _hydration_milliliters(nested)
            if nested_amount is not None:
                return nested_amount
    return None
