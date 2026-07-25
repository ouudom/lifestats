"use client";

import { Card, Chip, Surface, Typography } from "@heroui/react";
import { useQuery } from "@tanstack/react-query";
import { HeartPulse, Moon } from "lucide-react";
import { motion, useReducedMotion } from "motion/react";
import { useState } from "react";
import { EvilAnimatedLineChart } from "@/components/charts/EvilCharts";
import { AppAlert } from "@/components/ui/AppAlert";
import { EmptyContent } from "@/components/ui/EmptyContent";
import { PageHeader } from "@/components/ui/PageHeader";
import { SectionHeader } from "@/components/ui/SectionHeader";
import { api } from "@/lib/api";
import type { Dashboard, Insights } from "@/lib/types";
import {
  calendarRangeEnd,
  DateRangeControls,
  dateTick,
  insightsPath,
  type RangeKey,
  rangeLabel,
} from "../insights";

type SleepDetail = NonNullable<Dashboard["sleep"]>;
type StageSegment = SleepDetail["stages"][number];

const STAGE_META: Record<string, { label: string; color: string }> = {
  AWAKE: { label: "Awake", color: "var(--sleep-awake)" },
  RESTLESS: { label: "Restless", color: "var(--sleep-restless)" },
  ASLEEP: { label: "Asleep", color: "var(--sleep-light)" },
  REM: { label: "REM", color: "var(--sleep-rem)" },
  LIGHT: { label: "Light", color: "var(--sleep-light)" },
  DEEP: { label: "Deep", color: "var(--sleep-deep)" },
};

export function SleepOverview({
  data,
  date,
}: {
  data: Dashboard;
  date: string;
}) {
  const [range, setRange] = useState<RangeKey>("day");
  const [endDate, setEndDate] = useState(date);
  const insights = useQuery({
    queryKey: ["insights", range, endDate],
    queryFn: () => api<Insights>(insightsPath(endDate, range)),
    enabled: range !== "day",
  });
  const selectedDashboard = useQuery({
    queryKey: ["dashboard", endDate],
    queryFn: () => api<Dashboard>(`/dashboard?date=${endDate}`),
    enabled: range === "day" && endDate !== date,
  });
  const dayData = endDate === date ? data : selectedDashboard.data;
  const detail = dayData?.sleep;
  const sessions = dayData?.timeline.filter((item) => item.kind === "sleep") ?? [];

  return (
    <div className="mx-auto grid w-full max-w-6xl gap-6">
      <PageHeader title="Sleep" />

      <DateRangeControls
        end={endDate}
        max={date}
        onEndChange={setEndDate}
        onRangeChange={(nextRange) => {
          setRange(nextRange);
          setEndDate(calendarRangeEnd(endDate, nextRange, date));
        }}
        range={range}
      />

      {range === "day" ? (
        <>
          {selectedDashboard.isPending && endDate !== date ? (
            <Card variant="secondary">
              <Card.Content className="min-h-80 animate-pulse" />
            </Card>
          ) : selectedDashboard.isError && endDate !== date ? (
            <AppAlert
              message={selectedDashboard.error.message}
              title="Sleep day unavailable"
            />
          ) : detail && dayData ? (
            <>
              <SleepHero detail={detail} timezone={dayData.timezone} />
              <SleepTimeline detail={detail} timezone={dayData.timezone} />
              <SleepingHeartRate detail={detail} timezone={dayData.timezone} />
              <SleepFacts detail={detail} />
            </>
          ) : (
            <Card variant="secondary">
              <Card.Content>
                <EmptyContent
                  description="Try syncing Google Health or selecting another date."
                  icon={<Moon className="size-6" />}
                  title="No sleep session synced"
                />
              </Card.Content>
            </Card>
          )}
          {dayData && <SleepSessions sessions={sessions} timezone={dayData.timezone} />}
        </>
      ) : (
        <>
          {insights.isPending && (
            <Card variant="secondary">
              <Card.Content className="min-h-80 animate-pulse" />
            </Card>
          )}
          {insights.isError && (
            <AppAlert message={insights.error.message} title="Sleep history unavailable" />
          )}
          {insights.data && <SleepTrendCard insights={insights.data} range={range} />}
        </>
      )}
    </div>
  );
}

