"use client";

// Adapted from EvilCharts' MIT-licensed Recharts + Motion composition patterns.
import { motion, useReducedMotion } from "motion/react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

type ChartDatum = {
  label: string;
  value: number | null;
  detail?: string;
};

type CommonChartProps = {
  data: ChartDatum[];
  emptyMessage?: string;
  formatValue: (value: number) => string;
  valueLabel: string;
};

type BarShapeProps = {
  fill?: string;
  height?: number;
  index?: number;
  width?: number;
  x?: number;
  y?: number;
};

const axisStyle = { fill: "var(--text-muted)", fontSize: 11 };

export function EvilAnimatedBarChart({
  data,
  emptyMessage,
  fill = "var(--steps)",
  formatValue,
  goal,
  valueLabel,
}: CommonChartProps & { fill?: string; goal?: number }) {
  const reduceMotion = useReducedMotion();

  return (
    <motion.div
      animate={{ opacity: 1, y: 0 }}
      className="relative h-72 w-full"
      initial={reduceMotion ? false : { opacity: 0, y: 8 }}
      transition={{ duration: 0.35, ease: [0, 0.7, 0.5, 1] }}
    >
      <ResponsiveContainer height="100%" width="100%">
        <BarChart
          accessibilityLayer
          data={data}
          margin={{ bottom: 0, left: -16, right: 4, top: 12 }}
        >
          <CartesianGrid
            stroke="var(--border)"
            strokeDasharray="3 7"
            vertical={false}
          />
          <XAxis
            axisLine={false}
            dataKey="label"
            minTickGap={14}
            tick={axisStyle}
            tickLine={false}
          />
          <YAxis
            axisLine={false}
            tick={axisStyle}
            tickFormatter={(value: number) => compactNumber(value)}
            tickLine={false}
            width={50}
          />
          <Tooltip
            content={({ active, payload, label }) => {
              if (!active || !payload?.length || payload[0]?.value == null) return null;
              return (
                <ChartTooltip
                  detail={payload[0].payload.detail}
                  label={String(label)}
                  value={`${formatValue(Number(payload[0].value))} ${valueLabel}`.trim()}
                />
              );
            }}
            cursor={{ fill: "var(--surface-raised)", opacity: 0.5 }}
          />
          {goal != null && (
            <ReferenceLine
              label={{ fill: "var(--text-muted)", fontSize: 10, value: "Goal" }}
              stroke="var(--accent)"
              strokeDasharray="5 5"
              y={goal}
            />
          )}
          <Bar
            dataKey="value"
            fill={fill}
            isAnimationActive={false}
            maxBarSize={34}
            radius={[9, 9, 3, 3]}
            shape={(props: BarShapeProps) => (
              <AnimatedBarShape
                {...props}
                fill={fill}
                reduceMotion={Boolean(reduceMotion)}
              />
            )}
          />
        </BarChart>
      </ResponsiveContainer>
      {emptyMessage && (
        <div className="pointer-events-none absolute inset-0 grid place-items-center">
          <span className="rounded-lg bg-[var(--surface-raised)]/90 px-3 py-2 text-sm font-medium text-muted">
            {emptyMessage}
          </span>
        </div>
      )}
    </motion.div>
  );
}

