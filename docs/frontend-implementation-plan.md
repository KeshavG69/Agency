# Frontend Implementation Plan — adopting trycompai/crm's UI

**Decision (user, this session):** where Collecct's look and trycompai's look disagree,
**trycompai wins.** This applies to frontend UI only — backend decisions are separate.

**Target stack:** Next.js **16.3.0** · React **19.2.8** · **Tailwind v4 (new)** · shadcn/ui ·
TanStack Query · nuqs · recharts · Geist.
*(Next/React upgraded from 15.5.19/19.0.0 this session — see §4.2. Build verified.)*

**Research behind this:** `docs/trycompai-frontend-scan/` (5 area files, ~5,700 lines, with
`file:line` refs) and `docs/trycompai-deep-dive/05-design-system.md`.

---

## 0. The one fact that makes this cheap

Collecct's `app/globals.css` is **3,029 lines, but 91% token-driven**:

```
var(--…) uses : 566
hardcoded hex :  56   (20 of them are #fff)
rgba() literals: 18
```

So we do **not** rewrite 540 class rules. We **swap the values of the CSS variables**, and
the whole app reskins in one commit. Then Tailwind goes in alongside for new work.

⚠️ **The cascade trap.** `globals.css` currently has **zero `@layer`**. Unlayered CSS beats
`@layer utilities`, so the moment Tailwind is installed, every existing rule silently
outranks every Tailwind utility. You will write `className="p-4"` and nothing will happen.
Phase 0 fixes this first, before anything else.

---

# Phase 0 — Install Tailwind v4 without breaking the existing CSS

### 0.1 Install

```bash
cd frontend
npm i -D tailwindcss@^4 @tailwindcss/postcss
npm i clsx tailwind-merge class-variance-authority
```

### 0.2 Create `frontend/postcss.config.mjs` (does not exist yet)

```js
const config = { plugins: { "@tailwindcss/postcss": {} } };
export default config;
```

### 0.3 Restructure `app/globals.css` — the layer bridge

Tailwind v4 declares its own layers. The existing 3,029 lines must be **put into a layer
that ranks below `utilities`**, or they win every conflict.

Move the whole current file to `app/legacy.css` unchanged, then make `app/globals.css`:

```css
@import "tailwindcss";

/* The existing 3,029 lines, demoted so Tailwind utilities can override them.
   Without this they are UNLAYERED and beat every utility — you would write
   className="p-4" and see nothing happen, with no error to explain why. */
@layer legacy {
  @import "./legacy.css";
}

/* Explicit order: later layers win. `legacy` must sit below `utilities`. */
@layer theme, base, legacy, components, utilities;
```

**Verify before continuing.** Put `className="p-8 bg-red-500"` on any element. If it does
not turn red with visible padding, the layer order is wrong — stop and fix it.

### 0.4 `cn()` helper — `lib/cn.ts`

```ts
import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}
```

---

# Phase 1 — The token swap (this is what changes the look)

Replace the `:root` block at the top of `legacy.css` with trycompai's values. Because 566
rules read these variables, **the entire app restyles at once.**

### 1.1 The palette — copy verbatim