function SleepTrendCard({ insights, range }: { insights: Insights; range: RangeKey }) {
  const [metric, setMetric] = useState<"duration" | "efficiency" | "deep">("duration");
  const metricConfig = {
    duration: {
      label: "Total sleep",
      unit: "",
      value: (point: Insights["sleep"][number]) => point.minutesAsleep,
      format: formatMinutesNumber,
    },
    efficiency: {
      label: "Sleep efficiency",
      unit: "",
      value: (point: Insights["sleep"][number]) => point.sleepEfficiency,
      format: (value: number) => `${Math.round(value)}%`,
    },
    deep: {
      label: "Deep sleep",
      unit: "",
      value: (point: Insights["sleep"][number]) => point.minutesDeep,
      format: formatMinutesNumber,
    },
  }[metric];
  const chartData = insights.sleep.map((point) => ({
    label: dateTick(point.date, range),
    value: metricConfig.value(point),
    detail: new Date(`${point.date}T12:00:00Z`).toLocaleDateString([], {
      dateStyle: "full",
    }),
  }));
  const values = chartData
    .map((point) => point.value)
    .filter((value): value is number => value != null);
  const average = values.length
    ? values.reduce((sum, value) => sum + value, 0) / values.length
    : null;

  return (
    <>
      <Card variant="default" aria-labelledby="sleep-trend-title">
        <Card.Header>
          <SectionHeader
            action={
              <Chip color="success" size="sm" variant="soft">
                <Chip.Label>{insights.freshness}</Chip.Label>
              </Chip>
            }
            eyebrow={rangeLabel(insights)}
            id="sleep-trend-title"
            title={metricConfig.label}
          />
        </Card.Header>
        <Card.Content className="grid gap-6">
          <div className="flex flex-wrap items-end gap-2">
            <span className="text-5xl font-bold tracking-[-0.04em] tabular-nums max-sm:text-4xl">
              {average == null ? "—" : metricConfig.format(average)}
            </span>
            <span className="pb-1 text-sm font-semibold text-muted">per recorded night</span>
          </div>
          <div className="flex flex-wrap gap-2" role="group" aria-label="Sleep chart metric">
            {[
              ["duration", "Total sleep"],
              ["efficiency", "Efficiency"],
              ["deep", "Deep sleep"],
            ].map(([key, label]) => (
              <button
                aria-pressed={metric === key}
                className={`min-h-10 rounded-xl border px-4 text-sm font-semibold transition-colors ${
                  metric === key
                    ? "border-[var(--info)] bg-[var(--info)] text-[var(--canvas)]"
                    : "border-[var(--border-strong)] text-muted hover:text-foreground"
                }`}
                key={key}
                onClick={() => setMetric(key as "duration" | "efficiency" | "deep")}
                type="button"
              >
                {label}
              </button>
            ))}
          </div>
          {/* <EvilAnimatedLineChart
            data={
              chartData.length
                ? chartData
                : emptyChartSeries(insights.start, insights.end, range)
            }
            emptyMessage={chartData.length ? undefined : "No sleep data in this range"}
            formatValue={metricConfig.format}
            reference={average ?? undefined}
            valueLabel={metricConfig.unit}
          /> */}
        </Card.Content>
      </Card>

      <Card variant="secondary" aria-labelledby="sleep-nights-title">
        <Card.Header>
          <SectionHeader
            action={
              <Chip size="sm" variant="tertiary">
                <Chip.Label>{insights.sleep.length} nights</Chip.Label>
              </Chip>
            }
            id="sleep-nights-title"
            title="Nightly sleep"
          />
        </Card.Header>
        <Card.Content>
          {insights.sleep.length ? (
            <ol className="grid">
              {[...insights.sleep].reverse().slice(0, 14).map((point) => (
                <li
                  className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-4 border-b border-separator py-3 last:border-0"
                  key={point.date}
                >
                  <div>
                    <time className="text-sm text-muted" dateTime={point.date}>
                      {new Date(`${point.date}T12:00:00Z`).toLocaleDateString([], {
                        day: "numeric",
                        month: "short",
                        weekday: "short",
                      })}
                    </time>
                    <div className="mt-1 text-xs text-muted">
                      {formatTime(point.startAt, insights.timezone)}–
                      {formatTime(point.endAt, insights.timezone)}
                    </div>
                  </div>
                  <Typography className="tabular-nums" weight="semibold">
                    {formatMinutes(point.minutesAsleep)}
                  </Typography>
                </li>
              ))}
            </ol>
          ) : (
            <EmptyContent
              description="Google Health has no sleep sessions for this range."
              title="No recorded nights"
            />
          )}
        </Card.Content>
      </Card>

      <Surface className="flex flex-wrap justify-between gap-2 p-4" variant="tertiary">
        <Typography color="muted" type="body-xs">
          {insights.source} · {insights.derivation}
        </Typography>
        <Typography color="muted" type="body-xs">
          Missing nights are not treated as zero.
        </Typography>
      </Surface>
    </>
  );
}

