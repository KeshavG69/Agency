# 04 — Layout Shell & CSS Architecture

Source clone root (all paths below are relative to it):
`/private/tmp/claude-501/-Users-keshav-Developer-Others-AI-Agency/ad094025-ce2f-4d46-9f0e-7189842d9f45/scratchpad/crm`

Stack of the reference app (matters for portability):
`next 16.2.12`, `react 19.2.4`, `tailwindcss ^4`, `@tailwindcss/postcss ^4`, `radix-ui ^1.6.0`,
`nuqs ^2.8.9`, `vaul ^1.1.2`, shadcn style `radix-nova`
(`apps/app/package.json:14-40`, `packages/ui/package.json:19-60`, `apps/app/components.json:3`).

---

## 1. The shell — exact geometry and who scrolls

### 1.1 The three-level frame

**Level 0 — document.** `apps/app/app/layout.tsx:43-48`

```tsx
<html
    lang="en"
    suppressHydrationWarning
    className={cn(fontSans.variable, fontMono.variable, "h-full antialiased")}
>
    <body className="flex min-h-full flex-col font-sans">
```

`html.h-full` + `body.min-h-full` means the body is a full-viewport flex column but is
allowed to grow — the auth pages (`min-h-svh`) rely on that. The app group then *clamps* it.

**Level 1 — the app frame.** `apps/app/app/(app)/layout.tsx:16-38` (complete):

```tsx
<MobileNavProvider>
    <div className="isolate flex h-svh flex-col">
        <HydrateClient>
            <AppHeader user={{ name: user.name, email: user.email, image: user.image ?? null }} />
        </HydrateClient>
        <div className="flex min-h-0 flex-1">
            <AppIconRail />
            {children}
        </div>

        <RecordSheetHost />

        <QuickSwitcher />
    </div>
</MobileNavProvider>
```

Three things are doing all the work here:

- `h-svh` — **small viewport height**, not `h-screen`/`100vh`. On mobile Safari/Chrome the
  URL bar collapse changes `100vh` mid-scroll and the whole frame jumps; `svh` is the
  *smallest* stable height, so the frame never resizes when browser chrome animates.
- `isolate` — creates a stacking context on the frame so the `z-50` sheet/overlay and
  `z-100` view-transition groups can never be beaten by page content.
- `min-h-0` on the body row — the single most important class in the whole file. A flex
  child defaults to `min-height:auto`, so `flex-1` alone would let the row grow to its
  content height and push the frame past `100svh`, making `<body>` scroll. `min-h-0`
  forces the row to be exactly `100svh − 48px` and hands overflow to the descendants.

**Level 2 — the panes.**

| Pane | Class | Size |
|---|---|---|
| Header | `apps/app/components/app-header.tsx:62` `flex h-12 shrink-0 items-center gap-2 border-b px-3` | **48 px**, never shrinks |
| Icon rail | `apps/app/components/app-icon-rail.tsx:122` `hidden w-14 shrink-0 flex-col items-center gap-1 border-r py-3 md:flex` | **56 px**, ≥768 px only |
| Settings sidebar | `apps/app/app/(app)/settings/settings-sidebar.tsx:62` `hidden w-56 shrink-0 border-r md:block` | **224 px**, ≥768 px only |
| Page area | `apps/app/components/page-shell.tsx:8-11` | fills the rest |

Verbatim header (`apps/app/components/app-header.tsx:62`):

```tsx
<header className="flex h-12 shrink-0 items-center gap-2 border-b px-3 [view-transition-name:app-header]">
```

Verbatim rail (`apps/app/components/app-icon-rail.tsx:120-122`):

```tsx
<nav
    aria-label="Primary"
    className="hidden w-14 shrink-0 flex-col items-center gap-1 border-r py-3 md:flex [view-transition-name:app-rail]"
>
```

Both are `shrink-0` **and** fixed-dimension, so no amount of content can resize them.
Neither is `position: fixed` — they are ordinary flex items, which is why there is no
`padding-top`/`padding-left` compensation anywhere in the app and no scroll-under bugs.

### 1.2 The one scrolling pane

`apps/app/components/page-shell.tsx:5-23`:

```tsx
function PageShell({ className, ...props }: React.ComponentProps<"div">) {
    return (
        <PageTransition>
            <main
                data-slot="page-shell-scroll"
                className="flex min-w-0 flex-1 flex-col overflow-y-auto px-4 pt-4 pb-4 md:px-6 md:pt-6 md:pb-6"
            >
                <div
                    data-slot="page-shell"
                    className={cn(
                        "mx-auto flex w-full min-w-0 max-w-7xl flex-1 flex-col gap-6",
                        className,
                    )}
                    {...props}
                />
            </main>
        </PageTransition>
    );
}
```

- `overflow-y-auto` lives **only** here. The document itself never scrolls in the app group.
- `min-w-0` on both the `<main>` and the inner div: without it a wide table or a long
  unbroken string would blow the flex row out horizontally and shove the rail off-screen.
- Padding is on the scroll container (`px-4 pt-4 pb-4 md:px-6 md:pt-6 md:pb-6`), so the
  scrollbar sits at the true right edge of the pane, not inset by the gutter.
- `max-w-7xl` (1280 px) + `mx-auto` caps line length on ultrawide.

### 1.3 The second scroller: the table body

List pages opt into a *non-scrolling* page and a scrolling table instead. The chain of
`min-h-0` must be unbroken end to end, and it is — `apps/app/app/(app)/contacts/page.tsx:43,54`:

```tsx
<PageShell className="min-h-0">
    ...
    <PageShellContent className="min-h-0">
        <HydrateClient>
            <ContactsTable />
        </HydrateClient>
    </PageShellContent>
</PageShell>
```

then `packages/ui/src/components/data-table.tsx:215`:

```tsx
<div className={cn("flex min-h-0 flex-1 flex-col gap-3", className)}>
```

and finally `packages/ui/src/components/data-table.tsx:423-429`:

```tsx
<Table
    className={cn(
        "table-fixed [&_td:first-child]:pl-4 [&_th:first-child]:pl-4 [&_td:last-child]:pr-4 [&_th:last-child]:pr-4",
        tableClassName,
    )}
    containerClassName="min-h-0 flex-1 overflow-auto rounded-lg border bg-card"
>
```