```css
:root {
  /* --- surfaces: flat white, untinted greys. No warm paper tint. --- */
  --background: #ffffff;
  --foreground: #171717;
  --card: #ffffff;
  --card-foreground: #171717;
  --popover: #ffffff;
  --popover-foreground: #171717;

  --muted: #f4f4f4;
  --muted-foreground: #6b6b6b;
  --accent: #f4f4f4;
  --accent-foreground: #171717;

  --border: #e2e2e2;
  --input: #e2e2e2;

  /* --- the ONLY two filled colours in the system --- */
  --primary: #006b4f;            /* go */
  --primary-foreground: #ffffff;
  --destructive: #ae2e24;        /* stop */
  --destructive-foreground: #ffffff;

  /* --- the one intentionally per-theme token: a ring only has to be SEEN --- */
  --ring: #006b4f;

  --overlay: rgb(0 0 0 / 0.18);

  /* --- charts: validated colourblind-safe on this surface (see §6.1) --- */
  --chart-1: #00915f;
  --chart-2: #2563eb;
  --chart-3: #b45309;
  --chart-4: #7c3aed;
  --chart-5: #0891b2;

  /* --- ONE radius, identical in both themes --- */
  --radius: 5px;

  /* --- motion --- */
  --duration-exit: 150ms;   /* fast: the old thing is already stale */
  --duration-enter: 210ms;
  --duration-move: 400ms;   /* 2.7x exit: POSITION carries continuity, opacity is a veil */
}

.dark {
  --background: #0f0f0f;
  --foreground: #f5f5f5;
  --card: #1f1f1f;
  --card-foreground: #f5f5f5;
  --popover: #1f1f1f;
  --popover-foreground: #f5f5f5;

  --muted: #292929;
  --muted-foreground: #a0a0a0;
  --accent: #292929;
  --accent-foreground: #f5f5f5;

  --border: #2a2a2a;
  --input: #2a2a2a;

  /* A brand colour that changes per theme is not one colour, it is two. */
  --primary: #006b4f;
  --destructive: #ae2e24;

  --ring: #40be96;                 /* the exception — must be visible on dark */
  --overlay: rgb(0 0 0 / 0.55);    /* the scrim MUST be heavier in dark, or it vanishes */

  --chart-1: #0fa871;
  --chart-2: #4b87f0;
  --chart-3: #c07e22;
  --chart-4: #9b7bf0;
  --chart-5: #1e93b8;
}
```

### 1.2 Map Collecct's old variable names onto the new ones

Keep the old names alive so the 540 existing rules keep working while you migrate:

```css
:root {
  /* Compatibility shims — delete each one as its rules are migrated. */
  --paper: var(--background);
  --surface: var(--card);
  --surface-2: var(--muted);
  --ink: var(--foreground);
  --faint: var(--muted-foreground);
  --line: var(--border);
  --line-strong: var(--border);
  --accent-2: var(--primary);
  --accent-soft: var(--muted);

  /* Domain signal colours have no trycompai equivalent — KEEP these, they encode
     bid/no-bid/watch meaning, not brand. Re-point them at the new neutrals. */
  --bid: var(--primary);
  --nobid: var(--muted-foreground);
  --watch: #b45309;                       /* = --chart-3, the system's amber */
}
```

> **Note the deliberate exception:** `--muted` collides — Collecct used it for *text*,
> trycompai for a *surface*. That is why the shim above maps old `--muted` → `--faint`
> semantics via `--muted-foreground`. Grep for `var(--muted)` after the swap and check each
> use is a surface, not text.

### 1.3 Expose the tokens to Tailwind — `@theme inline`

Add to `globals.css` (Tailwind v4 syntax, after the `@import`):

```css
@theme inline {
  --color-background: var(--background);
  --color-foreground: var(--foreground);
  --color-card: var(--card);
  --color-muted: var(--muted);
  --color-muted-foreground: var(--muted-foreground);
  --color-border: var(--border);
  --color-primary: var(--primary);
  --color-primary-foreground: var(--primary-foreground);
  --color-destructive: var(--destructive);
  --color-ring: var(--ring);
  --color-chart-1: var(--chart-1);
  --color-chart-2: var(--chart-2);
  --color-chart-3: var(--chart-3);
  --color-chart-4: var(--chart-4);
  --color-chart-5: var(--chart-5);

  --radius-sm: 4px;
  --radius-md: 5px;
  --radius-lg: 8px;

  --font-sans: var(--font-geist-sans);
  --font-mono: var(--font-geist-mono);
}

@layer base {
  * { border-color: var(--border); }     /* their global border default */
  body { background: var(--background); color: var(--foreground); }
}
```

### 1.4 Fonts — swap Fraunces/Hanken/JetBrains for Geist

```bash
npm i geist
```

`app/layout.tsx`:

```tsx
import { GeistSans } from "geist/font/sans";
import { GeistMono } from "geist/font/mono";

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`${GeistSans.variable} ${GeistMono.variable}`}>
      <body>{children}</body>
    </html>
  );
}
```

Then delete the `Fraunces` / `Hanken_Grotesk` / `JetBrains_Mono` imports and the
`--font-display` variable. Any rule using `var(--font-display)` becomes `var(--font-sans)`
— trycompai has no display face; headings are the same family at a larger size.

### 1.5 Two bugs to fix while you are in here