export function EvilAnimatedLineChart({
  data,
  emptyMessage,
  formatValue,
  reference,
  referenceLabel,
  valueLabel,
}: CommonChartProps & { reference?: number; referenceLabel?: string }) {
  const reduceMotion = useReducedMotion();

  return (
    <motion.div
      animate={{ opacity: 1, y: 0 }}
      className="relative h-72 w-full"
      initial={reduceMotion ? false : { opacity: 0, y: 8 }}
      transition={{ duration: 0.35, ease: [0, 0.7, 0.5, 1] }}
    >
      <ResponsiveContainer height="100%" width="100%">
        <LineChart
          accessibilityLayer
          data={data}
          margin={{ bottom: 0, left: -16, right: 12, top: 12 }}
        >
          <defs>
            <filter height="200%" id="sleep-line-glow" width="200%" x="-50%" y="-50%">
              <feGaussianBlur result="blur" stdDeviation="3" />
              <feMerge>
                <feMergeNode in="blur" />
                <feMergeNode in="SourceGraphic" />
              </feMerge>
            </filter>
          </defs>
          <CartesianGrid
            stroke="var(--border)"
            strokeDasharray="3 7"
            vertical={false}
          />
          <XAxis
            axisLine={false}
            dataKey="label"
            minTickGap={18}
            tick={axisStyle}
            tickLine={false}
          />
          <YAxis
            axisLine={false}
            domain={["auto", "auto"]}
            tick={axisStyle}
            tickFormatter={(value: number) => formatValue(value)}
            tickLine={false}
            width={58}
          />
          <Tooltip
            content={({ active, payload, label }) => {
              if (!active || !payload?.length || payload[0]?.value == null) return null;
              return (
                <ChartTooltip
                  detail={payload[0].payload.detail}
                  label={String(label)}
                  value={`${formatValue(Number(payload[0].value))} ${valueLabel}`.trim()}
                />
              );
            }}
            cursor={{ stroke: "var(--border-strong)", strokeDasharray: "4 4" }}
          />
          {reference != null && (
            <ReferenceLine
              label={
                referenceLabel
                  ? {
                      fill: "var(--text-muted)",
                      fontSize: 10,
                      position: "insideTopRight",
                      value: referenceLabel,
                    }
                  : undefined
              }
              stroke="var(--sleep-rem)"
              strokeDasharray="4 6"
              y={reference}
            />
          )}
          <Line
            activeDot={{
              fill: "var(--canvas)",
              r: 6,
              stroke: "var(--sleep-rem)",
              strokeWidth: 3,
            }}
            connectNulls={false}
            dataKey="value"
            dot={{ fill: "var(--sleep-rem)", r: 3, strokeWidth: 0 }}
            filter="url(#sleep-line-glow)"
            isAnimationActive={!reduceMotion}
            stroke="var(--sleep-rem)"
            strokeWidth={3}
            type="linear"
          />
        </LineChart>
      </ResponsiveContainer>
      {emptyMessage && (
        <div className="pointer-events-none absolute inset-0 grid place-items-center">
          <span className="rounded-lg bg-[var(--surface-raised)]/90 px-3 py-2 text-sm font-medium text-muted">
            {emptyMessage}
          </span>
        </div>
      )}
    </motion.div>
  );
}

function AnimatedBarShape({
  fill = "var(--steps)",
  height = 0,
  index = 0,
  reduceMotion,
  width = 0,
  x = 0,
  y = 0,
}: BarShapeProps & { reduceMotion: boolean }) {
  return (
    <motion.rect
      animate={{ height, opacity: 1, y }}
      fill={fill}
      initial={reduceMotion ? false : { height: 0, opacity: 0.45, y: y + height }}
      rx={Math.min(9, width / 2)}
      transition={{
        delay: reduceMotion ? 0 : Math.min(index * 0.045, 0.6),
        duration: reduceMotion ? 0 : 0.5,
        ease: [0, 0.7, 0.5, 1],
      }}
      width={width}
      x={x}
    />
  );
}

function ChartTooltip({
  detail,
  label,
  value,
}: {
  detail?: string;
  label: string;
  value: string;
}) {
  return (
    <div className="grid gap-1 rounded-xl border border-[var(--border-strong)] bg-[var(--surface-raised)] px-3 py-2 shadow-xl">
      <span className="text-xs text-muted">{label}</span>
      <span className="font-semibold tabular-nums">{value}</span>
      {detail && <span className="text-xs text-muted">{detail}</span>}
    </div>
  );
}

function compactNumber(value: number): string {
  if (value >= 1000) return `${(value / 1000).toFixed(value >= 10_000 ? 0 : 1)}k`;
  return String(Math.round(value));
}