Result: filter bar and pagination stay pinned, only the rows scroll, and the page as a
whole is inert. Identical pattern on `deals/page.tsx:43,56`, `companies/page.tsx:40,53`,
`settings/members/page.tsx:41,51`, `settings/sso/page.tsx:43,60`.

The overview page (`apps/app/app/(app)/page.tsx:35`) deliberately omits `min-h-0` — it is a
long dashboard and *should* scroll in the `<main>`.

### 1.4 Sticky header without a border

`packages/ui/src/components/data-table.tsx:430`:

```tsx
<TableHeader className="sticky top-0 z-10 bg-muted [&_th]:bg-muted [&_tr]:border-0 [&_tr]:shadow-[inset_0_-1px_0_var(--border)]">
```

A `border-bottom` on a `position: sticky` `<tr>`/`<thead>` is not painted consistently
across browsers (border-collapse + sticky). They kill the border and draw an **inset box
shadow** instead — same 1 px line, always painted, and no extra layout box, so the header
row height is exactly `h-11` (44 px, `data-table.tsx:433,443`) with zero shift.
`SimpleTable` does the same for its non-panel variant (`packages/ui/src/components/simple-table.tsx:71-73`).

### 1.5 Nested layout

`apps/app/app/(app)/settings/layout.tsx:8-13` — the only nested layout; it re-runs the same
pattern one level down (column on mobile, row on desktop, `min-h-0 min-w-0` preserved):

```tsx
<div className="flex min-h-0 min-w-0 flex-1 flex-col md:flex-row">
    <SettingsSidebar />
    {children}
</div>
```

---

## 2. Token architecture

Single stylesheet: `packages/ui/src/styles/globals.css`, **887 lines**, exported as
`@crm/ui/globals.css` (`packages/ui/package.json:8`) and imported once at
`apps/app/app/layout.tsx:1`.

### 2.1 The head of the file (`globals.css:1-7`)

```css
@import "tailwindcss";
@import "tw-animate-css";
@import "shadcn/tailwind.css";

@source "../../**/*.{ts,tsx}";

@custom-variant dark (&:is(.dark *));
```

- Tailwind **v4**, no `tailwind.config.*` anywhere in the repo — confirmed:
  `apps/app/components.json:6` and `packages/ui/components.json:6` both have `"config": ""`.
- PostCSS is a one-plugin passthrough — `packages/ui/postcss.config.mjs:1-7`:
  ```js
  const config = { plugins: { "@tailwindcss/postcss": {} } };
  export default config;
  ```
  and the app just re-exports it: `apps/app/postcss.config.mjs:1`
  `export { default } from "@crm/ui/postcss.config";`
- `@source` is the monorepo fix: the app's content scan is rooted at the app, so the UI
  package's `ts/tsx` would be invisible; this line adds it.
- `@custom-variant dark (&:is(.dark *))` = class-based dark mode driven by `next-themes`,
  not `prefers-color-scheme`.
- Three utility families (`scrollbar-thin`, `scrollbar-gutter-stable`, `scroll-fade-b`,
  `scroll-fade-x`, `scrollbar-none`) come from the third-party `shadcn/tailwind.css`
  import, **not** from this file — see §4.3.

### 2.2 Two-tier tokens: raw vars → `@theme inline`

Tier 1, raw values, `:root` (`globals.css:9-74`) and `.dark` (`globals.css:76-139`).
Tier 2, `@theme inline` (`globals.css:141-207`) maps each raw var to a Tailwind namespace
so utilities are generated. The `inline` keyword is load-bearing: it makes Tailwind emit
`var(--background)` into the utility rather than snapshotting the value, so `.dark`
re-assignment flows through without regenerating classes.

Colours are covered by another agent. Everything else, verbatim:

**Typography** (`globals.css:55-56`, re-exported at `188-189, 203`):

```css
--font-sans: var(--font-geist-sans, "Geist", sans-serif);
--font-mono: var(--font-geist-mono, "Geist Mono", monospace);
```
```css
--font-sans: var(--font-sans);
--font-mono: var(--font-mono);
...
--font-heading: var(--font-sans);
```

`--font-geist-*` is injected by `next/font` (`apps/app/app/layout.tsx:11-19,46`). The
fallback chain inside the var means the stylesheet still renders standalone.
`--font-heading` is an alias to sans — one typeface for the whole product; it is consumed
by exactly one component (`packages/ui/src/components/sheet.tsx:129`
`cva("font-heading font-medium text-foreground", …)`).

There is **no type scale in the theme** — no `--text-*` overrides. The app rides Tailwind's
default scale and simply never uses the big end of it (see §3.2).

`--tracking-normal: 0em;` (`globals.css:72`) is declared but never mapped into `@theme` and
never referenced — dead token.

**Radius** (`globals.css:57`, `190-193`, `204-206`):

```css
--radius: 5px;
```
```css
--radius-sm: 4px;
--radius-md: var(--radius);
--radius-lg: 8px;
--radius-xl: 12px;
...
--radius-2xl: calc(var(--radius) * 1.8);
--radius-3xl: calc(var(--radius) * 2.2);
--radius-4xl: calc(var(--radius) * 2.6);
```

So: sm 4 · md **5** · lg 8 · xl 12 · 2xl 9 · 3xl 11 · 4xl 13 px. Note the scale is
**non-monotonic** — `2xl` (9) and `3xl` (11) are *smaller* than `xl` (12), because the
first four are hardcoded and the last three are derived from `--radius`. In practice only
sm/md/lg are used, and `docs/design.md:7-10` makes that a rule:

> Corners are rounded, from the scale only: `rounded-sm` (4px) for the smallest
> controls, `rounded-md` (5px) for buttons, inputs and segments, `rounded-lg`
> (8px) for surfaces that contain controls — popovers, dialogs, menus, table
> shells. Never a literal radius at the call site.

**Spacing** (`globals.css:73`):

```css
--spacing: 0.25rem;
```