**(a) `@keyframes fade-in` is defined twice** — `globals.css:762` and `:2238`. Delete the
second. Pre-existing; unrelated to this migration but it will confuse you later.

**(b) The blanket reduced-motion rule kills selective degradation.** Currently:

```css
@media (prefers-reduced-motion: reduce) {
  * { transition-duration: 0.001ms !important; animation-duration: 0.001ms !important; }
}
```

That is a sledgehammer — it also disables things that should survive, like a status ring
that only *moves* excessively. Replace the pattern with trycompai's: gate motion behind
`no-preference` so the reduced path is the **default**, and degrade selectively.

```css
/* Keep the blanket rule as a backstop, but let specific rules opt out of it. */
@media (prefers-reduced-motion: reduce) {
  .keep-static-signal { animation-duration: revert !important; }
}
```

---

# Phase 2 — Density and shape

trycompai reads as a serious tool because it is **small and tight**. Apply these as the
defaults in `@layer base`:

| Thing | Value |
|---|---|
| Control heights | 24 / 28 / **32** / 36px (`h-6/7/8/9`); inputs `h-8` |
| Body text | `text-xs` (12px) |
| Titles | `text-sm` |
| Table header | `h-11` |
| Numbers | `tabular-nums` on **every** mutable number |
| Borders | 1px borders, **not** shadows |
| Shadows | max 12% black; `shadow-2xs` only on filled buttons, removed on `:active` |
| Radius | 4 / 5 / 8px — never a literal radius |

### 2.1 The scrollbar — copy verbatim

```css
@layer base {
  * { scrollbar-width: thin; scrollbar-color: var(--border) transparent; }

  ::-webkit-scrollbar { width: 10px; height: 10px; }
  ::-webkit-scrollbar-track { background: transparent; }
  ::-webkit-scrollbar-thumb {
    /* Track is 10px; the thumb PAINTS at 6px because of the transparent border.
       Tinting with --border means it themes in both modes for free. */
    border: 2px solid transparent;
    background-clip: padding-box;
    background-color: var(--border);
    border-radius: 9999px;
  }
  ::-webkit-scrollbar-thumb:hover { background-color: var(--muted-foreground); }
}
```

### 2.2 Sticky table header

```css
/* A sticky element's `border-bottom` does not paint reliably while scrolling.
   Draw the line as an inset shadow instead. */
.table-head-sticky {
  position: sticky;
  top: 0;
  z-index: 1;
  background: var(--background);
  box-shadow: inset 0 -1px 0 var(--border);
}
```

---

# Phase 3 — The layout shell

```tsx
// app/(app)/layout.tsx
<div className="isolate flex h-svh flex-col">        {/* h-svh, NOT h-vh: no mobile URL-bar reflow */}
  <header className="flex h-12 shrink-0 items-center gap-2 border-b px-3">…</header>
  <div className="flex min-h-0 flex-1">              {/* min-h-0 — see below */}
    <nav className="hidden w-14 shrink-0 flex-col md:flex border-r">…</nav>
    <main className="min-h-0 flex-1 overflow-y-auto px-4 md:px-6">{children}</main>
  </div>
</div>
```

**`min-h-0` is the entire trick.** A flex child defaults to `min-height: auto`, which
refuses to shrink below its content — so without it, overflow escapes to the document and
the *whole page* scrolls instead of the pane. On list pages add it at **three** levels so
the table container scrolls and the page itself stays inert:

```tsx
<main className="min-h-0 flex-1 overflow-hidden">
  <div className="flex min-h-0 flex-col gap-3">
    <FilterBar />
    <div className="min-h-0 flex-1 overflow-auto rounded-lg border bg-card">
      <table className="w-full table-fixed">…</table>   {/* table-fixed: no column jitter */}
    </div>
  </div>
</main>
```

---

# Phase 4 — Motion

### 4.1 The icon hover system — pure CSS, zero dependencies, works today

