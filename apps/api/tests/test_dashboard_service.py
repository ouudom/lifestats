from datetime import UTC, date, datetime
from types import SimpleNamespace
from uuid import UUID
from zoneinfo import ZoneInfo

from src.modules.dashboard.service import (
    _hydration_milliliters,
    _insights_from_records,
    _sleep_detail_from_record,
    _sleep_heart_rate_detail,
)


def test_sleep_detail_exposes_stages_and_deduplicates_summary() -> None:
    stage_summary = [
        {"type": "AWAKE", "minutes": "104", "count": "4"},
        {"type": "LIGHT", "minutes": "200", "count": "14"},
        {"type": "DEEP", "minutes": "83", "count": "4"},
        {"type": "REM", "minutes": "66", "count": "8"},
    ]
    record = SimpleNamespace(
        id=UUID("5aca88c2-0825-4676-a7c0-4b2c59ff4fb7"),
        started_at=datetime(2026, 7, 23, 17, 58, tzinfo=UTC),
        ended_at=datetime(2026, 7, 24, 1, 31, tzinfo=UTC),
        last_synced_at=datetime(2026, 7, 24, 9, 45, tzinfo=UTC),
        raw_payload={
            "sleep": {
                "interval": {
                    "startTime": "2026-07-23T17:58:00Z",
                    "endTime": "2026-07-24T01:31:00Z",
                },
                "stages": [
                    {
                        "type": "DEEP",
                        "startTime": "2026-07-23T18:19:30Z",
                        "endTime": "2026-07-23T18:41:30Z",
                    }
                ],
                "summary": {
                    "minutesInSleepPeriod": "453",
                    "minutesAsleep": "349",
                    "minutesAwake": "104",
                    "minutesToFallAsleep": "0",
                    "minutesAfterWakeUp": "0",
                    "stagesSummary": stage_summary + stage_summary,
                },
            }
        },
    )

    detail = _sleep_detail_from_record(record)

    assert detail is not None
    assert detail["minutesAsleep"] == 349
    assert detail["sleepEfficiency"] == 77.0
    assert detail["stageSummary"] == [
        {"type": "AWAKE", "minutes": 104, "count": 4},
        {"type": "REM", "minutes": 66, "count": 8},
        {"type": "LIGHT", "minutes": 200, "count": 14},
        {"type": "DEEP", "minutes": 83, "count": 4},
    ]
    assert detail["stages"] == [
        {
            "type": "DEEP",
            "startAt": datetime(2026, 7, 23, 18, 19, 30, tzinfo=UTC),
            "endAt": datetime(2026, 7, 23, 18, 41, 30, tzinfo=UTC),
        }
    ]


def test_sleep_detail_requires_session_interval() -> None:
    record = SimpleNamespace(
        id=UUID("5aca88c2-0825-4676-a7c0-4b2c59ff4fb7"),
        started_at=None,
        ended_at=None,
        last_synced_at=datetime(2026, 7, 24, 9, 45, tzinfo=UTC),
        raw_payload={"sleep": {"summary": {"minutesAsleep": "349"}}},
    )

    assert _sleep_detail_from_record(record) is None


def test_sleep_heart_rate_uses_samples_and_excludes_large_gaps() -> None:
    synced_at = datetime(2026, 7, 24, 9, 45, tzinfo=UTC)
    records = [
        SimpleNamespace(
            started_at=datetime(2026, 7, 23, 18, 0, tzinfo=UTC),
            last_synced_at=synced_at,
            raw_payload={"heartRate": {"beatsPerMinute": 50}},
        ),
        SimpleNamespace(
            started_at=datetime(2026, 7, 23, 18, 10, tzinfo=UTC),
            last_synced_at=synced_at,
            raw_payload={"heartRate": {"beatsPerMinute": 70}},
        ),
        SimpleNamespace(
            started_at=datetime(2026, 7, 23, 18, 20, tzinfo=UTC),
            last_synced_at=synced_at,
            raw_payload={"heartRate": {"beatsPerMinute": 55}},
        ),
        SimpleNamespace(
            started_at=datetime(2026, 7, 23, 19, 0, tzinfo=UTC),
            last_synced_at=synced_at,
            raw_payload={"heartRate": {"beatsPerMinute": 90}},
        ),
    ]
    resting = SimpleNamespace(
        last_synced_at=synced_at,
        raw_payload={"dailyRestingHeartRate": {"beatsPerMinute": 60}},
    )

    detail = _sleep_heart_rate_detail(records, resting)

    assert detail["averageSleepingHeartRate"] == 60.0
    assert detail["restingHeartRate"] == 60.0
    assert detail["percentAboveResting"] == 50.0
    assert detail["percentBelowResting"] == 50.0
    assert len(detail["heartRateSamples"]) == 4
    assert detail["heartRateSource"] == "Google Health"