Declared in `:root` but **not** re-exported in `@theme inline`, so it does not actually
override Tailwind's spacing base — it happens to equal the v4 default (4 px), so the
omission is invisible. `p-4` = 16 px, `gap-6` = 24 px, etc. One place consumes it as a
function: `packages/ui/src/components/tabs.tsx:65`
`group-data-vertical/tabs:py-[calc(--spacing(1.25))]`.

**Shadow** (`globals.css:58-71`, mapped `195-202`):

```css
--shadow-x: 0px;
--shadow-y: 1px;
--shadow-blur: 2px;
--shadow-spread: 0px;
--shadow-opacity: 0.08;
--shadow-color: hsl(0 0% 0%);
--shadow-2xs: 0px 1px 2px 0px hsl(0 0% 0% / 0.04);
--shadow-xs: 0px 1px 2px 0px hsl(0 0% 0% / 0.04);
--shadow-sm: 0px 1px 2px 0px hsl(0 0% 0% / 0.06);
--shadow: 0px 1px 2px 0px hsl(0 0% 0% / 0.06);
--shadow-md: 0px 2px 4px -1px hsl(0 0% 0% / 0.08);
--shadow-lg: 0px 4px 8px -2px hsl(0 0% 0% / 0.08);
--shadow-xl: 0px 8px 16px -4px hsl(0 0% 0% / 0.08);
--shadow-2xl: 0px 16px 32px -8px hsl(0 0% 0% / 0.12);
```

Two observations. (a) The whole scale tops out at **12 % black** — this is a border-first
design (§3.4), shadows only ever hint. (b) The six decomposed `--shadow-x/y/blur/spread/
opacity/color` vars are **not used by the eight composed values** — they are inert
leftovers from the shadcn theme generator. Identical block is duplicated verbatim in
`.dark` (`globals.css:125-138`), i.e. shadows do not darken in dark mode.

**Motion** — a second, separate `:root` block far down the file (`globals.css:399-403`):

```css
:root {
    --duration-exit: 150ms;
    --duration-enter: 210ms;
    --duration-move: 400ms;
}
```

Not in `@theme`; consumed only by the `::view-transition-*` rules.

### 2.3 Layers actually used

Only four constructs, in this order:

1. `@layer base` (`globals.css:209-240`) — reset + scrollbars.
2. `@layer utilities` ×2 (`globals.css:242-254` bloom, `256-334` icon motion).
3. `@utility alert-attention` (`globals.css:877-881`) — v4's custom-utility API.
4. **Unlayered** CSS for everything else: `@keyframes` (336-397, 801-837, 849-875),
   `::view-transition-*` (434-481), and the entire `.link-hover*` system (483-799).

`@layer base` content, verbatim (`globals.css:209-215`):

```css
@layer base {
    * {
        @apply border-border outline-ring/50;
    }
    body {
        @apply bg-background text-foreground font-sans;
    }
```

`* { border-color }` is what lets every component write bare `border`, `border-b`,
`border-r` with no colour — see `app-header.tsx:62`, `app-icon-rail.tsx:122`,
`dashboard.tsx:131`. This is a global-selector dependency worth flagging for porting (§8).

---

## 3. The density system

The app is deliberately **one notch denser than stock shadcn**: base UI text is `text-xs`
(12 px), not `text-sm`.

### 3.1 Control heights (the 6/7/8/9 ladder)

`packages/ui/src/components/button.tsx:25-35`, verbatim:

```tsx
size: {
    default:
        "h-8 gap-1.5 px-2.5 has-data-[icon=inline-end]:pr-2 has-data-[icon=inline-start]:pl-2",
    xs: "h-6 gap-1 rounded-sm px-2 text-xs has-data-[icon=inline-end]:pr-1.5 has-data-[icon=inline-start]:pl-1.5 [&_svg:not([class*='size-'])]:size-3",
    sm: "h-7 gap-1 px-2.5 has-data-[icon=inline-end]:pr-1.5 has-data-[icon=inline-start]:pl-1.5 [&_svg:not([class*='size-'])]:size-3.5",
    lg: "h-9 gap-1.5 px-2.5 has-data-[icon=inline-end]:pr-2 has-data-[icon=inline-start]:pl-2",
    icon: "size-8",
    "icon-xs": "size-6 rounded-sm [&_svg:not([class*='size-'])]:size-3",
    "icon-sm": "size-7",
    "icon-lg": "size-9",
},
```

24 / 28 / 32 / 36 px, with the icon square always matching the text variant's height, and
the glyph shrinking in lockstep (12 / 14 / 16 px via `[&_svg:not([class*='size-'])]:size-*`
— the `:not` guard means a call site can still override with an explicit `size-*`).

Matching:

- Input — `packages/ui/src/components/input.tsx:10`: `h-8 w-full min-w-0 rounded-md border border-input bg-background px-2.5 py-1 text-xs …`
- Select trigger — `packages/ui/src/components/select.tsx:35`: `… data-[size=default]:h-8 data-[size=sm]:h-7 …`
- Tabs list — `packages/ui/src/components/tabs.tsx:27`: `… group-data-horizontal/tabs:h-8 …`
- Table head — `packages/ui/src/components/table.tsx:76`: `h-9 px-2 …`, overridden to `h-11` in the data table (`data-table.tsx:433,443`)
- Table cell — `packages/ui/src/components/table.tsx:89`: `px-2 py-2.5 align-middle whitespace-nowrap …` (data table bumps to `px-3 py-3`, sub-rows `py-2.5`, `data-table.tsx:541,578`)
- Sheet section gutter — `apps/app/components/detail-sheet.tsx:39`: `const GUTTER = "px-5";` — one constant, applied to header, stats, tab list and every section, so all sheet content aligns on a single 20 px rail.

### 3.2 Type scale actually in use