```css
@layer utilities {
  .cds-icon { transform-origin: center; }

  @media (prefers-reduced-motion: no-preference) {
    /* One overshoot spring for everything. */
    .cds-icon { transition: transform 240ms cubic-bezier(0.34, 1.56, 0.64, 1); }

    .cds-icon[data-motion="pop"]         { --cds-hover: scale(1.14); }
    .cds-icon[data-motion="scale"]       { --cds-hover: scale(1.1); }
    .cds-icon[data-motion="lift"]        { --cds-hover: translateY(-2px) scale(1.06); }
    .cds-icon[data-motion="turn"]        { --cds-hover: rotate(90deg); }
    .cds-icon[data-motion="rotate"]      { --cds-hover: rotate(-12deg); }
    .cds-icon[data-motion="flip"]        { --cds-hover: rotateY(180deg); }
    .cds-icon[data-motion="nudge-right"] { --cds-hover: translateX(3px); }
    .cds-icon[data-motion="nudge-left"]  { --cds-hover: translateX(-3px); }
    .cds-icon[data-motion="nudge-up"]    { --cds-hover: translateY(-3px); }
    .cds-icon[data-motion="nudge-down"]  { --cds-hover: translateY(3px); }
    .cds-icon[data-motion="launch"]      { --cds-hover: translate(2px, -2px); }

    /* :where() => specificity 0-0-0, so a consumer class always wins.
       Fires on ANCESTOR hover: hovering the button animates its icon. */
    :where(a, button, [role="button"], .group):hover .cds-icon {
      transform: var(--cds-hover, none);
    }
  }
}
```

Usage — assign motion by **meaning**, not decoration:

```tsx
// lib/icon-motion.ts
export const MOTION_BY_ICON: Record<string, string> = {
  ArrowRight: "nudge-right", ArrowLeft: "nudge-left",
  ChevronUp: "nudge-up",     ChevronDown: "nudge-down",
  Settings: "turn",          Refresh: "turn",
  Trash: "wiggle",           ExternalLink: "launch",
  Download: "nudge-down",    Upload: "nudge-up",
  // fallback: "pop"
};
```

### 4.2 View Transitions — ✅ UNBLOCKED (upgraded this session)

Was blocked on Next 15.5.19 / React 19.0.0. **Upgraded and verified:**

| | before | now |
|---|---|---|
| next | 15.5.19 | **16.3.0** |
| react / react-dom | 19.0.0 | **19.2.8** |

Two things were required, both done:

**(a) `react-canary.d.ts`** at the frontend root — `ViewTransition`'s *types* live in the
canary surface, not the stable one:
```ts
/// <reference types="react/canary" />
```

**(b) Nothing else.** The *runtime* comes from the React build Next vendors. Verified: the
standalone `react@19.2.8` package contains **no** `ViewTransition` export, while
`next/dist/compiled/react` does, and its react-dom already handles `view-transition-name`.
Inside a Next app, `import { ViewTransition } from "react"` resolves to Next's copy. Do not go
chasing `react@experimental`; you do not need it.

> ⚠️ **Correction (verified in Next 16.3.0):** an earlier draft of this plan told you to set
> `experimental: { viewTransition: true }`. **That key does not exist in 16.3.0** — it produces
> an `Unrecognized key(s) in object: 'viewTransition'` build warning and does nothing. The flag
> was required up to Next 16.2 (which is what trycompai pins); the feature has since graduated
> out of it. `grep -io viewtransition node_modules/next/dist/server/config-schema.js` returns
> nothing. A probe component compiled and built cleanly with an empty `next.config.mjs`.

**Verified by build**, not by assumption: a probe component using
`<ViewTransition enter="nav-lateral" exit="none" default="none">` typechecked and
`next build` completed all 11 routes.

> Next 16 rewrote `tsconfig.json` on first build (`jsx: "preserve"` → `"react-jsx"`, plus a
> `.next/dev/types` include). That is Next's own codemod and is expected — keep it.

Now port their setup:

```tsx
// components/page-transition.tsx
import { ViewTransition } from "react";

const directional = {
  "nav-forward": "nav-forward",
  "nav-back": "nav-back",
  "nav-lateral": "nav-lateral",
  default: "none",          // animation is OPT-IN: an unnamed transition renders instantly
} as const;

export function PageTransition({ children }: { children: React.ReactNode }) {
  return (
    <ViewTransition enter={directional} exit={directional} update={directional} default="none">
      {children}
    </ViewTransition>
  );
}
```

