"use client";

import * as React from "react";
import {
  Legend as RechartsLegend,
  ResponsiveContainer,
  Tooltip as RechartsTooltip,
} from "recharts";
import type { LegendPayload, TooltipPayloadEntry } from "recharts";
import { cn } from "@/lib/cn";

// Chart primitives, distilled from shadcn's chart.tsx.
//
// The whole trick is ChartStyle: it emits `--color-{key}` scoped to one chart instance, so
// every recharts mark can be written as `var(--color-revenue)` — a stable name that follows
// the SERIES, not its position in the data. Two consequences worth stating outright, because
// they are the rules the palette comes with:
//   * filtering a series out never repaints the survivors (their colour is keyed by name);
//   * series order is Object.keys(config) order — fixed, never cycled. A sixth series is the
//     caller's problem: fold it into an explicit "Other" rather than generating a hue.

const THEMES = { light: "", dark: ".dark" } as const;

export type ChartTheme = keyof typeof THEMES;

export interface ChartSeries {
  label?: React.ReactNode;
  /** A CSS colour. Prefer a token — `var(--chart-2)` — over a raw hex: the tokens already
   *  carry their own light/dark values, so one declaration themes correctly in both. */
  color?: string;
  /** Only for a series that must diverge from the tokens per theme. */
  theme?: Record<ChartTheme, string>;
}

export type ChartConfig = Record<string, ChartSeries>;

const ChartContext = React.createContext<ChartConfig | null>(null);

function useChartConfig(): ChartConfig {
  const config = React.useContext(ChartContext);
  if (!config) throw new Error("Chart parts must be rendered inside <ChartContainer>.");
  return config;
}

export function ChartContainer({
  config,
  className,
  children,
  style,
}: {
  config: ChartConfig;
  className?: string;
  children: React.ReactElement;
  style?: React.CSSProperties;
}) {
  // Scopes the emitted custom properties to this instance. React's ids contain colons,
  // which are not valid in an unescaped attribute selector, hence the strip.
  const id = React.useId().replace(/:/g, "");

  return (
    <div
      data-chart={id}
      style={style}
      className={cn(
        "flex justify-center text-xs",
        // Mark specs live here rather than on every chart: ticks recede to muted, the grid
        // is a half-strength border, and recharts' default focus outline is dropped.
        "[&_.recharts-cartesian-axis-tick_text]:fill-muted-foreground",
        "[&_.recharts-cartesian-grid_line]:stroke-border/50",
        "[&_.recharts-surface]:outline-hidden",
        className,
      )}
    >
      <ChartStyle id={id} config={config} />
      <ChartContext.Provider value={config}>
        <ResponsiveContainer>{children}</ResponsiveContainer>
      </ChartContext.Provider>
    </div>
  );
}

function ChartStyle({ id, config }: { id: string; config: ChartConfig }) {
  const css = Object.entries(THEMES)
    .map(([theme, selector]) => {
      const decls = Object.entries(config)
        .map(([key, series]) => {
          const color = series.theme?.[theme as ChartTheme] ?? series.color;
          return color ? `  --color-${key}: ${color};` : null;
        })
        .filter((decl): decl is string => decl !== null)
        .join("\n");
      // A label-only config would otherwise emit an empty rule.
      return decls ? `${selector} [data-chart=${id}] {\n${decls}\n}` : null;
    })
    .filter((rule): rule is string => rule !== null)
    .join("\n");

  // The config is authored in code, never derived from user input, so there is no
  // untrusted substring here to escape.
  return <style dangerouslySetInnerHTML={{ __html: css }} />;
}

/* -------------------------------------------------------------------------------------
   Shared payload plumbing. recharts hands tooltip and legend items to a custom `content`
   element by cloning it, so both arrive with loosely-typed fields; these helpers read them
   without letting `any` escape into the components.
   ------------------------------------------------------------------------------------- */

/** The subset of a tooltip/legend item both renderers actually read. */
type PayloadLike = {
  dataKey?: unknown;
  name?: unknown;
  value?: unknown;
  payload?: unknown;
};

function stringField(source: unknown, field: string): string | undefined {
  if (typeof source !== "object" || source === null) return undefined;
  const value = (source as Record<string, unknown>)[field];
  return typeof value === "string" ? value : undefined;
}

/**
 * Which ChartConfig entry an item belongs to.
 *
 * A cartesian series carries its identity on `dataKey` ("revenue"), but a pie slice does
 * not — every slice shares dataKey "value" and keeps its identity inside its own datum.
 * `nameKey` names the datum field to read for that case (`nameKey="key"` → payload.key).
 */
function seriesKey(item: PayloadLike, nameKey?: string): string {
  if (nameKey) return stringField(item.payload, nameKey) ?? nameKey;
  return stringField(item, "dataKey") ?? stringField(item, "name") ?? "value";
}

/** Colour follows the entity: same key in, same colour out, regardless of rank or filtering. */
function seriesColor(config: ChartConfig, key: string, item: PayloadLike): string | undefined {
  if (key in config) return `var(--color-${key})`;
  // Unconfigured marks (a one-off Cell with a literal fill) keep whatever they were given.
  return stringField(item.payload, "fill") ?? stringField(item, "color");
}

