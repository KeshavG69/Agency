"use client";

import dynamic from "next/dynamic";

// The import boundary for every chart in the app. recharts is ~100kB of JS that only the
// Dashboard needs, so it loads on demand rather than in the first paint's bundle.
const load = () => import("./dashboard-chart");

// ssr:false because recharts measures the DOM to size itself. Server-rendering it produces
// a chart drawn at zero width, which then has to be thrown away on hydration — no benefit,
// and a mismatch warning for the trouble.
const ssr = false;

/**
 * Fixed height, so the placeholder occupies exactly the space the chart will and the page
 * does not jump when the bundle lands. 200px matches the components' default `height`; a
 * caller that overrides it should wrap the chart in its own sized box.
 */
function ChartSkeleton() {
  return (
    <div className="flex h-[200px] w-full items-center justify-center" aria-hidden>
      <div className="h-full w-full animate-pulse rounded-md bg-muted" />
    </div>
  );
}

export const AreaTrend = dynamic(() => load().then((m) => m.AreaTrend), {
  ssr,
  loading: ChartSkeleton,
});

export const BarStat = dynamic(() => load().then((m) => m.BarStat), {
  ssr,
  loading: ChartSkeleton,
});

export const DonutStat = dynamic(() => load().then((m) => m.DonutStat), {
  ssr,
  loading: ChartSkeleton,
});

// Type-only re-exports: they compile away, so importing a ChartConfig here does not drag
// recharts into the caller's bundle.
export type { ChartDatum, DonutDatum } from "./dashboard-chart";
export type { ChartConfig, ChartSeries } from "./chart";