```css
/* Global reset — nothing animates unless it asks to. Enforced here AND in the React
   `default: "none"` above; a forgotten transitionType is a silent no-op, which is the
   right failure mode for motion. */
::view-transition-group(*), ::view-transition-new(*) { animation: none; }
::view-transition-old(*)                            { animation: none; opacity: 0; }

/* The pseudo tree is its own stacking context — without this, content paints over the header. */
::view-transition-group(app-header), ::view-transition-group(app-rail) { z-index: 100; }

@keyframes vt-fade  { from { opacity: 0 } to { opacity: 1 } }
@keyframes vt-rise  { from { transform: translateY(12px) } to { transform: translateY(0) } }
@keyframes vt-slide { from { transform: translateX(var(--slide-offset)) } to { transform: translateX(0) } }

@media (prefers-reduced-motion: no-preference) {
  /* Siblings imply no hierarchy: fade + a 12px rise, and deliberately NO horizontal slide. */
  ::view-transition-new(.nav-lateral) {
    animation: var(--duration-enter) ease-out both vt-fade,
               var(--duration-enter) ease-out both vt-rise;
  }

  /* Forward/back: the enter is DELAYED by exactly the exit duration, so the two never
     cross-dissolve into mud. Both slides start at t=0 to carry positional continuity. */
  ::view-transition-old(.nav-forward) {
    --slide-offset: -60px;
    animation: var(--duration-exit) ease-in both vt-fade reverse,
               var(--duration-move) ease-in-out both vt-slide reverse;
  }
  ::view-transition-new(.nav-forward) {
    --slide-offset: 60px;
    animation: var(--duration-enter) ease-out var(--duration-exit) both vt-fade,
               var(--duration-move) ease-in-out both vt-slide;
  }
}
```

**Name the persistent CHROME, never the content.** Under the `animation: none` reset, giving
an element a `view-transition-name` *excludes* it from the morph — so the header and rail sit
still while the body changes. trycompai names exactly four things and zero rows or cards:

```tsx
<header className="… [view-transition-name:app-header]">
<nav    className="… [view-transition-name:app-rail]">
<div    className="… [view-transition-name:page-header]">
```

Then trigger it from navigation:
```tsx
<Link href="/pipeline" transitionTypes={["nav-lateral"]}>Pipeline</Link>
```

> Worth knowing: trycompai defines `nav-forward` and `nav-back` fully but **never triggers
> them** — only `nav-lateral` is wired, at two call sites. Start with lateral; add the
> directional pair only if a real hierarchy appears.

---

# Phase 5 — The data layer

```bash
npm i @tanstack/react-query
```

### 5.1 The query client — this is the whole config

```ts
// lib/query-client.ts
import { defaultShouldDehydrateQuery, QueryClient } from "@tanstack/react-query";

export function makeQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: { staleTime: 30_000 },
      dehydrate: {
        // Dehydrate PENDING queries too. Without this, any server prefetch you did
        // not await is thrown away and re-fetched on the client — pure waste.
        shouldDehydrateQuery: (q) =>
          defaultShouldDehydrateQuery(q) || q.state.status === "pending",
      },
    },
  });
}

let browserQueryClient: QueryClient | undefined;
export function getQueryClient() {
  if (typeof window === "undefined") return makeQueryClient();   // fresh per request
  return (browserQueryClient ??= makeQueryClient());             // singleton in the browser
}
```

### 5.2 THE LOADING RULE — the single highest-impact change

> **`isPending`** (never loaded) may show a spinner.
> **`isFetching`** (refetching) may **never** replace content.

```tsx
const q = useQuery({
  queryKey: ["opportunities", input],
  queryFn: () => fetchOpportunityPage(input),
  placeholderData: (previous) => previous,   // keep the old rows on screen
});

const rows = useDeferredValue(q.data?.items ?? []);
// Derive colCount from the DEFERRED rows, not the live ones — otherwise you get a
// one-frame colspan mismatch when the shape changes.
const colCount = useMemo(() => columns.length, [rows]);

return (
  <Table>
    <TBody>
      {rows.length === 0
        ? <EmptyOrLoading loading={q.isFetching} colSpan={colCount} />  {/* ONLY here */}
        : rows.map(renderRow)}
    </TBody>
    <Footer>{q.isFetching && <Spinner className="size-4" />}</Footer>  {/* the only moving thing */}
  </Table>
);
```