function SleepHero({ detail, timezone }: { detail: SleepDetail; timezone: string }) {
  return (
    <Card variant="default" aria-labelledby="sleep-duration-title">
      <Card.Content className="grid gap-8 p-7 max-sm:p-5">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <Typography.Heading id="sleep-duration-title" level={2}>
              Sleep duration
            </Typography.Heading>
          </div>
          <Chip color="success" size="sm" variant="soft">
            <Chip.Label>{detail.freshness}</Chip.Label>
          </Chip>
        </div>

        <div className="flex flex-wrap items-end gap-x-5 gap-y-2">
          <span className="text-6xl font-bold leading-none tracking-[-0.04em] tabular-nums max-sm:text-5xl">
            {formatMinutes(detail.minutesAsleep)}
          </span>
          <span className="pb-1 text-lg font-semibold tabular-nums text-muted">
            {formatTime(detail.startAt, timezone)}–{formatTime(detail.endAt, timezone)}
          </span>
        </div>

        <Typography.Paragraph className="max-w-[70ch]" color="muted" size="sm">
          Google Health recorded {formatMinutes(detail.minutesInSleepPeriod)} in the sleep
          period. Sleep efficiency was {formatPercent(detail.sleepEfficiency)}, calculated
          from synced duration fields.
        </Typography.Paragraph>

        <dl className="grid grid-cols-4 gap-3 max-lg:grid-cols-2 max-sm:grid-cols-1">
          <Metric label="In sleep period" value={formatMinutes(detail.minutesInSleepPeriod)} />
          <Metric label="Asleep" value={formatMinutes(detail.minutesAsleep)} />
          <Metric label="Awake" value={formatMinutes(detail.minutesAwake)} />
          <Metric label="Sleep efficiency" value={formatPercent(detail.sleepEfficiency)} />
        </dl>
      </Card.Content>
    </Card>
  );
}

function SleepTimeline({ detail, timezone }: { detail: SleepDetail; timezone: string }) {
  const sessionStart = new Date(detail.startAt).getTime();
  const sessionEnd = new Date(detail.endAt).getTime();
  const midpoint = new Date(sessionStart + (sessionEnd - sessionStart) / 2).toISOString();
  const stageTypes = detail.stageSummary
    .map((stage) => stage.type)
    .filter((type) => type in STAGE_META);

  return (
    <Card variant="secondary" aria-labelledby="sleep-stages-title">
      <Card.Header>
        <SectionHeader
          action={
            <Chip size="sm" variant="soft">
              <Chip.Label>{detail.stages.length} segments</Chip.Label>
            </Chip>
          }
          // eyebrow="Sleep composition"
          id="sleep-stages-title"
          title="Stage timeline"
        />
      </Card.Header>
      <Card.Content className="grid gap-5">
        {stageTypes.length ? (
          <div className="grid gap-4">
            {stageTypes.map((type) => {
              const summary = detail.stageSummary.find((stage) => stage.type === type);
              const meta = STAGE_META[type];
              return (
                <div className="grid gap-2" key={type}>
                  <div className="flex items-baseline justify-between gap-3">
                    <Typography weight="semibold">{meta.label}</Typography>
                    <Typography className="tabular-nums" color="muted" type="body-sm">
                      {formatMinutes(summary?.minutes ?? null)} · {summary?.count ?? 0} periods
                    </Typography>
                  </div>
                  <StageTrack
                    color={meta.color}
                    end={sessionEnd}
                    label={`${meta.label} stage timeline`}
                    segments={detail.stages.filter((stage) => stage.type === type)}
                    start={sessionStart}
                  />
                </div>
              );
            })}
            <div className="flex justify-between text-xs tabular-nums text-muted">
              <time dateTime={detail.startAt}>{formatTime(detail.startAt, timezone)}</time>
              <time dateTime={midpoint}>{formatTime(midpoint, timezone)}</time>
              <time dateTime={detail.endAt}>{formatTime(detail.endAt, timezone)}</time>
            </div>
          </div>
        ) : (
          <EmptyContent
            description="This session has duration data but no processed sleep stages."
            title="Sleep stages unavailable"
          />
        )}
        <Typography.Paragraph color="muted" size="xs">
          Stage colors show Google Health classifications. They do not indicate good or bad
          sleep.
        </Typography.Paragraph>
      </Card.Content>
    </Card>
  );
}