def test_sleep_heart_rate_is_unavailable_without_samples() -> None:
    detail = _sleep_heart_rate_detail([], None)

    assert detail["heartRateAvailability"] == "not-synced"
    assert detail["heartRateFreshness"] == "unknown"
    assert detail["averageSleepingHeartRate"] is None
    assert detail["percentAboveResting"] is None


def test_sleep_heart_rate_selects_one_source_and_deduplicates_timestamps() -> None:
    first_sync = datetime(2026, 7, 24, 9, 40, tzinfo=UTC)
    latest_sync = datetime(2026, 7, 24, 9, 45, tzinfo=UTC)
    records = [
        SimpleNamespace(
            source_family="Pixel Watch",
            started_at=datetime(2026, 7, 23, 18, 0, tzinfo=UTC),
            last_synced_at=latest_sync,
            raw_payload={"heartRate": {"beatsPerMinute": 50}},
        ),
        SimpleNamespace(
            source_family="Pixel Watch",
            started_at=datetime(2026, 7, 23, 18, 10, tzinfo=UTC),
            last_synced_at=first_sync,
            raw_payload={"heartRate": {"beatsPerMinute": 70}},
        ),
        SimpleNamespace(
            source_family="Pixel Watch",
            started_at=datetime(2026, 7, 23, 18, 10, tzinfo=UTC),
            last_synced_at=latest_sync,
            raw_payload={"heartRate": {"beatsPerMinute": 80}},
        ),
        SimpleNamespace(
            source_family="Pixel Watch",
            started_at=datetime(2026, 7, 23, 18, 20, tzinfo=UTC),
            last_synced_at=latest_sync,
            raw_payload={"heartRate": {"beatsPerMinute": 60}},
        ),
        SimpleNamespace(
            source_family="Phone",
            started_at=datetime(2026, 7, 23, 18, 0, tzinfo=UTC),
            last_synced_at=latest_sync,
            raw_payload={"heartRate": {"beatsPerMinute": 120}},
        ),
        SimpleNamespace(
            source_family="Phone",
            started_at=datetime(2026, 7, 23, 18, 5, tzinfo=UTC),
            last_synced_at=latest_sync,
            raw_payload={"heartRate": {"beatsPerMinute": 120}},
        ),
    ]

    detail = _sleep_heart_rate_detail(records, None)

    assert detail["heartRateSource"] == "Pixel Watch"
    assert detail["averageSleepingHeartRate"] == 65.0
    assert detail["heartRateSamples"] == [
        {
            "observedAt": datetime(2026, 7, 23, 18, 0, tzinfo=UTC),
            "beatsPerMinute": 50.0,
        },
        {
            "observedAt": datetime(2026, 7, 23, 18, 10, tzinfo=UTC),
            "beatsPerMinute": 80.0,
        },
        {
            "observedAt": datetime(2026, 7, 23, 18, 20, tzinfo=UTC),
            "beatsPerMinute": 60.0,
        },
    ]


def test_sleep_heart_rate_reports_sync_state_without_using_resting_freshness() -> None:
    resting = SimpleNamespace(
        last_synced_at=datetime.now(UTC),
        raw_payload={"dailyRestingHeartRate": {"beatsPerMinute": 60}},
    )
    missing_permission = SimpleNamespace(
        enabled=False,
        error="scope_not_granted",
        status="completed",
    )
    failed = SimpleNamespace(enabled=True, error="provider_error", status="failed")
    running = SimpleNamespace(enabled=True, error=None, status="running")

    permission_detail = _sleep_heart_rate_detail([], resting, missing_permission)
    failed_detail = _sleep_heart_rate_detail([], resting, failed)
    running_detail = _sleep_heart_rate_detail([], resting, running)

    assert permission_detail["heartRateAvailability"] == "permission-missing"
    assert failed_detail["heartRateAvailability"] == "failed"
    assert running_detail["heartRateAvailability"] == "syncing"
    assert permission_detail["heartRateFreshness"] == "unknown"