function formatDefault(value: unknown): string {
  if (typeof value === "number") return value.toLocaleString();
  if (typeof value === "string") return value;
  if (value === null || value === undefined) return "";
  // A range series (`[low, high]`) is the only remaining shape recharts produces.
  return Array.isArray(value) ? value.map(formatDefault).join(" – ") : String(value);
}

/* ---------------------------------------- Tooltip ---------------------------------------- */

/** Every chart gets one: an HTML chart is interactive by default, so values are on demand. */
export const ChartTooltip = RechartsTooltip;

export interface ChartTooltipContentProps {
  /** Injected by recharts when it clones this element — never passed by hand. */
  active?: boolean;
  payload?: readonly TooltipPayloadEntry[];
  label?: React.ReactNode;
  className?: string;
  labelClassName?: string;
  indicator?: "dot" | "line" | "dashed";
  hideLabel?: boolean;
  hideIndicator?: boolean;
  /** Config key for the header label; defaults to the first item's series. */
  labelKey?: string;
  /** Datum field holding each item's series name — see seriesKey(). */
  nameKey?: string;
  labelFormatter?: (label: React.ReactNode) => React.ReactNode;
  valueFormatter?: (value: number) => string;
}

export function ChartTooltipContent({
  active,
  payload,
  label,
  className,
  labelClassName,
  indicator = "dot",
  hideLabel = false,
  hideIndicator = false,
  labelKey,
  nameKey,
  labelFormatter,
  valueFormatter,
}: ChartTooltipContentProps) {
  const config = useChartConfig();

  if (!active || !payload?.length) return null;

  const first = payload[0];
  const headerKey = labelKey ?? seriesKey(first, nameKey);
  const rawLabel =
    typeof label === "string" && label in config ? config[label].label ?? label : label;
  const header = labelFormatter
    ? labelFormatter(rawLabel)
    : rawLabel ?? config[headerKey]?.label;

  return (
    <div
      className={cn(
        "grid min-w-[9rem] items-start gap-1.5 rounded-lg border bg-popover px-2.5 py-1.5",
        "text-xs shadow-md",
        className,
      )}
    >
      {!hideLabel && header ? (
        <div className={cn("font-medium text-foreground", labelClassName)}>{header}</div>
      ) : null}
      <div className="grid gap-1.5">
        {payload.map((item, index) => {
          const key = seriesKey(item, nameKey);
          const color = seriesColor(config, key, item);
          const name = config[key]?.label ?? stringField(item, "name") ?? key;
          const value =
            valueFormatter && typeof item.value === "number"
              ? valueFormatter(item.value)
              : formatDefault(item.value);

          return (
            <div
              key={`${key}-${index}`}
              className="flex w-full items-center gap-2 text-muted-foreground"
            >
              {!hideIndicator ? <Indicator variant={indicator} color={color} /> : null}
              <span className="flex-1 truncate">{name}</span>
              <span className="font-mono font-medium tabular-nums text-foreground">{value}</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function Indicator({
  variant,
  color,
}: {
  variant: "dot" | "line" | "dashed";
  color: string | undefined;
}) {
  if (variant === "dashed") {
    return (
      <span
        className="h-2.5 w-0 shrink-0 border-[1.5px] border-dashed"
        style={{ borderColor: color }}
      />
    );
  }
  return (
    <span
      // 10px on the long edge — at or above the 8px minimum a marker needs to stay legible.
      className={cn("shrink-0 rounded-[2px]", variant === "dot" ? "h-2.5 w-2.5" : "h-2.5 w-1")}
      style={{ backgroundColor: color }}
    />
  );
}

/* ---------------------------------------- Legend ---------------------------------------- */

/** Required at two or more series: identity must never be carried by colour alone. */
export const ChartLegend = RechartsLegend;

export interface ChartLegendContentProps {
  /** Injected by recharts when it clones this element. */
  payload?: readonly LegendPayload[];
  className?: string;
  verticalAlign?: "top" | "bottom" | "middle";
  nameKey?: string;
}

export function ChartLegendContent({
  payload,
  className,
  verticalAlign = "bottom",
  nameKey,
}: ChartLegendContentProps) {
  const config = useChartConfig();

  if (!payload?.length) return null;

  return (
    <div
      className={cn(
        "flex flex-wrap items-center justify-center gap-x-4 gap-y-1.5 text-xs",
        verticalAlign === "top" ? "pb-3" : "pt-3",
        className,
      )}
    >
      {payload.map((item, index) => {
        const key = seriesKey(item, nameKey);
        return (
          <div key={`${key}-${index}`} className="flex items-center gap-1.5 text-muted-foreground">
            <span
              className="h-2 w-2 shrink-0 rounded-[2px]"
              style={{ backgroundColor: seriesColor(config, key, item) }}
            />
            {config[key]?.label ?? item.value ?? key}
          </div>
        );
      })}
    </div>
  );
}