**Collecct's current defect (`app/page.tsx`):** `setLoading(true)` injects a "Loading…"
banner **above** the rows (`:884`) and a staggered `animationDelay` (`:907`) re-runs the full
row cascade on every commit — so each keystroke visibly re-animates the list. Delete both.
Search is already debounced at 300ms (`:230`); keep that.

### 5.3 Hover + focus prefetch — ~10 lines, biggest perceived win

```ts
// lib/use-prefetch-opportunity.ts
export function usePrefetchOpportunity() {
  const qc = useQueryClient();
  return useCallback((id: string) => {
    void qc.prefetchQuery({
      queryKey: ["opportunity", id],
      queryFn: () => fetchOpportunity(id),
    });
  }, [qc]);
}
```

```tsx
<tr onMouseEnter={() => prefetch(o.id)} onFocus={() => prefetch(o.id)}>
```

**`onFocus` as well as `onMouseEnter`** — keyboard users get it too.

### 5.4 Invalidation — name what changed, not the keys

```ts
// lib/cache.ts
export function useCollecctCache() {
  const qc = useQueryClient();

  const run = (record: QueryKey[], rest: QueryKey[], settle: "all" | "record" = "all") => {
    const awaited = settle === "all" ? [...record, ...rest] : record;
    const behind  = settle === "all" ? [] : rest;
    for (const key of behind) void qc.invalidateQueries({ queryKey: key });
    return Promise.all(awaited.map((key) => qc.invalidateQueries({ queryKey: key })));
  };

  return {
    // BOTH branches invalidate the same keys. `settle` only changes what you AWAIT.
    // An inline editor passes "record" so its spinner clears as soon as that record is
    // fresh; the lists catch up behind it.
    opportunity: (id: string, settle?: "all" | "record") =>
      run([["opportunity", id]],
          [["opportunities"], ["counts"], ["dashboard"]], settle),
    contactFacts: (email: string) =>
      run([["contact-facts", email]], [["suggestions"]]),
  };
}
```

**Do not add optimistic updates.** trycompai has zero (`onMutate`/`setQueryData` = 0 hits).
Immediacy comes from prefetch plus showing the in-flight value:

```tsx
const shown = saving ? draft.trim() : value;   // optimistic DISPLAY, no cache writes, no rollback
```

---

# Phase 6 — Charts

```bash
npm i recharts@3
```

### 6.1 The palette — validated, copy verbatim

Both sets **pass all six checks** (lightness band, chroma floor, CVD separation,
normal-vision floor, contrast) — verified with the dataviz validator against `#ffffff` and
`#0f0f0f`:

| Slot | Light | Dark |
|---|---|---|
| 1 | `#00915f` | `#0fa871` |
| 2 | `#2563eb` | `#4b87f0` |
| 3 | `#b45309` | `#c07e22` |
| 4 | `#7c3aed` | `#9b7bf0` |
| 5 | `#0891b2` | `#1e93b8` |

Rules that come with them:
- **Assign in fixed order, never cycled.** A 6th series folds into "Other" — never a
  generated hue.
- **Colour follows the entity, not its rank.** Filtering out a series must not repaint the
  survivors.
- **Never a dual-axis chart.** Two measures of different scale → two charts.
- **Status colours are reserved.** `--bid`/`--nobid`/`--watch` never double as "series 4".

### 6.2 The chart container — CSS-var injection per series

The trick: a `<style>` tag emits `--color-{key}` per theme, so recharts marks reference
`var(--color-revenue)` and theme-switch for free.