function StageTrack({
  color,
  end,
  label,
  segments,
  start,
}: {
  color: string;
  end: number;
  label: string;
  segments: StageSegment[];
  start: number;
}) {
  const reduceMotion = useReducedMotion();
  const duration = Math.max(end - start, 1);
  return (
    <div
      aria-label={label}
      className="relative h-10 overflow-hidden rounded-2xl bg-[var(--surface-inset)] ring-1 ring-inset ring-[var(--border)]"
      role="img"
    >
      {segments.map((segment, index) => {
        const segmentStart = new Date(segment.startAt).getTime();
        const segmentEnd = new Date(segment.endAt).getTime();
        const left = Math.max(0, ((segmentStart - start) / duration) * 100);
        const width = Math.max(0.35, ((segmentEnd - segmentStart) / duration) * 100);
        return (
          <motion.span
            animate={{ opacity: 1, scaleX: 1 }}
            className="absolute inset-y-0 rounded-md"
            initial={reduceMotion ? false : { opacity: 0.35, scaleX: 0 }}
            key={`${segment.startAt}-${index}`}
            style={{
              backgroundColor: color,
              left: `${left}%`,
              originX: 0,
              width: `${Math.min(width, 100 - left)}%`,
            }}
            transition={{
              delay: reduceMotion ? 0 : Math.min(index * 0.035, 0.5),
              duration: reduceMotion ? 0 : 0.35,
              ease: [0, 0.7, 0.5, 1],
            }}
          />
        );
      })}
    </div>
  );
}

function SleepFacts({ detail }: { detail: SleepDetail }) {
  const timingFacts = [
    ["Time to fall asleep", formatMinutes(detail.minutesToFallAsleep)],
    ["After final wake-up", formatMinutes(detail.minutesAfterWakeUp)],
    ["Stage data", detail.stages.length ? `${detail.stages.length} segments` : "Unavailable"],
    ["Source", detail.source],
  ];

  return (
    <Card variant="secondary" aria-labelledby="sleep-details-title">
      <Card.Header>
        <SectionHeader
          action={
            <Chip size="sm" variant="tertiary">
              <Chip.Label>Source-backed</Chip.Label>
            </Chip>
          }
          id="sleep-details-title"
          title="Sleep timing"
        />
      </Card.Header>
      <Card.Content>
        <dl className="grid grid-cols-4 gap-3 max-lg:grid-cols-2 max-sm:grid-cols-1">
          {timingFacts.map(([label, value]) => (
            <Metric key={label} label={label} value={value} />
          ))}
        </dl>
      </Card.Content>
      <Card.Footer>
        <Typography.Paragraph color="muted" size="xs">
          {detail.derivation} Last synced{" "}
          <time dateTime={detail.lastSyncedAt}>
            {new Date(detail.lastSyncedAt).toLocaleString()}
          </time>
          .
        </Typography.Paragraph>
      </Card.Footer>
    </Card>
  );
}

