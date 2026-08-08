"use client";

import * as React from "react";
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Label,
  Pie,
  PieChart,
  XAxis,
  YAxis,
} from "recharts";
import {
  ChartContainer,
  ChartLegend,
  ChartLegendContent,
  ChartTooltip,
  ChartTooltipContent,
  type ChartConfig,
} from "@/components/chart";
import { cn } from "@/lib/cn";

// The three chart forms Collecct needs, and only those — the form is chosen by the question:
//   change over time            → AreaTrend
//   magnitude across categories → BarStat
//   part-to-whole, ≤5 slices    → DonutStat
// A single number is not a plot: render "42 open pursuits" as a stat tile instead.
//
// Two rules are enforced by construction here rather than left to the caller: no chart takes
// a second axis (two measures of different scale are two charts), and a legend appears as
// soon as there are two series to tell apart.
//
// Import these through dashboard-charts.tsx, not directly — recharts is heavy and belongs
// behind a dynamic() boundary.

/** A row of chart data. Values stay primitive; recharts reads them by key. */
export type ChartDatum = Record<string, string | number | null | undefined>;

interface CartesianProps {
  data: readonly ChartDatum[];
  /** Series, in the order they should be coloured. Keys must match the data's field names. */
  config: ChartConfig;
  /** Field holding the category / time axis. */
  xKey: string;
  height?: number;
  className?: string;
  formatX?: (value: string) => string;
  formatValue?: (value: number) => string;
}

/** Tick labels arrive from recharts untyped; normalise before handing them to the caller. */
function tickFormatter(format?: (value: string) => string) {
  return format ? (value: unknown) => format(String(value)) : undefined;
}

export function AreaTrend({
  data,
  config,
  xKey,
  height = 200,
  className,
  formatX,
  formatValue,
}: CartesianProps) {
  const keys = Object.keys(config);
  // One gradient per series, namespaced per instance — two AreaTrends on the same page
  // would otherwise share (and fight over) the same <defs> ids.
  const gid = React.useId().replace(/:/g, "");

  return (
    <ChartContainer
      config={config}
      className={cn("aspect-auto w-full", className)}
      style={{ height }}
    >
      {/* 12px of horizontal margin, not the 0 a full-bleed area wants: the first and last
          x ticks sit directly under the end points, and recharts drops any tick whose label
          would overflow the plot — at margin 0 the "Jan" end of the axis silently vanishes. */}
      <AreaChart data={data} margin={{ left: 12, right: 12, top: 10, bottom: 0 }}>
        <defs>
          {keys.map((key) => (
            <linearGradient key={key} id={`${gid}-${key}`} x1="0" y1="0" x2="0" y2="1">
              <stop offset="5%" stopColor={`var(--color-${key})`} stopOpacity={0.3} />
              <stop offset="95%" stopColor={`var(--color-${key})`} stopOpacity={0} />
            </linearGradient>
          ))}
        </defs>
        <CartesianGrid vertical={false} />
        <XAxis
          dataKey={xKey}
          tickLine={false}
          axisLine={false}
          tickMargin={12}
          tickFormatter={tickFormatter(formatX)}
        />
        <ChartTooltip
          cursor={false}
          content={<ChartTooltipContent indicator="dot" valueFormatter={formatValue} />}
        />
        {keys.length > 1 ? <ChartLegend content={<ChartLegendContent />} /> : null}
        {keys.map((key) => (
          <Area
            key={key}
            dataKey={key}
            type="monotone"
            stroke={`var(--color-${key})`}
            strokeWidth={2}
            fill={`url(#${gid}-${key})`}
          />
        ))}
      </AreaChart>
    </ChartContainer>
  );
}

export function BarStat({
  data,
  config,
  xKey,
  height = 200,
  className,
  formatX,
  formatValue,
}: CartesianProps) {
  const keys = Object.keys(config);

  return (
    <ChartContainer
      config={config}
      className={cn("aspect-auto w-full", className)}
      style={{ height }}
    >
      <BarChart data={data} margin={{ left: 0, right: 0, top: 10, bottom: 0 }}>
        <CartesianGrid vertical={false} />
        <XAxis
          dataKey={xKey}
          tickLine={false}
          axisLine={false}
          tickMargin={12}
          tickFormatter={tickFormatter(formatX)}
        />
        {/* Unlike the trend, a magnitude comparison is read off the scale, so this one axis
            earns its keep — still no tick or axis lines. */}
        <YAxis
          tickLine={false}
          axisLine={false}
          width={48}
          tickMargin={8}
          tickFormatter={tickFormatter(formatValue ? (v) => formatValue(Number(v)) : undefined)}
        />
        <ChartTooltip
          // A hairline cursor band, not recharts' default slab, which reads as a selection.
          cursor={{ fill: "var(--muted-surface)", fillOpacity: 0.6 }}
          content={<ChartTooltipContent indicator="dot" valueFormatter={formatValue} />}
        />
        {keys.length > 1 ? <ChartLegend content={<ChartLegendContent />} /> : null}
        {keys.map((key) => (
          <Bar key={key} dataKey={key} fill={`var(--color-${key})`} radius={[4, 4, 0, 0]} />
        ))}
      </BarChart>
    </ChartContainer>
  );
}