```tsx
// components/chart.tsx  (distilled from shadcn's chart.tsx)
const THEMES = { light: "", dark: ".dark" } as const;

export type ChartConfig = Record<string, {
  label?: string;
  color?: string;
  theme?: Record<keyof typeof THEMES, string>;
}>;

export function ChartContainer({ config, className, children, style }: {
  config: ChartConfig; className?: string; children: React.ReactElement; style?: React.CSSProperties;
}) {
  const id = React.useId().replace(/:/g, "");
  return (
    <div
      data-chart={id}
      style={style}
      className={cn(
        "flex justify-center text-xs",
        "[&_.recharts-cartesian-axis-tick_text]:fill-muted-foreground",
        "[&_.recharts-cartesian-grid_line]:stroke-border/50",
        "[&_.recharts-surface]:outline-hidden",
        className,
      )}
    >
      <ChartStyle id={id} config={config} />
      <ResponsiveContainer>{children}</ResponsiveContainer>
    </div>
  );
}

function ChartStyle({ id, config }: { id: string; config: ChartConfig }) {
  return (
    <style dangerouslySetInnerHTML={{
      __html: Object.entries(THEMES).map(([theme, prefix]) => `
${prefix} [data-chart=${id}] {
${Object.entries(config).map(([key, c]) => {
  const color = c.theme?.[theme as keyof typeof THEMES] ?? c.color;
  return color ? `  --color-${key}: ${color};` : null;
}).filter(Boolean).join("\n")}
}`).join("\n"),
    }} />
  );
}
```

### 6.3 Code-split the chart — recharts is heavy

```tsx
// components/dashboard-charts.tsx
import dynamic from "next/dynamic";
const load = () => import("./dashboard-chart");

const loading = () => (
  // Fixed height => the spinner does not cause layout shift when the chart lands.
  <div className="flex h-[200px] items-center justify-center"><Spinner /></div>
);

export const AreaTrend = dynamic(() => load().then((m) => m.AreaTrend), { ssr: false, loading });
export const DonutStat = dynamic(() => load().then((m) => m.DonutStat), { ssr: false, loading });
```

`ssr: false` because recharts measures the DOM; rendering it on the server buys nothing and
costs a hydration mismatch.

### 6.4 A trend chart

```tsx
export function AreaTrend({ data, config, xKey, height = 200, formatX, formatValue }: Props) {
  const keys = Object.keys(config);
  const gid = React.useId().replace(/:/g, "");

  return (
    <ChartContainer config={config} className="aspect-auto w-full" style={{ height }}>
      <AreaChart data={data} margin={{ left: 0, right: 0, top: 10, bottom: 0 }}>
        <defs>
          {keys.map((key) => (
            <linearGradient key={key} id={`${gid}-${key}`} x1="0" y1="0" x2="0" y2="1">
              <stop offset="5%"  stopColor={`var(--color-${key})`} stopOpacity={0.3} />
              <stop offset="95%" stopColor={`var(--color-${key})`} stopOpacity={0} />
            </linearGradient>
          ))}
        </defs>
        <CartesianGrid vertical={false} />
        <XAxis dataKey={xKey} tickLine={false} axisLine={false} tickMargin={12} />
        <ChartTooltip cursor={false} content={<ChartTooltipContent indicator="dot" />} />
        {keys.map((key) => (
          <Area key={key} dataKey={key} type="monotone"
                stroke={`var(--color-${key})`} strokeWidth={2}
                fill={`url(#${gid}-${key})`} />
        ))}
      </AreaChart>
    </ChartContainer>
  );
}
```

Mark specs that come with the system: **2px lines**, recessive grid (`vertical={false}`,
border at 50%), no tick lines or axis lines, ticks in `muted-foreground`, ≥8px markers, and
a tooltip on **every** chart — an HTML chart is interactive by default.

**A legend is required for ≥2 series** (≤4 series should also be direct-labelled), so
identity is never carried by colour alone. A single-series chart needs no legend — the
title names it.

### 6.5 Where charts go in Collecct

The Dashboard already exists. Candidates, with the right form for each job:

| Question | Form | Why |
|---|---|---|
| Pipeline value over time | **Area trend** | change over time |
| Opportunities by stage | **Bar** | magnitude comparison across categories |
| Bid / No-Bid / Watch split | **Donut with a centre total** | part-to-whole, ≤5 slices |
| "42 open pursuits" | **Stat tile — not a chart** | a single number is not a plot |

That last row matters: a hero number in `text-2xl tabular-nums` beats a chart of one value.

---

# Phase 7 — The Agent tab

The backend for this already exists: `GET /api/intelligence/events/{subject_id}`,
`GET /api/intelligence/suggestions`, `POST /api/intelligence/facts/{id}/decide`.

### 7.1 Self-terminating poll

```ts
const WORKING_POLL_MS = 3000;