function SleepingHeartRate({
  detail,
  timezone,
}: {
  detail: SleepDetail;
  timezone: string;
}) {
  const chartData: { label: string; value: number | null; detail: string }[] = [];
  const rates = detail.heartRateSamples.map((sample) => sample.beatsPerMinute);
  const minimum = rates.length ? Math.min(...rates) : null;
  const maximum = rates.length ? Math.max(...rates) : null;
  const available =
    detail.heartRateAvailability === "available" && detail.heartRateSamples.length > 0;
  detail.heartRateSamples.forEach((sample, index) => {
    const previous = detail.heartRateSamples[index - 1];
    if (
      previous &&
      new Date(sample.observedAt).getTime() - new Date(previous.observedAt).getTime() >
        15 * 60 * 1000
    ) {
      chartData.push({
        detail: "No sample",
        label: formatTime(
          new Date(new Date(previous.observedAt).getTime() + 15 * 60 * 1000).toISOString(),
          timezone,
        ),
        value: null,
      });
    }
    chartData.push({
      detail: new Date(sample.observedAt).toLocaleString([], { timeZone: timezone }),
      label: formatTime(sample.observedAt, timezone),
      value: sample.beatsPerMinute,
    });
  });

  return (
    <Card variant="default" aria-labelledby="sleeping-heart-rate-title">
      <Card.Header>
        <SectionHeader
          action={
            <Chip size="sm" variant="soft">
              <Chip.Label>
                {detail.heartRateAvailability === "available"
                  ? detail.heartRateFreshness
                  : availabilityLabel(detail.heartRateAvailability)}
              </Chip.Label>
            </Chip>
          }
          eyebrow={`${formatTime(detail.startAt, timezone)}–${formatTime(detail.endAt, timezone)}`}
          id="sleeping-heart-rate-title"
          title="Sleeping heart rate"
        />
      </Card.Header>
      <Card.Content className="grid gap-6">
        {available ? (
          <>
            <div className="flex flex-wrap items-end gap-2">
              <span className="text-5xl font-bold tracking-[-0.04em] tabular-nums max-sm:text-4xl">
                {formatBpm(detail.averageSleepingHeartRate)}
              </span>
              <span className="pb-1 text-sm font-semibold text-muted">average</span>
            </div>
            <EvilAnimatedLineChart
              data={chartData}
              formatValue={(value) => `${Math.round(value)}`}
              reference={detail.restingHeartRate ?? undefined}
              referenceLabel={
                detail.restingHeartRate == null
                  ? undefined
                  : `Resting ${Math.round(detail.restingHeartRate)} bpm`
              }
              valueLabel="bpm"
            />
            <Typography.Paragraph color="muted" size="sm">
              {detail.heartRateSamples.length.toLocaleString()} samples ·{" "}
              {formatBpm(minimum)}–{formatBpm(maximum)}
            </Typography.Paragraph>
            <div className="grid gap-4" aria-label="Compared with resting heart rate">
              <div className="flex flex-wrap items-baseline justify-between gap-2">
                <Typography weight="semibold">Compared with resting heart rate</Typography>
                <Typography color="muted" type="body-sm">
                  {formatBpm(detail.restingHeartRate)}
                </Typography>
              </div>
              {detail.percentAboveResting == null ||
              detail.percentBelowResting == null ? (
                <Typography.Paragraph color="muted" size="sm">
                  Resting comparison unavailable.
                </Typography.Paragraph>
              ) : (
                <>
                  <ComparisonBar
                    label="Above"
                    value={detail.percentAboveResting}
                    variant="above"
                  />
                  <ComparisonBar
                    label="At or below"
                    value={detail.percentBelowResting}
                    variant="below"
                  />
                </>
              )}
            </div>
          </>
        ) : detail.heartRateAvailability === "failed" ? (
          <AppAlert
            message="The last Google Health heart-rate sync failed. The next sync will retry."
            title="Sleeping heart rate sync failed"
          />
        ) : (
          <EmptyContent
            description={heartRateEmptyDescription(detail.heartRateAvailability)}
            icon={<HeartPulse className="size-6" />}
            title={heartRateEmptyTitle(detail.heartRateAvailability)}
          />
        )}
      </Card.Content>
      <Card.Footer>
        <Typography.Paragraph color="muted" size="xs">
          {detail.heartRateSource} · {detail.heartRateDerivation}
        </Typography.Paragraph>
      </Card.Footer>
    </Card>
  );
}

function availabilityLabel(availability: SleepDetail["heartRateAvailability"]): string {
  return {
    available: "Available",
    failed: "Sync failed",
    "not-synced": "Not synced",
    "permission-missing": "Permission required",
    syncing: "Syncing",
  }[availability];
}

function heartRateEmptyTitle(
  availability: SleepDetail["heartRateAvailability"],
): string {
  if (availability === "permission-missing") return "Google Health permission required";
  if (availability === "syncing") return "Sleeping heart rate syncing";
  return "Sleeping heart rate unavailable";
}