| Role | Class | Source |
|---|---|---|
| Page title | `text-2xl md:text-3xl` + `font-medium tracking-tight` | `page-shell.tsx:64` |
| Page description | `text-sm` + `text-muted-foreground` | `page-shell.tsx:80` |
| Sheet title | `text-lg leading-tight tracking-tight` (size `lg`) / `text-sm` (default) | `sheet.tsx:129-139` |
| Card / section heading | `text-sm font-medium` | `card.tsx:31`, `dashboard.tsx:93` |
| **Body / controls / tables** | `text-xs` | `button.tsx:7`, `input.tsx:10`, `table.tsx:18` |
| Sheet prose | `text-xs/relaxed` | `sheet.tsx:11,163`, `tabs.tsx:83` |
| Sheet property rows | `text-xs/5` (12 px on a 20 px line) | `detail-sheet.tsx:343,346,355` |
| Section label | `font-medium text-muted-foreground text-xs uppercase tracking-wider` | `detail-sheet.tsx:41-42` |
| Stat value | `font-medium text-3xl tracking-tight tabular-nums` | `stat-card.tsx:84` |
| Logo fallback initials | `text-[10px]` … `data-[size=xs]:text-[8px]` | `entity-logo.tsx:55-56` |

Two shared string constants keep sheet metadata identical across all three record types
(`apps/app/components/detail-sheet.tsx:41-48`):

```tsx
export const SECTION_TITLE =
    "font-medium text-muted-foreground text-xs uppercase tracking-wider";

export const PROPERTY_ROW = "grid grid-cols-[6.5rem_minmax(0,1fr)] gap-2";

export const PROPERTY_LABEL = "truncate text-muted-foreground text-xs";

const PROPERTY_CELL = "border border-transparent py-1";
```

`PROPERTY_ROW` is the label/value grid — a **fixed 6.5 rem label column** (104 px) and a
`minmax(0,1fr)` value column. The fixed column is why every property row in every sheet
aligns, and `minmax(0,…)` is what allows the value to truncate instead of expanding the grid.

`PROPERTY_CELL = "border border-transparent py-1"` is the inline-edit trick: the cell
**always** reserves a 1 px border box, so when a field becomes an editable input with a
visible border, nothing moves by a pixel.

### 3.3 `tabular-nums`

Applied everywhere a number can change without a layout change being wanted — 24 call
sites. Representative:

- Money: `record-parts.tsx:85` `<span className="tabular-nums">{formatMoney(amountCents, currency)}</span>`
- Sheet stats: `company-sheet.tsx:303,308`
- Counts in tab labels: `detail-sheet.tsx:206` `<span className="text-muted-foreground tabular-nums">`
- Filter/column counts: `data-table.tsx:232,392` `<span className="tabular-nums opacity-60">`
- Pagination: `table-pagination.tsx:33,53`
- Chart tooltips/axes: `chart.tsx:256`, `dashboard-chart.tsx:527`
- Stat cards & deltas: `stat-card.tsx:42,84`

Rule of thumb visible in the code: **any digit that re-renders gets `tabular-nums`**;
static digits don't.

### 3.4 Border-versus-shadow

The design doc is explicit that only two things are filled (`docs/design.md:22-25`), and the
CSS backs it with a border-first surface language:

- Dashboard surfaces use plain `border`, **no shadow** — `packages/ui/src/components/dashboard.tsx:131`
  `cn("flex flex-col gap-4 border p-5 md:p-6", className)`; `:202`, `:206` likewise;
  `StatGroup` at `:13` `"@container/stats overflow-hidden border"`.
- Table shell: `rounded-lg border bg-card` (`data-table.tsx:428`).
- `shadow-2xs` (4 % black) appears **only** on filled buttons and the active tab —
  `button.tsx:12,14,16,20,22`, `tabs.tsx:67` — and is removed on `:active`
  (`active:shadow-none`) so the press reads as a physical depress.
- The only real shadows are on floating layers: `shadow-lg` on the sheet
  (`sheet.tsx:11`), `shadow-md` + `ring-1 ring-foreground/10` on select/popover content
  (`select.tsx:89`).
- Inputs in dark mode swap the shadow for an inner one to read as recessed —
  `input.tsx:10`: `dark:bg-muted dark:shadow-[inset_0_1px_1px_rgb(0_0_0/0.30)]`.
- Row hover affordance is a 2 px bar + a padding slide, not a shadow —
  `packages/ui/src/lib/row-accent.ts:1-21`:
  ```ts
  const BAR = [
      "[&>td:first-child]:relative",
      "[&>td:first-child]:before:pointer-events-none [&>td:first-child]:before:absolute",
      "[&>td:first-child]:before:inset-y-0 [&>td:first-child]:before:left-0 [&>td:first-child]:before:w-0.5",
      "[&>td:first-child]:before:bg-foreground [&>td:first-child]:before:opacity-0",
      "[&:hover>td:first-child]:before:opacity-100",
  ].join(" ");
  ```
  The bar is an absolutely-positioned `::before`, so it costs no layout; the accompanying
  `[&:hover>td:first-child]:pl-5` animates padding only inside a `table-fixed` column, so
  the column width never changes.

---

## 4. Scrollbars & overflow containment

### 4.1 The global scrollbar rule (`globals.css:217-239`, inside `@layer base`)

```css
    * {
        scrollbar-width: thin;
        scrollbar-color: var(--border) transparent;
    }
    *::-webkit-scrollbar {
        width: 10px;
        height: 10px;
    }
    *::-webkit-scrollbar-track {
        background: transparent;
    }
    *::-webkit-scrollbar-thumb {
        background-color: var(--border);
        border: 2px solid transparent;
        border-radius: 9999px;
        background-clip: padding-box;
    }
    *::-webkit-scrollbar-thumb:hover {
        background-color: var(--muted-foreground);
    }
    *::-webkit-scrollbar-corner {
        background: transparent;
    }
```

The `border: 2px solid transparent` + `background-clip: padding-box` pair is the whole
trick: the track is 10 px wide (so the hit target stays comfortable) but the *painted*
thumb is 6 px, floating with 2 px of clear space each side. Rounded to a pill, tinted with
the theme's own `--border`, and darkening to `--muted-foreground` on hover — so the
scrollbar is themed in both modes for free, with no `.dark` override needed.

Both the standards (`scrollbar-width/color`) and the WebKit pseudo-elements are declared,
so Firefox and Chromium agree.

### 4.2 Containment in the app

- Scroll owners are explicit and few: `page-shell.tsx:10` (`overflow-y-auto`),
  `data-table.tsx:428` (`overflow-auto`), `detail-sheet.tsx:232` (`overflow-y-auto`),
  `timeline.tsx:214` (`overflow-y-auto`), `simple-table.tsx:63`
  (`min-h-0 flex-1 overflow-x-hidden overflow-y-auto`), `table.tsx:14`
  (`relative w-full overflow-x-auto`).
