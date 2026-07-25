from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from src.core.capabilities import AppCapabilitiesResponse


class MetricResponse(BaseModel):
    key: str
    label: str
    value: float | None
    unit: str
    source: str
    observedAt: str
    freshness: str
    availability: str


class TimelineItemResponse(BaseModel):
    id: str
    kind: str
    title: str
    occurredAt: str
    source: str
    detail: str | None
    freshness: str


class SyncStateResponse(BaseModel):
    dataType: str
    status: str
    lastSyncedAt: str | None
    recordCount: int
    error: str | None


class SleepStageSegmentResponse(BaseModel):
    type: str
    startAt: datetime
    endAt: datetime


class SleepStageSummaryResponse(BaseModel):
    type: str
    minutes: int
    count: int


class SleepHeartRateSampleResponse(BaseModel):
    observedAt: datetime
    beatsPerMinute: float


class SleepDetailResponse(BaseModel):
    sessionId: str
    startAt: datetime
    endAt: datetime
    minutesInSleepPeriod: int | None
    minutesAsleep: int | None
    minutesAwake: int | None
    minutesToFallAsleep: int | None
    minutesAfterWakeUp: int | None
    sleepEfficiency: float | None
    stages: list[SleepStageSegmentResponse]
    stageSummary: list[SleepStageSummaryResponse]
    heartRateSamples: list[SleepHeartRateSampleResponse]
    averageSleepingHeartRate: float | None
    restingHeartRate: float | None
    percentAboveResting: float | None
    percentBelowResting: float | None
    heartRateAvailability: Literal[
        "available",
        "syncing",
        "permission-missing",
        "failed",
        "not-synced",
    ]
    heartRateFreshness: str
    heartRateSource: str
    heartRateDerivation: str
    source: str
    freshness: str
    availability: str
    derivation: str
    lastSyncedAt: datetime


class DashboardResponse(BaseModel):
    capabilities: AppCapabilitiesResponse
    date: str
    timezone: str
    metrics: list[MetricResponse]
    timeline: list[TimelineItemResponse]
    sync: list[SyncStateResponse]
    sleep: SleepDetailResponse | None


class StepsPointResponse(BaseModel):
    date: str
    value: int


class StepsBucketResponse(BaseModel):
    startedAt: datetime
    value: int


class WaterPointResponse(BaseModel):
    date: str
    value: float


class WaterEntryResponse(BaseModel):
    startedAt: datetime
    value: float


class SleepTrendPointResponse(BaseModel):
    date: str
    minutesAsleep: int | None
    minutesInSleepPeriod: int | None
    minutesAwake: int | None
    sleepEfficiency: float | None
    minutesDeep: int | None
    minutesLight: int | None
    minutesRem: int | None
    startAt: datetime
    endAt: datetime


class InsightsResponse(BaseModel):
    start: str
    end: str
    timezone: str
    source: str
    derivation: str
    freshness: str
    availability: str
    steps: list[StepsPointResponse]
    stepBuckets: list[StepsBucketResponse]
    water: list[WaterPointResponse]
    waterEntries: list[WaterEntryResponse]
    sleep: list[SleepTrendPointResponse]