function heartRateEmptyDescription(
  availability: SleepDetail["heartRateAvailability"],
): string {
  if (availability === "permission-missing") {
    return "Reconnect Google Health and grant health metrics access.";
  }
  if (availability === "syncing") {
    return "Heart-rate data is syncing. This panel will update when sync completes.";
  }
  return "No Google Health heart-rate samples were synced during this sleep period.";
}

function ComparisonBar({
  label,
  value,
  variant,
}: {
  label: string;
  value: number;
  variant: "above" | "below";
}) {
  return (
    <div className="grid gap-2">
      <div className="flex items-baseline justify-between gap-3">
        <Typography color="muted" type="body-sm">
          {label}
        </Typography>
        <Typography className="tabular-nums" weight="semibold">
          {formatPercent(value)}
        </Typography>
      </div>
      <div
        aria-label={`${label}: ${formatPercent(value)}`}
        aria-valuemax={100}
        aria-valuemin={0}
        aria-valuenow={Math.round(value)}
        className="h-4 overflow-hidden rounded-full bg-[var(--border)]"
        role="progressbar"
      >
        <div
          className={`h-full rounded-full ${
            variant === "above" ? "bg-[var(--sleep-rem)]" : "bg-[var(--sleep-light)]"
          }`}
          style={{ width: `${Math.min(100, Math.max(0, value))}%` }}
        />
      </div>
    </div>
  );
}

function SleepSessions({
  sessions,
  timezone,
}: {
  sessions: Dashboard["timeline"];
  timezone: string;
}) {
  return (
    <Card variant="secondary" aria-labelledby="sessions-title">
      <Card.Header>
        <SectionHeader
          action={
            <Chip size="sm" variant="soft">
              <Chip.Label>{sessions.length} sessions</Chip.Label>
            </Chip>
          }
          id="sessions-title"
          title="Sleep sessions"
        />
      </Card.Header>
      <Card.Content>
        {sessions.length ? (
          <ol className="grid">
            {sessions.map((session) => (
              <li
                className="grid grid-cols-[36px_minmax(0,1fr)_auto] items-center gap-3 border-b border-separator py-3 last:border-0 max-sm:grid-cols-[36px_minmax(0,1fr)]"
                key={session.id}
              >
                <Surface
                  className="grid size-9 place-items-center rounded-full text-lg text-accent"
                  variant="tertiary"
                  aria-hidden="true"
                >
                  <Moon className="size-5" />
                </Surface>
                <div className="min-w-0">
                  <Typography weight="semibold">{session.title}</Typography>
                  <Typography.Paragraph color="muted" size="xs">
                    {session.detail ?? "Sleep session"}
                  </Typography.Paragraph>
                </div>
                <div className="text-right max-sm:col-start-2 max-sm:text-left">
                  <time className="block text-xs tabular-nums" dateTime={session.occurredAt}>
                    {formatTime(session.occurredAt, timezone)}
                  </time>
                  <Typography color="muted" type="body-xs">
                    {session.source}
                  </Typography>
                </div>
              </li>
            ))}
          </ol>
        ) : (
          <EmptyContent
            description="No additional sessions were found for this date."
            icon={<Moon className="size-6" />}
            title="No session in timeline"
          />
        )}
      </Card.Content>
    </Card>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <Surface className="grid gap-1.5 p-4" variant="tertiary">
      <dt className="text-xs text-muted">{label}</dt>
      <dd className="text-lg font-semibold tabular-nums">{value}</dd>
    </Surface>
  );
}

function formatMinutes(value: number | null | undefined): string {
  if (value == null) return "Unavailable";
  const hours = Math.floor(value / 60);
  const minutes = Math.round(value % 60);
  if (!hours) return `${minutes}m`;
  return minutes ? `${hours}h ${minutes}m` : `${hours}h`;
}

function formatPercent(value: number | null | undefined): string {
  return value == null ? "Unavailable" : `${Math.round(value)}%`;
}

function formatBpm(value: number | null | undefined): string {
  return value == null ? "Unavailable" : `${Math.round(value)} bpm`;
}

function formatMinutesNumber(value: number): string {
  return formatMinutes(Math.round(value));
}

function formatTime(value: string, timezone: string): string {
  return new Date(value).toLocaleTimeString([], {
    hour: "numeric",
    minute: "2-digit",
    timeZone: timezone,
  });
}