- `overflow-hidden` is used to *stop* propagation, not to hide: `detail-sheet.tsx:221`
  on each `TabsContent`, `dashboard.tsx:13` on `StatGroup` (so the child grid's borders
  clip to the rounded outer border).
- Mobile settings nav is a horizontal scroller with non-shrinking chips —
  `settings-sidebar.tsx:80` `flex gap-1 overflow-x-auto border-b p-2 md:hidden`, items
  `shrink-0 px-3` (`:87`).

### 4.3 The heavier containment (chat/attachments)

`packages/ui/src/components/message-scroller.tsx:45`:

```tsx
"size-full min-h-0 min-w-0 scroll-fade-b scrollbar-thin scrollbar-gutter-stable overflow-y-auto overscroll-contain contain-content data-autoscrolling:scrollbar-thumb-transparent data-autoscrolling:scrollbar-track-transparent",
```

and `:76`:

```tsx
"min-w-0 shrink-0 [contain-intrinsic-size:auto_10rem] [content-visibility:auto]",
```

- `scrollbar-gutter-stable` reserves the scrollbar's track **before** content arrives, so
  a growing thread never shifts horizontally when the bar appears.
- `overscroll-contain` stops scroll chaining into the sheet behind it.
- `contain-content` + per-item `content-visibility:auto` with `contain-intrinsic-size:auto 10rem`
  skips rendering off-screen messages while still reserving a plausible 160 px box each,
  so the scrollbar length stays honest.

`packages/ui/src/components/attachment.tsx:186` uses the horizontal counterpart:
`flex min-w-0 scroll-fade-x snap-x snap-mandatory scroll-px-1 scrollbar-none gap-3 overflow-x-auto overscroll-x-contain …`.

**Important**: `scroll-fade-b`, `scroll-fade-x`, `scrollbar-thin`, `scrollbar-none`,
`scrollbar-gutter-stable`, `scrollbar-thumb-*` are **not defined in `globals.css`** — they
come from `@import "shadcn/tailwind.css"` (`globals.css:3`, package `@shadcn/react ^0.2.1`).
Porting these class names without that import gives silently dead classes.

---

## 5. The record sheet

### 5.1 URL is the state — `apps/app/components/crm/record-sheet/record-stack.ts`

The open stack is a single comma-separated query param of `kind:id` pairs
(`record-stack.ts:33-39`):

```ts
const params = {
    record: parseAsArrayOf(parseAsString, ",").withDefault([]),
    tab: parseAsString,
    add: parseAsStringLiteral(RECORD_FORMS),
    thread: parseAsString,
    [TIMELINE_PARAM]: timelineTabParser,
};
```

so a URL reads `/contacts?record=contact:abc,company:def&tab=deals`. Everything about the
sheet — which records are stacked, which tab, which sub-form, which thread — is
shareable, refresh-safe and back-button-safe.

The history policy is the interesting bit (`record-stack.ts:79-89`):

```ts
const open = useCallback(
    (ref: RecordRef) => {
        const key = recordKey(ref);
        write(
            [...stack.filter((entry) => recordKey(entry) !== key), ref],
            stack.length === 0 ? "push" : "replace",
        );
    },
    [stack, write],
);
```

**One** history entry is pushed when the sheet first opens; every subsequent
drill-down `replace`s it. So Back always means "close the sheet, return to the list" — it
never walks the user back through five nested records. In-sheet Back is a separate
in-app affordance (`close()` at `:91`, wired at `record-parts.tsx:49`
`onBack={stack.length > 1 ? close : undefined}`). Opening a record already in the stack
de-dupes it and re-tops it (the `filter` above), so cycles can't grow the stack.
Every write also nulls `tab`, `add`, `thread` and the timeline param (`:63-71`) — a fresh
record never inherits the previous one's sub-state.

### 5.2 One host, one mounted record — `record-sheet-host.tsx:11-43`

```tsx
export function RecordSheetHost() {
    const { stack, top, closeAll } = useRecordStack();

    const [shown, setShown] = useState<RecordRef | null>(top);
    if (top && (!shown || recordKey(shown) !== recordKey(top))) {
        setShown(top);
    }

    return (
        <>
            <DetailSheet
                open={stack.length > 0}
                onOpenChange={(next) => {
                    if (!next) closeAll();
                }}
            >
                {shown?.kind === "company" ? (
                    <CompanySheet key={recordKey(shown)} companyId={shown.id} />
                ) : null}
                ...
```

Three deliberate mechanics:

1. Only the **top** of the stack is rendered. The stack below it exists purely as URL data
   and as the Back affordance — no hidden DOM, no N mounted sheets.
2. `shown` is a *derived-state-during-render* latch (the React-approved
   "adjust state while rendering" pattern, no effect). It follows `top` while open, but
   because it is never reset to `null`, the sheet keeps rendering the last record during
   the close animation instead of flashing empty as `stack` empties.
3. `key={recordKey(shown)}` forces a full remount when the record changes — no stale tab
   scroll position or half-edited inline field leaking between records.

### 5.3 Stacking without disturbing the page

`apps/app/components/detail-sheet.tsx:65-81`:

```tsx
<Sheet open={open} onOpenChange={onOpenChange}>
    <SheetContent
        ref={content}
        side="right"
        size={size}
        showCloseButton={false}
        onOpenAutoFocus={(event) => {
            event.preventDefault();
            content.current?.focus();
        }}
        className={cn("flex flex-col gap-0 p-0", className)}
    >
```

- It renders through a Radix **portal** (`sheet.tsx:83-85`) into `document.body`, so it is
  outside the page's flex tree — it cannot reflow the list. Positioning is
  `fixed z-50 … data-[side=right]:inset-y-0 data-[side=right]:right-0 data-[side=right]:h-full`
  (`sheet.tsx:11`) and the overlay is `fixed inset-0 z-50` (`sheet.tsx:62`).
- Width, `sheet.tsx:18-19`: `"2xl": "… sm:max-w-5xl … lg:w-[68vw]"` — 68 % of viewport,
  capped at 1024 px, with a `w-3/4` mobile default (`sheet.tsx:11`).