def test_insights_aggregate_steps_and_sleep_by_wake_date() -> None:
    records = [
        SimpleNamespace(
            data_type="steps",
            record_date=date(2026, 7, 24),
            started_at=datetime(2026, 7, 24, 2, 10, tzinfo=UTC),
            last_synced_at=datetime(2026, 7, 24, 9, 45, tzinfo=UTC),
            raw_payload={"steps": {"count": {"value": "1200"}}},
        ),
        SimpleNamespace(
            data_type="steps",
            record_date=date(2026, 7, 24),
            started_at=datetime(2026, 7, 24, 2, 40, tzinfo=UTC),
            last_synced_at=datetime(2026, 7, 24, 9, 45, tzinfo=UTC),
            raw_payload={"steps": {"count": {"value": "361"}}},
        ),
        SimpleNamespace(
            data_type="hydration-log",
            record_date=None,
            started_at=datetime(2026, 7, 24, 2, 46, tzinfo=UTC),
            last_synced_at=datetime(2026, 7, 24, 9, 45, tzinfo=UTC),
            raw_payload={
                "hydrationLog": {
                    "interval": {"startTime": "2026-07-24T02:46:00Z"},
                    "water": {"volume": {"milliliters": "500"}},
                }
            },
        ),
        SimpleNamespace(
            id=UUID("5aca88c2-0825-4676-a7c0-4b2c59ff4fb7"),
            data_type="sleep",
            record_date=None,
            started_at=datetime(2026, 7, 23, 17, 58, tzinfo=UTC),
            ended_at=datetime(2026, 7, 24, 1, 31, tzinfo=UTC),
            last_synced_at=datetime(2026, 7, 24, 9, 45, tzinfo=UTC),
            raw_payload={
                "sleep": {
                    "interval": {
                        "startTime": "2026-07-23T17:58:00Z",
                        "endTime": "2026-07-24T01:31:00Z",
                    },
                    "summary": {
                        "minutesInSleepPeriod": "453",
                        "minutesAsleep": "349",
                        "minutesAwake": "104",
                        "stagesSummary": [
                            {"type": "DEEP", "minutes": "83", "count": "4"},
                            {"type": "LIGHT", "minutes": "200", "count": "14"},
                            {"type": "REM", "minutes": "66", "count": "8"},
                        ],
                    },
                }
            },
        ),
    ]

    insights = _insights_from_records(
        records,
        date(2026, 7, 18),
        date(2026, 7, 24),
        ZoneInfo("Asia/Phnom_Penh"),
    )

    assert insights["steps"] == [{"date": "2026-07-24", "value": 1561}]
    assert insights["stepBuckets"] == [
        {
            "startedAt": datetime(2026, 7, 24, 9, tzinfo=ZoneInfo("Asia/Phnom_Penh")),
            "value": 1561,
        }
    ]
    assert insights["water"] == [{"date": "2026-07-24", "value": 500.0}]
    assert insights["waterEntries"] == [
        {
            "startedAt": datetime(2026, 7, 24, 9, 46, tzinfo=ZoneInfo("Asia/Phnom_Penh")),
            "value": 500.0,
        }
    ]
    assert insights["sleep"] == [
        {
            "date": "2026-07-24",
            "minutesAsleep": 349,
            "minutesInSleepPeriod": 453,
            "minutesAwake": 104,
            "sleepEfficiency": 77.0,
            "minutesDeep": 83,
            "minutesLight": 200,
            "minutesRem": 66,
            "startAt": datetime(2026, 7, 23, 17, 58, tzinfo=UTC),
            "endAt": datetime(2026, 7, 24, 1, 31, tzinfo=UTC),
        }
    ]


def test_hydration_milliliters_ignores_unrelated_numbers() -> None:
    assert (
        _hydration_milliliters(
            {
                "hydrationLog": {
                    "interval": {"startTime": "2026-07-24T02:46:00Z"},
                    "water": {"volume": {"milliliters": "375.5"}},
                }
            }
        )
        == 375.5
    )
    assert _hydration_milliliters({"hydrationLog": {"water": {"amount": 500}}}) is None