const trail = useQuery({
  queryKey: ["agent-events", opportunityId],
  queryFn: () => fetchAgentTrail(opportunityId),
  // Poll ONLY while work is outstanding; stop by returning false.
  refetchInterval: (q) => (q.state.data?.working ? WORKING_POLL_MS : false),
  refetchOnWindowFocus: false,
});
```

Our agents are Celery tasks, not streams, so `working` comes from
`GET /api/intelligence/tasks/health` (`open > 0` for that subject) rather than a session
token. Apply their **90-second staleness cutoff**: if the newest event is older than 90s and
nothing is queued, render "ended", not "working" — otherwise a crashed worker spins forever.

### 7.2 A refusal is a warning, not an error

```tsx
// Our agent_events rows carry ok:false for a No-Bid, an empty contact search,
// or a company that could not be identified.
const tone = e.ok ? "muted" : "warning";       // NOT "error"
```

> Their test comment: *"reads a refusal as a warning, because that is the interesting half."*

Render the step verb, then the reason after an em-dash:
`No-Bid — HUBZone set-aside we are ineligible for, no teaming path.`

### 7.3 Suggestions go **under the field**, not in a global inbox

This corrects an assumption in our API design. `/suggestions` stays useful as a review
queue, but the **primary** surface is inline:

```tsx
<PropertyRow label="Title">
  {fact ? (
    <span className="underline decoration-dotted" title={fact.rationale}>{fact.value}</span>
  ) : <Empty />}

  {suggestion && (
    <div className="mt-1 flex items-center gap-2 text-xs text-muted-foreground">
      <span>{suggestion.value}</span>
      <button onClick={() => decide(suggestion.id, true)}  aria-label="Accept">✓</button>
      <button onClick={() => decide(suggestion.id, false)} aria-label="Dismiss">✕</button>
    </div>
  )}
</PropertyRow>
```

An accepted fact gets a **dotted underline + hover provenance tooltip**, with their opacity
ladder: claim 100% / reasons 80% / date+host 60%. Our `rationale` string is already
generated from a fixed weighted vocabulary in `models/evidence.py` — same call they made,
so it is safe to display verbatim.

### 7.4 Six states, and never wipe the trail

loading · empty · working · ended · offline · failed. An error must **never** clear the
transcript — show the error beside the existing content.

---

# Phase 8 — URL state (nuqs)

```bash
npm i nuqs
```

Move the Pipeline filters from localStorage to the URL. localStorage state is invisible to
a server prefetch and cannot be shared; URL state gives you back/forward, deep links, and a
pasteable filtered view.

```ts
export const opportunityParsers = {
  q:        parseAsString.withDefault(""),
  status:   parseAsString.withDefault("all"),
  agency:   parseAsArrayOf(parseAsString).withDefault([]),
  naics:    parseAsArrayOf(parseAsString).withDefault([]),
  page:     parseAsInteger.withDefault(1).withOptions({ history: "push" }),
};
```

Note `history: "push"` on **page only** — paging goes in history, filter tweaks replace. And
every filter change resets `page: 1`.

---

## Order of work

| # | Phase | Risk | Payoff |
|---|---|---|---|
| 1 | **0 — Tailwind + layer bridge** | ⚠️ blocks everything if wrong | none on its own |
| 2 | **1 — Token swap + Geist** | low (91% token-driven) | **the whole app looks like theirs** |
| 3 | **5.2 — The loading rule** | low | biggest perceived-speed win |
| 4 | 2–3 — Density, scrollbar, shell | low | the "serious tool" feel |
| 5 | 5.1/5.3/5.4 — Query client, prefetch, cache | medium | removes ~8 useState + 2 useEffect |
| 6 | 4.1 — Icon motion | low | polish, zero deps |
| 7 | 6 — Charts | medium | new capability |
| 8 | 7 — Agent tab | medium | surfaces work already built |
| 9 | 8 — nuqs | medium | shareable views |
| 10 | 4.2 — View Transitions | low | ✅ unblocked — Next 16 / React 19.2.8 installed |

**Do 1 and 2 in one sitting.** Phase 0 alone leaves the app in a half-configured state, and
Phase 1 is what actually delivers the look.