- `onOpenAutoFocus` is prevented and focus moved to the container instead of the first
  focusable child — that avoids the browser scrolling an input into view, which is a
  classic source of a one-frame jump on open.
- `p-0 gap-0` on the content: the sheet supplies **no** padding, every section brings the
  shared `GUTTER` (§3.1), so header/stats/tabs/body all share one alignment rail.

The sheet's own internal column is another `min-h-0` chain — `detail-sheet.tsx:196,221,232`:

```tsx
className="flex min-h-0 flex-1 flex-col gap-0"                                            // Tabs root
className="flex min-h-0 flex-1 flex-col overflow-hidden outline-none data-[state=inactive]:hidden"  // TabsContent
className="flex min-h-0 flex-1 flex-col overflow-y-auto"                                  // DetailSheetBody
```

with `shrink-0` on the fixed chrome: stats bar `detail-sheet.tsx:151`
`<dl className="flex shrink-0 divide-x border-b bg-muted/40">`, tab list `:200`
`"w-full shrink-0 justify-start gap-6 border-b"`.

### 5.4 `keepMounted` — `detail-sheet.tsx:180-227`

```tsx
export function DetailSheetTabs({ tabs, value, onValueChange }: {...}) {
    const [opened] = useState(() => new Set<string>());
    opened.add(value);

    return (
        <Tabs value={value} onValueChange={onValueChange} className="flex min-h-0 flex-1 flex-col gap-0">
            ...
            {tabs.map((tab) => (
                <TabsContent
                    key={tab.value}
                    value={tab.value}
                    forceMount={
                        tab.keepMounted && opened.has(tab.value) ? true : undefined
                    }
                    className="flex min-h-0 flex-1 flex-col overflow-hidden outline-none data-[state=inactive]:hidden"
                >
                    {tab.content}
                </TabsContent>
            ))}
        </Tabs>
    );
}
```

**Lazy-then-sticky**: a `keepMounted` tab is not mounted until it has been visited once
(`opened` gate), and from then on it is `forceMount`ed and merely hidden by
`data-[state=inactive]:hidden`. The one consumer is the agent chat —
`company-sheet.tsx:243-247`:

```tsx
{
    value: "agent",
    label: "Agent",
    content: <AgentPanel record={{ kind: "company", id: company.id }} />,
    keepMounted: true,
},
```

so a streaming agent run and its scroll position survive tab switches, while the cost is
never paid by users who never open the tab. `opened` is a `useState(() => new Set())`, i.e.
per-mount — and since the host remounts on record change (§5.2), the gate resets per record.

### 5.5 Why the list underneath doesn't re-render

- `RecordSheetHost` is mounted in the **layout** (`(app)/layout.tsx:33`), a sibling of
  `{children}` — opening a record never re-renders the page subtree from the top.
- nuqs writes are shallow by default: the `record` param changes the URL without a server
  round-trip or an RSC re-fetch of the list page.
- Subscriptions are split by hook: the table reads only its own params
  (`useTableQuery(contactsSearchParams)`, `contacts-table.tsx:118`), the sheet reads only
  `record`/`tab`/`add`/`thread`. A record change doesn't touch the table's params.
- The list query holds its rows across refetches — `contacts-table.tsx:120-123`:
  ```tsx
  const contacts = useQuery({
      ...trpc.contacts.list.queryOptions(input),
      placeholderData: (previous) => previous,
  });
  ```
- And the table defers row swaps — `data-table.tsx:192` `const deferredRows = useDeferredValue(rows);`
- Rows are prefetched on hover so the sheet opens with data already in cache —
  `contacts-table.tsx:165` `onRowHover={(row) => prefetchRecord({ kind: "contact", id: row.id })}`,
  implementation in `record-prefetch.ts:8-32`.

### 5.6 Mobile: sheet becomes a drawer

`apps/app/components/responsive-sheet.tsx:34-45` swaps the Radix sheet for a vaul drawer
below 768 px, behind an identical API, with the breakpoint centralised in
`packages/ui/src/hooks/use-mobile.ts:3` (`const MOBILE_BREAKPOINT = 768;`):

```tsx
function Sheet({ children, ...props }: RootProps) {
    const isMobile = useIsMobile();
    return (
        <ResponsiveContext.Provider value={isMobile}>
            {isMobile ? <Drawer {...props}>{children}</Drawer> : <UISheet {...props}>{children}</UISheet>}
        </ResponsiveContext.Provider>
    );
}
```

and the drawer height is pinned (`responsive-sheet.tsx:64`):
`"data-[vaul-drawer-direction=bottom]:h-[88dvh]"` — note `dvh` here (a drawer *should*
track the dynamic viewport) versus `svh` on the frame.

---

## 6. `PageShell` composition

`apps/app/components/page-shell.tsx` exports seven pieces (`:123-131`) and every page uses
the same five-line skeleton. Full contact page (`apps/app/app/(app)/contacts/page.tsx:42-60`):

```tsx
<PageShell className="min-h-0">
    <PageShellHeader>
        <PageShellHeading>
            <PageShellTitle>Contacts</PageShellTitle>
            <PageShellDescription>Everyone in the pipeline.</PageShellDescription>
        </PageShellHeading>
        <PageShellActions>
            <CreateContactSheet />
        </PageShellActions>
    </PageShellHeader>

    <PageShellContent className="min-h-0">
        <HydrateClient>
            <ContactsTable />
        </HydrateClient>
    </PageShellContent>
</PageShell>
```

The clever part is the header. `PageShellHeader` (`page-shell.tsx:31-42`) declares a
two-column grid once:

```tsx
<header
    data-slot="page-shell-header"
    className={cn("flex flex-col gap-3 [view-transition-name:page-header]", className)}
    {...props}
>
    <div className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-4 gap-y-2">
        {children}
    </div>
</header>
```

and each child **places itself** by explicit line, so the JSX order can't break the layout
(`page-shell.tsx:64,80,96`):