export interface DonutDatum {
  /** Must match a ChartConfig key — that is what pins the slice to its colour. */
  key: string;
  value: number;
}

export function DonutStat({
  data,
  config,
  total,
  totalLabel,
  height = 200,
  className,
  formatValue,
  formatTotal,
  onSelect,
  activeKey,
}: {
  data: readonly DonutDatum[];
  config: ChartConfig;
  /** Centre figure. Defaults to the sum, which is what part-to-whole normally wants. */
  total?: number;
  totalLabel?: string;
  height?: number;
  className?: string;
  formatValue?: (value: number) => string;
  formatTotal?: (value: number) => string;
  /** Clicking a slice calls this with its key; the caller decides what "selected" means. */
  onSelect?: (key: string) => void;
  /** When set, that slice stays solid and the rest dim, so the ring shows the active filter. */
  activeKey?: string | null;
}) {
  const sum = total ?? data.reduce((n, d) => n + d.value, 0);
  const centre = (formatTotal ?? formatValue ?? ((v: number) => v.toLocaleString()))(sum);

  const fmt = formatValue ?? ((v: number) => v.toLocaleString());
  return (
    <div className={cn("w-full", className)}>
      <ChartContainer config={config} className="aspect-auto w-full" style={{ height }}>
        <PieChart>
          <ChartTooltip
            cursor={false}
            // Slices carry their identity in the datum, not on dataKey; hideLabel because the
            // slice name is already the row name and would otherwise print twice.
            content={<ChartTooltipContent nameKey="key" hideLabel valueFormatter={formatValue} />}
          />
          <Pie
            data={data}
            dataKey="value"
            nameKey="key"
            innerRadius="64%"
            outerRadius="90%"
            // Rounded segment ends + a small angular gap read as a modern, premium donut.
            cornerRadius={5}
            paddingAngle={2}
            // recharts 3's pie entrance animation stalls at its first frame here, leaving the
            // ring empty (verified in the browser: the sector paths never advance past t≈0).
            // Bar and Area animate correctly, so this is disabled for the pie alone.
            isAnimationActive={false}
            // Separator in the surface colour, so slices read as distinct in both themes.
            stroke="var(--card)"
            strokeWidth={2}
            // Index, not payload: recharts' click datum is wrapped and its shape shifts between
            // versions, but the index into `data` is stable.
            onClick={onSelect ? (_, index) => onSelect(data[index]?.key) : undefined}
            style={onSelect ? { cursor: "pointer", outline: "none" } : undefined}
          >
            {data.map((d) => (
              <Cell
                key={d.key}
                fill={`var(--color-${d.key})`}
                // When a slice is active, the others fade so the ring reads as a filter.
                fillOpacity={activeKey && d.key !== activeKey ? 0.24 : 1}
              />
            ))}
            <Label
              content={({ viewBox }) => {
                // Only a polar viewBox has a centre; anything else means it has not measured yet.
                if (!viewBox || !("cx" in viewBox)) return null;
                return (
                  <text x={viewBox.cx} y={viewBox.cy} textAnchor="middle" dominantBaseline="middle">
                    <tspan
                      x={viewBox.cx}
                      y={viewBox.cy - 2}
                      className="fill-foreground text-[30px] font-semibold tracking-tight tabular-nums"
                    >
                      {centre}
                    </tspan>
                    {totalLabel ? (
                      <tspan
                        x={viewBox.cx}
                        y={viewBox.cy + 20}
                        className="fill-muted-foreground text-[11px] uppercase tracking-wider"
                      >
                        {totalLabel}
                      </tspan>
                    ) : null}
                  </text>
                );
              }}
            />
          </Pie>
        </PieChart>
      </ChartContainer>
      {data.length > 1 && (
        <div className="donut-legend">
          {data.map((d) => {
            const dim = activeKey && d.key !== activeKey;
            const label = (config[d.key] as { label?: string } | undefined)?.label ?? d.key;
            return (
              <button
                type="button"
                key={d.key}
                className={cn("dl-item", dim && "is-dim", !onSelect && "dl-static")}
                onClick={onSelect ? () => onSelect(d.key) : undefined}
                disabled={!onSelect}
              >
                <span className="dl-dot" style={{ background: `var(--color-${d.key})` }} />
                <span className="dl-label">{label}</span>
                <span className="dl-val">{fmt(d.value)}</span>
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