```tsx
"col-start-1 row-start-1 min-w-0 self-center text-balance font-medium text-2xl tracking-tight md:text-3xl"   // Title
"col-span-full row-start-2 text-balance text-muted-foreground text-sm"                                        // Description
"col-start-2 row-start-1 flex flex-wrap items-center gap-2 self-center justify-self-end"                      // Actions
```

`PageShellHeading` exists only to group title+description semantically without adding a
grid item — `page-shell.tsx:53` `cn("contents", className)`. `display: contents` dissolves
the wrapper so its children remain direct grid items. This is the pattern that lets
`OverviewGreeting` (a client component) be dropped in as a heading on the dashboard
(`(app)/page.tsx:37-41`) while the static pages pass a literal `<PageShellTitle>` — same
grid, different content, no per-page CSS.

`PageShellContent` (`page-shell.tsx:104-121`) opens a container-query scope:

```tsx
"@container/page-content flex flex-1 flex-col gap-6"
```

so the dashboard grids size themselves against the **pane**, not the viewport —
`dashboard.tsx:29-32,55-68`:

```tsx
const GRID_COLS = {
    2: "@md/dashboard:grid-cols-2",
    3: "@md/dashboard:grid-cols-2 @4xl/dashboard:grid-cols-3",
    4: "@md/dashboard:grid-cols-2 @4xl/dashboard:grid-cols-4",
} as const;
```
```tsx
split === "hero" ? "@3xl/dashboard:grid-cols-[2fr_1fr]" : "@3xl/dashboard:grid-cols-2",
```

This is why nothing in the dashboard breaks when the icon rail disappears at `md` — the
content reacts to its own width, not the window's.

Every part carries a `data-slot="page-shell-*"` attribute (`:9,13,33,52,62,78,94,112`),
giving stable hooks for tests and for future CSS without touching class names.

---

## 7. Everything that prevents layout shift

Consolidated list, each with its anchor:

1. **`h-svh`** not `100vh` — mobile browser chrome cannot resize the frame. `(app)/layout.tsx:18`
2. **Fixed, `shrink-0` chrome** — header 48 px (`app-header.tsx:62`), rail 56 px
   (`app-icon-rail.tsx:122`), settings sidebar 224 px (`settings-sidebar.tsx:62`),
   sheet stats and tab bar (`detail-sheet.tsx:151,200`), pagination
   (`table-pagination.tsx:32` `flex shrink-0 flex-wrap …`).
3. **`min-h-0` chain** — content overflow is always absorbed by a designated scroller
   instead of growing the frame. See §1.2/§1.3.
4. **`table-fixed` + percentage column widths** — column geometry is decided before data
   arrives, so rows can't re-measure as cells fill. `data-table.tsx:425`, widths declared
   per column in `contacts-table.tsx:30,48,61,74,81,91,105` (`w-[22%] … w-[12%]`) and
   `company-sheet.tsx:100-114`.
5. **Sticky header drawn with an inset shadow**, not a border (`data-table.tsx:430`) — the
   1 px line paints reliably without a border box.
6. **Reserved transparent border on editable cells** — `detail-sheet.tsx:48`
   `const PROPERTY_CELL = "border border-transparent py-1";`
7. **Fixed label column** — `detail-sheet.tsx:44` `grid grid-cols-[6.5rem_minmax(0,1fr)] gap-2`.
8. **Reserved section-header height** — `detail-sheet.tsx:258`
   `<div className="flex h-5 items-center justify-between gap-3">` (row keeps 20 px whether
   or not an action button is present).
9. **Explicit image dimensions with an equal-size fallback** — `entity-logo.tsx:11-17`
   (`PX` map 16/20/24/32/48), `:55-56` box classes, `:110` `<Image … width={px} height={px} />`,
   and initials render inside the same box when the URL fails (`:83`), so a broken logo
   never collapses the row. `loading="lazy" decoding="async"` at `:103-104`.
10. **Em-dash placeholder for empty cells** — `packages/ui/src/components/empty-cell.tsx:3-5`
    `return <span className={cn("text-muted-foreground", className)}>—</span>;`
11. **`tabular-nums`** on every mutable number — §3.3.
12. **`placeholderData: (previous) => previous`** + **`useDeferredValue`** — the table never
    empties between pages/filters. `contacts-table.tsx:122`, `data-table.tsx:192`.
13. **Fixed-height empty/loading rows** — `data-table.tsx:484`
    `"h-32 whitespace-normal py-8 text-center align-middle text-muted-foreground"`, so the
    spinner state is the same height as a populated chunk.
14. **`suppressHydrationWarning` on relative timestamps** — `contacts-table.tsx:94,107` —
    server/client "2 hours ago" mismatches don't blow up or re-lay-out.
15. **`scrollbar-gutter-stable`** in the chat viewport — `message-scroller.tsx:45`.
16. **`content-visibility:auto` with `contain-intrinsic-size:auto 10rem`** — off-screen
    messages still reserve height. `message-scroller.tsx:76`.
17. **`truncate` / `min-w-0` everywhere text meets a flex row** — e.g. `app-header.tsx:81`
    `"min-w-0 truncate font-medium text-sm"`, `record-link.tsx:28`, `contacts-table.tsx:40`.
    Long names can't push a layout wider.
18. **`text-balance` / `text-pretty`** on headings and prose — `page-shell.tsx:64,80`,
    `card.tsx:31,42` — avoids orphan-driven height changes between renders.
19. **Portal + `fixed` sheet** — the detail pane never occupies page flow (§5.3).
20. **Focus is not auto-scrolled into view on sheet open** — `detail-sheet.tsx:72-75`.
21. **`isolate`** on the frame plus `::view-transition-group(app-header)/(app-rail) { z-index: 100 }`
    (`globals.css:444-447`) — chrome stays put and on top during page transitions.
22. **Icon size is pinned by the button variant** — `button.tsx:7`
    `[&_svg]:shrink-0 [&_svg:not([class*='size-'])]:size-4` — an icon that loads late can't
    resize its button.

---

## 8. Porting to Collecct — adopt / adapt / conflict

Collecct = Next 15.5 + React 19 + Tailwind + **one hand-written `app/globals.css` (~1500 lines)**,
top bar + Bid sidebar + pipeline list + detail pane. That target is structurally the same
shape as this app (header + rail + list + right sheet), so most of the *layout* transfers
cleanly; most of the *CSS plumbing* does not.

### Adopt as-is (pure Tailwind class recipes, no stylesheet dependency)

- **The frame**: `isolate flex h-svh flex-col` → `header h-12 shrink-0` → `flex min-h-0 flex-1`
  → sidebar `w-14 shrink-0 … md:flex` → `<main class="flex min-w-0 flex-1 flex-col overflow-y-auto …">`.
  Swapping Collecct's current top-bar shell to this eliminates whole-page scroll in one edit.
  The `min-h-0` chain must be unbroken from `(app)/layout` down to the table container —
  a single missing `min-h-0` re-introduces body scroll and is the #1 failure mode.
- **`h-svh` over `h-screen`** for the frame; `dvh` only for a bottom drawer.
- **PageShell + `display:contents` heading grid** — one file (`page-shell.tsx`, 131 lines,
  zero dependencies beyond `cn`) that removes header markup from every page. Direct copy.
- **Sticky table header via `shadow-[inset_0_-1px_0_var(--border)]`** — assumes a
  `--border` var exists; Collecct almost certainly has an equivalent, rename and go.
- **`table-fixed` + per-column `w-[N%]`** for the pipeline list.
- **`placeholderData: (previous) => previous` + `useDeferredValue`** for the list.
- **URL-driven detail pane** via nuqs (`record=kind:id` array, push-once-then-replace) —
  Collecct already uses nuqs-style filters per the opportunity-filters work, and this makes
  a Bid detail pane deep-linkable and Back-safe for free.
- **`keepMounted` lazy-then-`forceMount` tabs** — directly applicable to an agent/chat or
  a long-running enrichment tab in the Bid pane.
- **`tabular-nums`** on contract values, deadlines, counts; **`PROPERTY_CELL`
  transparent-border** reservation for inline-editable fields.
- **The scrollbar block** (`globals.css:217-239`) — self-contained, theme-driven, ~20 lines,
  works in any stylesheet. Just be aware of the layer caveat below.

### Adapt

- **Density**: this app is `text-xs`-based with a 24/28/32/36 control ladder. If Collecct's
  existing CSS is `text-sm`/`h-9`-based, do **not** half-adopt — mixed ladders look broken.
  Either take the whole ladder or keep yours and just take the geometry rules.
- **`* { border-color: var(--border) }`** in `@layer base` is a prerequisite for every bare
  `border`/`border-b` in the copied JSX. If Collecct doesn't have it, copied markup renders
  with the UA's black borders. Either add the reset or write `border-b border-[var(--border)]`
  at each call site.
- **`GUTTER = "px-5"` constant** — cheap discipline, adopt the idea even if the value differs.
- **`useIsMobile()` 768 px + responsive Sheet/Drawer swap** — note it returns `false` on the
  first render (`use-mobile.ts:6-20`, state starts `undefined`), so SSR always paints the
  desktop branch and mobile hydration swaps. Acceptable here because the sheet starts
  closed; be careful if Collecct wants an SSR-correct mobile shell.

### Conflicts — check before copying

1. **Tailwind v4 only.** `@theme inline`, `@utility`, `@custom-variant`, `@source`, and
   `@import "tailwindcss"` are all v4 syntax and require `@tailwindcss/postcss`. If Collecct
   is on v3 with a `tailwind.config.js`, none of §2 ports — the equivalent is
   `theme.extend` + `:root` vars. Confirm Collecct's Tailwind major first; this is the
   single biggest fork in the road.
2. **`@layer` vs a 1500-line unlayered stylesheet — this will bite.** Under the cascade,
   **unlayered CSS always beats layered CSS regardless of specificity**. Collecct's existing
   hand-written rules are unlayered; Tailwind v4 puts its utilities in `@layer utilities`.
   So a legacy `.pipeline-row td { padding: 12px }` will silently defeat `py-2.5` on a
   copied component. Fix before migrating anything: wrap the legacy sheet in
   `@layer legacy { … }` and declare the order explicitly
   (`@layer theme, base, legacy, components, utilities;`), or accept that every ported
   component needs `!` overrides. Decide this up front — retrofitting is far worse.
3. **React 19.2 / Next 16 features are used in the shell.** `PageTransition`
   (`page-transition.tsx:2` `import { ViewTransition } from "react"`) and
   `<Link transitionTypes={["nav-lateral"]}>` (`app-icon-rail.tsx:71`,
   `settings-sidebar.tsx:49`) do **not** exist on Next 15.5 / React 19.0-19.1. Strip
   `PageTransition` from the copied `PageShell` (it's a pure wrapper — delete the two lines
   at `page-shell.tsx:3,7,21`) and drop `transitionTypes`. The `::view-transition-*` block
   (`globals.css:434-481`) and `[view-transition-name:*]` classes become inert but harmless.
4. **`shadcn/tailwind.css` utilities** (`scroll-fade-b`, `scrollbar-thin`,
   `scrollbar-gutter-stable`, `scrollbar-none`) are third-party. If Collecct doesn't install
   `@shadcn/react`, hand-write the two you actually need:
   `scrollbar-gutter: stable;` and a `mask-image` fade.
5. **Global `*` scrollbar rules** override any existing custom scrollbar CSS in Collecct's
   1500 lines (or lose to it, per conflict #2). Reconcile deliberately, don't paste both.
6. **Radius scale is non-monotonic** (`2xl` 9px < `xl` 12px, §2.2). Don't copy the
   `calc(var(--radius) * n)` tail; define 2xl/3xl explicitly or omit them.
7. **Dead/no-op tokens to not carry over**: `--tracking-normal` (unused),
   `--shadow-x/y/blur/spread/opacity/color` (not referenced by the composed shadows),
   `--spacing` in `:root` (never mapped into `@theme`, so it changes nothing).
8. **The `.dark` block duplicates the shadow scale verbatim** (`globals.css:125-138`) — 14
   lines of copy-paste. If Collecct hand-maintains its dark block, don't inherit this;
   define shadows once outside the theme blocks.
9. **No `tailwind.config.*` at all** in this repo. If Collecct has plugins
   (typography, forms) wired through a config, a v4 migration has to re-home them as
   `@plugin` imports.
