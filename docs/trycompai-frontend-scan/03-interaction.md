# 03 — Perceived Performance & Interaction Feel

Source: `/private/tmp/.../scratchpad/crm` — a Turborepo monorepo (`apps/app` = Next.js 15 app
router + React 19, `apps/api` = NestJS + nestjs-trpc, `packages/ui` = shared component lib).
State transport is **nuqs** (URL query state) + **TanStack Query** (server state). There is no
Redux/Zustand, and almost no `useState` outside of drafts and popovers.

The whole "feels instant" story here rests on four load-bearing decisions:

1. **Every list/filter/sort/page/tab/expand/column-visibility value lives in the URL**, not in
   component state. Nothing has to be re-derived or re-synced on mount.
2. **`placeholderData: (previous) => previous`** on every paginated query, so a refetch never
   empties the table.
3. **`useDeferredValue(rows)`** inside `DataTable`, so the incoming page never blocks typing.
4. **Local-state-for-typing + debounced commit** in `useSearchInput`, so the input is never
   throttled by the network or by URL writes.

---

## 1. `useSearchInput` — the 250ms debounce hook (VERBATIM, in full)

`packages/ui/src/hooks/use-search-input.ts` — the entire file, 29 lines:

```ts
"use client";

import { useEffect, useRef, useState } from "react";

export function useSearchInput(
	committed: string,
	commit: (value: string) => void,
	delayMs = 250,
) {
	const [value, setValue] = useState(committed);

	const commitRef = useRef(commit);
	commitRef.current = commit;

	const committedRef = useRef(committed);
	committedRef.current = committed;

	useEffect(() => {
		setValue(committed);
	}, [committed]);

	useEffect(() => {
		if (value === committedRef.current) return;
		const timer = setTimeout(() => commitRef.current(value), delayMs);
		return () => clearTimeout(timer);
	}, [value, delayMs]);

	return [value, setValue] as const;
}
```

### What each piece buys

- **`const [value, setValue] = useState(committed)`** (line 10) — the *local* mirror. The
  `<input>` is controlled by this, never by the URL. Keystrokes therefore render at React speed
  and are completely decoupled from the router, the query, and the network. This is the whole
  trick: the input is a *local* controlled component that happens to publish to a slower store.

- **`commitRef` (lines 12–13)** — the commit callback is stashed in a ref and reassigned on every
  render *during render*, not in an effect. This keeps the debounce effect's dep array to
  `[value, delayMs]` only. If `commit` were a dep, the inline arrow passed by `ListSearch` would
  be a new identity every render and would **cancel and restart the timer on every render**,
  meaning the search would never fire while anything else on the page was re-rendering. This ref
  is the difference between a working debounce and a debounce that starves.

- **`committedRef` (lines 15–16)** — same trick for the committed value. Read inside the effect
  via `committedRef.current` rather than being a dependency, so that the arrival of a new
  committed value (from the URL) does not itself restart the debounce timer.

- **`useEffect(() => { setValue(committed); }, [committed])` (lines 18–20)** — the **two-way
  sync**. Downward: URL → input. This is what makes browser Back/Forward, a shared link, or a
  programmatic `setState({ q: "" })` actually move the caret text. Without it the input would
  drift permanently out of sync with the URL after a history navigation.

- **`if (value === committedRef.current) return;` (line 23)** — the **loop breaker**. When the
  commit lands and the URL echoes back, effect #1 sets `value` to the same string; effect #2 then
  sees `value === committed` and bails without scheduling another commit. Without this guard the
  two effects would ping-pong forever.

- **`return () => clearTimeout(timer)` (line 25)** — each keystroke cancels the pending commit.
  Classic trailing debounce: exactly one network round trip per 250ms *pause*, not per keystroke.

- **`delayMs = 250`** — note the default, not 300/500. 250ms is roughly the boundary where a
  round trip still reads as "the app responded to what I typed" rather than "the app is
  fetching". They also made it a parameter, so a heavier endpoint can dial it up without forking
  the hook.

### The consumer — `apps/app/components/data-table/list-search.tsx` (whole component)

```tsx
export function ListSearch({ placeholder }: { placeholder: string }) {
	const [{ q }, setState] = useQueryStates(searchParsers);

	const [value, setValue] = useSearchInput(q, (next) =>
		setState({ q: next, page: 1 }),
	);

	return (
		<InputGroup className="w-full sm:w-64">
			<InputGroupAddon>
				<Search />
			</InputGroupAddon>
			<InputGroupInput
				placeholder={placeholder}
				value={value}
				onChange={(event) => setValue(event.target.value)}
				autoComplete="off"
			/>
		</InputGroup>
	);
}
```
(`list-search.tsx:13–33`)

Two details worth stealing:
- **`setState({ q: next, page: 1 })`** — committing a search *always* resets to page 1 in the
  same URL write. One history entry, one re-render, no flash of "page 4 of a 1-page result".
- **`autoComplete="off"`** — kills the browser's native dropdown, which otherwise covers the
  first rows of results and makes the list feel occluded/laggy.

### A subtlety in the parsers — `list-search-params.ts:15–18`

```ts
export const searchParsers = {
	q: parseAsString.withDefault(""),
	page: parseAsInteger.withDefault(1).withOptions({ history: "push" }),
};
```

`q` writes with the nuqs default (`history: "replace"`) — typing a search does **not** spam the
back stack. `page` uses `history: "push"` — paging *is* a navigation you expect Back to undo.
That asymmetry is a deliberate feel decision: Back after searching returns you to the previous
*page* you were on, not to the previous keystroke.

And `toInput` trims once, centrally:

```ts
return {
	q: values.q.trim(),
	...
```
(`list-search-params.ts:104`) — so `"acme "` and `"acme"` hit the same query key and the same
cache entry. Trailing-space keystrokes cost zero network.

### Anti-pattern they explicitly rejected

`docs/crm-plan.md:1238` claims *"search debounce is nuqs' `limitUrlUpdates`, not a timer"*. The
shipped code does **not** do that — it uses the timer hook above. The plan's version would have
kept the input value in the URL and throttled the writes, which still routes every keystroke
through the router. The shipped version is faster: the router is never in the typing path at all.

---

## 2. Loading UI vs. keeping stale content — the philosophy

The rule the codebase actually follows: **a spinner is only allowed when there is literally
nothing on screen to keep.** Anywhere prior content exists, the prior content stays and the only
signal is a small non-displacing indicator.

### 2a. The table: stale rows survive, spinner only on empty

`packages/ui/src/components/data-table.tsx:479–488`:

```tsx
<TableBody>
	{deferredRows.length === 0 ? (
		<TableRow className="hover:bg-transparent">
			<TableCell
				colSpan={colCount}
				className="h-32 whitespace-normal py-8 text-center align-middle text-muted-foreground"
			>
				{loading ? <Spinner /> : (empty ?? "No results found.")}
			</TableCell>
		</TableRow>
	) : (
```

Read the condition carefully. `loading` is **only consulted inside the empty branch**. If there
is even one row, `loading` is ignored by the body entirely — the old page 3 rows sit there,
fully interactive and clickable, while page 4 is in flight. `loading` never blanks anything.

Meanwhile every table passes `loading={contacts.isFetching}` — *isFetching*, not *isPending*
(`contacts-table.tsx:164`, `companies-table.tsx:200`, `deals-table.tsx:181`,
`members-table.tsx:182`, `sso-table.tsx:161`). `isFetching` is true for background refetches too,
so the *indicator* is honest about every request; it's the *placement* that keeps it non-
destructive.

Where the indicator actually lives — `packages/ui/src/components/table-pagination.tsx:32–41`:

```tsx
<div className="flex shrink-0 flex-wrap items-center justify-between gap-3">
	<span className="flex items-center gap-2 text-muted-foreground text-xs tabular-nums">
		{loading && <Spinner />}
		{meta ??
			(total === 0
				? "No results"
				: `Showing ${numberFormat.format(rangeStart)}–${numberFormat.format(
						rangeEnd,
					)} of ${numberFormat.format(total)}`)}
	</span>
```

A 16px spinner in the footer status line, next to the row count. It is the *only* thing that
moves. The table body — the thing your eyes are on — is untouched.

### 2b. `placeholderData: (previous) => previous` — the reason rows survive

Applied identically on every paginated/filtered query:

- `contacts-table.tsx:120–123`
- `companies-table.tsx:148–150`
- `deals-table.tsx:131`
- `dashboard-summary.tsx:59–61`
- `members-table.tsx:143`
- `sso-table.tsx:136`
- `quick-switcher.tsx:51–55`

```tsx
const contacts = useQuery({
	...trpc.contacts.list.queryOptions(input),
	placeholderData: (previous) => previous,
});
```

Without this, changing any filter changes the query key → new cache entry → `data === undefined`
→ table renders the empty branch → **full blank + spinner + layout collapse on every keystroke.**
This one line is what converts "search blanks the list" into "search updates the list".

### 2c. `useDeferredValue` — typing never waits on rendering the result

`data-table.tsx:192–196`:

```tsx
const deferredRows = useDeferredValue(rows);
const anyExpandable =
	expandable != null &&
	deferredRows.some((row) => expandable.isExpandable(row));
const colCount = visibleColumns.length + (anyExpandable ? 1 : 0);
```

When a large new page arrives mid-typing, React 19 renders it at low priority. The keystroke
commits first; the 200-row re-render is interruptible. Note also that `anyExpandable` and
`colCount` are derived from `deferredRows`, not `rows` — so the *column count* never disagrees
with the rows currently painted. Deriving from the fresh `rows` while painting deferred rows
would produce a one-frame colspan mismatch.

### 2d. Where they *do* show a spinner (all four are genuine cold starts)

| Location | Code | Why it's legitimate |
| --- | --- | --- |
| Record sheet body | `record-parts.tsx:53–56` — `{loading ? <div className="flex min-h-0 flex-1 items-center justify-center"><Spinner /></div> : ...}` fed by `loading={query.isPending}` (`company-sheet.tsx:253`, `contact-sheet.tsx:138`, `deal-sheet.tsx:99`) | `isPending` = no cached data at all. The sheet just opened on a record never fetched. Header/title/avatar already render *above* the spinner, so the frame is stable. |
| Timeline | `timeline.tsx:203–206` | `history.isPending`, first load of a tab. |
| Dashboard | `dashboard-summary.tsx:71–77` — `if (!summary) return <Spinner/>` | Guards on `!summary`, i.e. *no data ever*, not on `isFetching`. Scope toggle (me/team) reuses `placeholderData` and never hits this. |
| Agent panel | `agent-panel.tsx:96`, `154` | `conversations.isPending` / `archive.isPending \|\| thread.isPending`. |

Every one of them keys off `isPending` (never-loaded) and never off `isFetching`
(refetching). **`isPending` → allowed to show a spinner. `isFetching` → forbidden from replacing
content.** That is the philosophy in one line.

### 2e. Skeletons — used exactly twice, both for *newly revealed* space

Skeletons appear only where content is being revealed into space that did not previously exist,
so there is no stale content to preserve:

- `email-thread-entry.tsx:51–55` — inside an `<AccordionContent>` the user just expanded:
  ```tsx
  {thread.isPending ? (
  	<div className="flex flex-col gap-2">
  		<Skeleton className="h-4 w-1/3" />
  		<Skeleton className="h-4 w-2/3" />
  	</div>
  ) : ...
  ```
  Paired with a lazy query gated on the accordion (`email-thread-entry.tsx:30–35`):
  ```tsx
  const [opened, setOpened] = useState(false);
  const thread = useQuery({
  	...trpc.google.thread.queryOptions({ threadId }),
  	enabled: opened,
  });
  ```
  The timeline doesn't fetch N email threads it may never show. The skeleton has the same rough
  height as the incoming text, so expanding doesn't double-jump.

- `dashboard.tsx:179–213` — `DashboardSkeleton`, and note `aria-hidden` on the wrapper
  (line 190) so screen readers aren't read a wall of nothing.

There are **zero `loading.tsx` route files** and exactly **one `<Suspense>`-consuming component**
(`overview-greeting.tsx` uses `useSuspenseQuery`). Loading is handled by server prefetch +
hydration, not by route-level fallbacks — see 2f.

### 2f. Server prefetch + hydration = no client-side first paint gap

Every list page prefetches on the server and hydrates the exact same query key the client will
ask for (`contacts/page.tsx:30–40`):

```tsx
const values = await contactsSearchParams.load(searchParams);

const trpc = getServerTrpc();
const queryClient = getServerQueryClient();
await queryClient.prefetchQuery(
	trpc.contacts.list.queryOptions(contactsSearchParams.toInput(values)),
);
void queryClient.prefetchQuery(trpc.users.list.queryOptions());
void queryClient.prefetchQuery(
	trpc.companies.options.queryOptions({ q: "" }),
);
```

Three details:
- `contactsSearchParams.load(searchParams)` is the **same parser set** the client uses, so the
  server prefetches the key the client is about to request. A shared/bookmarked URL with filters
  is server-rendered *already filtered*.
- `await` on the primary list (blocking — you must not paint an empty table), `void` on the
  secondary facet options (non-blocking — the filter dropdowns can populate a beat later).
  That's a deliberate two-tier waterfall.
- `apps/app/lib/trpc/query-client.ts:10–14` dehydrates **pending** queries too:
  ```ts
  dehydrate: {
  	shouldDehydrateQuery: (query) =>
  		defaultShouldDehydrateQuery(query) ||
  		query.state.status === "pending",
  },
  ```
  so the `void`-prefetched queries stream their in-flight promise to the client instead of being
  restarted from scratch there.
- `staleTime: 30_000` (`query-client.ts:9`) — navigating back to a list within 30s is instant
  with zero refetch.

---

## 3. Every interaction engineered to feel immediate

**Hover-prefetch on table rows.** `data-table.tsx:512–515`:
```tsx
onMouseEnter={
	onRowHover ? () => onRowHover(row) : undefined
}
onFocus={onRowHover ? () => onRowHover(row) : undefined}
```
wired to `usePrefetchRecord()` (`record-prefetch.ts:8–31`, `queryClient.prefetchQuery` per kind),
used as `onRowHover={(row) => prefetchRecord({ kind: "contact", id: row.id })}`
(`contacts-table.tsx:165`). By the time the click lands, the detail sheet's data is usually in
cache and `isPending` is false — so the spinner in 2d never even appears. Note `onFocus` is wired
identically: **keyboard tabbing prefetches too**, so keyboard users get the same warm cache as
mouse users. That symmetry is rare and worth copying.

**Inline field edits show your own text immediately.** `inline-field.tsx:65`:
```tsx
const shown = saving ? draft.trim() : (value ?? "");
```
While the mutation is in flight the field renders *your* draft, not the stale server value. It
reverts to `value` only once the mutation settles and the invalidated query returns. This is the
optimistic *feel* — you never watch your typed value snap back to the old one — without any
`onMutate`/`setQueryData` rollback machinery. (Confirmed: `grep -rn "onMutate|setQueryData"` over
`apps/app` returns **zero hits**. There are no true optimistic updates anywhere in this app; the
perceived immediacy is entirely local draft state + placeholder data + prefetch.)

**Per-field save spinners.** `inline-field.tsx:25–33`:
```ts
export function savingField(update: {
	isPending: boolean;
	variables?: { data?: object } | undefined;
}): (field: string) => boolean {
	const fields = update.isPending
		? Object.keys(update.variables?.data ?? {})
		: [];
	return (field) => fields.includes(field);
}
```
It reads `update.variables.data` — the payload of the *in-flight* mutation — to work out **which
field** is saving, so only that one row shows a spinner instead of the whole sheet greying out.

**Two-tier cache invalidation — `settle: "record"`.** `apps/app/lib/trpc/cache.ts:31–46`:
```ts
const run = (
	record: QueryKey[],
	rest: QueryKey[],
	{ settle = "all" }: Options = {},
): Promise<void> => {
	const awaited = settle === "all" ? [...record, ...rest] : record;
	const behind = settle === "all" ? [] : rest;

	for (const queryKey of behind) {
		void queryClient.invalidateQueries({ queryKey });
	}

	return Promise.all(
		awaited.map((queryKey) => queryClient.invalidateQueries({ queryKey })),
	).then(() => undefined);
};
```
With `settle: "record"` the awaited promise resolves as soon as the **record you're looking at**
refetches; the list pages, dashboard and search index refresh in the background (`void`, not
awaited). So the button's pending state clears the instant the visible thing is correct, instead
of waiting on six queries you can't see. Used at `company-sheet.tsx:344`, `contact-sheet.tsx:303`,
`deal-sheet.tsx:184`, `facts.tsx:61`, `google-connection.tsx:230`.

Also in the same file, deletion (`cache.ts:105–134`) invalidates every *other* record but marks
the deleted key `refetchType: "none"` — so closing a deleted record doesn't fire a doomed request
that 404s and flashes an error.

**Conditional polling, armed by the data itself.** `companies-table.tsx:151–156`:
```tsx
refetchInterval: (query) =>
	query.state.data?.rows.some((row) =>
		isEnriching(row.enrichmentStatus, row.queued),
	)
		? ENRICHMENT_POLL_MS
		: false,
```
The list polls at 3s **only while a visible row is actually enriching**, and disarms itself the
moment the last one finishes. Same shape at `agent-panel.tsx:143–144` (poll while
`status === "working"`), `company-sheet.tsx:172`, `contact-sheet.tsx:90`,
`google-connection.tsx:202`. No `setInterval`, no cleanup bugs, no polling a settled page.

**Sheet content survives the closing animation.** `record-sheet-host.tsx:14–17`:
```tsx
const [shown, setShown] = useState<RecordRef | null>(top);
if (top && (!shown || recordKey(shown) !== recordKey(top))) {
	setShown(top);
}
```
This is React's sanctioned *set-state-during-render* derived-state pattern (no effect, no extra
paint). `shown` deliberately **doesn't** clear when `top` becomes null, so the sheet keeps
rendering its record through the exit animation instead of flashing empty. And `key={recordKey(shown)}`
(lines 28/32/36) forces a clean remount per record — replacing what would otherwise be a
reset-on-prop-change effect (the skill's Rule 5, applied correctly).

**Tab content stays mounted once opened.** `detail-sheet.tsx:189–190` and `218–221`:
```tsx
const [opened] = useState(() => new Set<string>());
opened.add(value);
...
forceMount={
	tab.keepMounted && opened.has(tab.value) ? true : undefined
}
```
A lazily-initialised Set (`useState(() => new Set())`, so it's allocated once) records which tabs
you have visited. Visited + `keepMounted` tabs render `forceMount` and are merely hidden with
`data-[state=inactive]:hidden` — so switching back to the Agent tab restores its scroll position
and in-flight stream instantly rather than remounting. Unvisited tabs are never mounted, so the
initial sheet open stays cheap. Opt-in per tab (`keepMounted: true` only on the Agent tab,
`company-sheet.tsx:246`).

**Quick switcher keeps its previous hit list.** `quick-switcher.tsx:51–55`:
```tsx
const results = useQuery({
	...trpc.search.quick.queryOptions({ q: query }),
	enabled: open && query.trim().length >= 2,
	placeholderData: (previous) => previous,
});
```
Typing another character keeps the previous results on screen — the palette never flickers to
empty mid-word. `enabled: ... length >= 2` avoids firing a useless one-character query, and the
`CommandEmpty` explains *why* nothing is shown rather than looking broken
(`quick-switcher.tsx:79–83`):
```tsx
<CommandEmpty>
	{query.trim().length < 2
		? "Type at least two characters."
		: "Nothing matches."}
</CommandEmpty>
```
Also `<Command shouldFilter={false}>` (line 72) — cmdk's client-side fuzzy filter is disabled
because the server already ranked the hits; leaving it on would silently hide server results that
don't fuzzy-match.

**Composer clears on success, not on click.** `activity-composer.tsx:60–79` — `reset()` runs in
`onSuccess` *after* `await cache.activity()`, and `submit()` early-returns
`if (text === "" || create.isPending) return;`. Double-submit is impossible, and the draft is
never lost to a failed request.

**The send button only exists when it's usable.** `activity-composer.tsx:142`:
`{text === "" ? null : (<InputGroupButton type="submit" ...>)}` — the button appears the moment
you type. A disabled-but-present button reads as "the app is stuck"; an appearing button reads as
"the app noticed".

---

## 4. Keyboard / focus / a11y affordances that also improve feel

**⌘K / Ctrl-K quick switcher** — `quick-switcher.tsx:39–49`, the app's single global shortcut:
```tsx
useMountEffect(() => {
	const onKeyDown = (event: KeyboardEvent) => {
		if (event.key === "k" && (event.metaKey || event.ctrlKey)) {
			event.preventDefault();
			void setOpen((current) => (current ? null : true));
		}
	};

	document.addEventListener("keydown", onKeyDown);
	return () => document.removeEventListener("keydown", onKeyDown);
});
```
The open state is `useQueryState("k", parseAsBoolean...)` (line 36) — the palette's visibility is
in the URL, so Escape/Back both close it and a deep link can open it. `setOpen(null)` rather than
`false` removes the param entirely instead of leaving `?k=false` litter. Note the toggle:
pressing ⌘K again closes it.

**Sheet focus is redirected to the scroll container, not the first control** —
`detail-sheet.tsx:63–75`:
```tsx
const content = useRef<HTMLDivElement>(null);
...
onOpenAutoFocus={(event) => {
	event.preventDefault();
	content.current?.focus();
}}
```
Radix's default would focus the first focusable child (usually the Back or Close button), which
both looks wrong (a highlighted button on open) and breaks arrow-key scrolling. Focusing the
container means the sheet is immediately scrollable by keyboard *and* nothing is spuriously
ringed. Deliberate, and rarely done.

**Inline edit keyboard contract** — `inline-field.tsx:69–88`: `autoFocus` on the input, `onBlur`
commits, `Enter` commits (with `preventDefault`), `Escape` restores `value` and exits. Commit is
guarded — `if (draft.trim() !== (value ?? "")) onSave(draft.trim());` (line 62) — so tabbing
through fields without changing anything fires zero mutations.

**⌘/Ctrl-Enter to submit the composer** — `activity-composer.tsx:94–100`, plus `Escape` to clear
the draft and the due date.

**Addon click focuses the input** — `input-group.tsx:56–62`: clicking the search magnifier (or
any inline addon) focuses the `<input>`, with an explicit bail-out for addons that contain a
button and for block-start/block-end addons. The whole search chrome becomes one target.

**Table semantics that also read well** — `data-table.tsx:449–455` sets real
`aria-sort={"ascending" | "descending" | undefined}` on the active header; `aria-expanded` +
`aria-controls={filtersId}` on the mobile Filters disclosure (`:224–225`, `useId()` at `:175`);
`<span className="sr-only">Detail</span>` labels the expander column (`:434`);
`<Spinner>` carries `role="status" aria-label="Loading"` (`spinner.tsx:8–9`), so a refetch is
announced without any visible text moving; `DashboardSkeleton` is `aria-hidden` (`dashboard.tsx:190`).

**Reduced motion is respected throughout** — every view-transition and hover animation sits
inside `@media (prefers-reduced-motion: no-preference)` (`globals.css:261`, `:449`) with explicit
`reduce` overrides at `:839` and `:883`. The baseline is `animation: none` for all view
transitions (`globals.css:434–442`) and only the named nav classes opt in — i.e. motion is
opt-in, not opt-out.

---

## 5. Layout stability — what stops content jumping

**`table-fixed` + explicit per-column widths.** `data-table.tsx:425` sets `table-fixed`, and every
column declares a percentage width — `w-[22%]`, `w-[20%]`, `w-[24%]`, `w-[18%]`, `w-[16%]`,
`w-[10%]`, `w-[12%]` (`contacts-table.tsx:36,48,61,73,81,91,104`). Column widths are therefore
**independent of cell content**, so a page of long company names and a page of short ones lay out
identically. Without `table-fixed`, every refetch would re-measure and every column would twitch.
Cells are `overflow-hidden` (`:541`) and content is `truncate`d, so nothing can force a reflow.

**`tabular-nums` on every number that changes.** 24 usages. Critically on the pagination status
line (`table-pagination.tsx:33`, `:53`) and the counts (`data-table.tsx:232`, `:392`) — so
"Showing 1–25 of 1,204" → "Showing 26–50 of 1,204" does not shimmy, and `9/10 → 10/10` doesn't
shift the Next button. Also on money (`record-parts.tsx:85`, `deals-table.tsx:66`), counts
(`companies-table.tsx:93`, `:101`), tab badges (`detail-sheet.tsx:206`, `timeline.tsx:194`) and
KPI values (`stat-card.tsx:42`, `:84`).

**Fixed row and header heights.** `h-11` on every `<TableHead>` (`data-table.tsx:433`, `:443`),
`py-3` on body cells (`:541`) and `py-2.5` on sub-rows (`:578`), `h-32` on the empty/loading cell
(`:484`). That last one is the important one: **the empty state and the spinner occupy the same
reserved 128px block**, so going from "loading" → "no results" → "3 rows" doesn't collapse and
re-expand the container.

**Sticky header with an inset shadow instead of a border.** `data-table.tsx:430`:
```
"sticky top-0 z-10 bg-muted [&_th]:bg-muted [&_tr]:border-0 [&_tr]:shadow-[inset_0_-1px_0_var(--border)]"
```
The header rule is an *inset box-shadow*, not a border. A border on a sticky element inside a
scroll container renders inconsistently (sub-pixel gaps as you scroll); an inset shadow paints
inside the element's own box and stays put. Cheap trick, big difference.

**Number formatters are module-level singletons, not per-render.** `table-pagination.tsx:9`
(`const numberFormat = new Intl.NumberFormat();`), `activity-composer.tsx:31–34`,
`email-thread-entry.tsx:15–20`. `Intl.*Format` construction is genuinely expensive; hoisting it
out of the component keeps large list renders cheap.

**`suppressHydrationWarning` on relative timestamps** — `contacts-table.tsx:94`, `:107`:
```tsx
<span className="text-muted-foreground" suppressHydrationWarning>
	{relativeTimeFromIso(row.lastActivityAt)}
</span>
```
Server and client compute "3 days ago" against different clocks. Rather than deferring the render
(which would cause a visible pop-in) they accept and silence the mismatch. The cell paints once,
with content, in the server HTML.

**Independent scroll panes; the shell itself never scrolls.** `(app)/layout.tsx:18` is
`h-svh flex flex-col` with `flex min-h-0 flex-1` on the row (`:28`). `PageShell` puts
`overflow-y-auto` on `<main>` (`page-shell.tsx:10`). The table container is
`min-h-0 flex-1 overflow-auto rounded-lg border bg-card` (`data-table.tsx:428`). Result: the
header and icon rail are fixed, the table body scrolls inside its own bordered box, and the
pagination bar (`shrink-0`, `table-pagination.tsx:32`) is always visible without being
`position: fixed`. `min-h-0` appears on essentially every flex ancestor — that's the flexbox
detail that makes a nested scroll container actually scroll instead of growing the page.
Same nesting in the sheet: `detail-sheet.tsx:221` (`flex min-h-0 flex-1 flex-col overflow-hidden`),
`:232` (`overflow-y-auto`), `timeline.tsx:214`.

**Custom thin scrollbars, globally.** `globals.css:217–239` — `scrollbar-width: thin`,
transparent track, `--border`-coloured thumb with `background-clip: padding-box` and a 2px
transparent border (so the thumb reads as inset). Because these are styled rather than hidden,
scroll panes don't gain/lose width when content overflows. The chat viewport goes further with
`scrollbar-gutter-stable` (`message-scroller.tsx:45`) — gutter space reserved before it's needed,
so streaming text never nudges the layout sideways when the scrollbar appears.

**`content-visibility: auto` on chat messages** — `message-scroller.tsx:76`:
`"[contain-intrinsic-size:auto_10rem] [content-visibility:auto]"`. Off-screen messages skip
layout/paint entirely, but the declared intrinsic size keeps the scrollbar honest. Cheap
virtualisation with zero JS.

**Hover accent uses padding on the first cell, not a border.** `row-accent.ts:1–14` — a
`::before` pseudo-element bar with `opacity: 0 → 100`, plus
`[&>td:first-child]:transition-[padding]` / `[&:hover>td:first-child]:pl-5`. Only one cell's
padding animates, and only in one axis; the row height and every other column stay fixed, so
sweeping the mouse down a list doesn't ripple the layout.

---

## 6. The no-useEffect rule vs. reality

### The stated rule

`.agents/skills/no-use-effect/SKILL.md:12`:
> Never call `useEffect` directly. Use derived state, event handlers, data-fetching libraries, or
> `useMountEffect` instead.

Five replacements (SKILL.md:20–26): derive inline · `useQuery` · event handlers · `useMountEffect`
· `key` prop. Escape hatch is `packages/ui/src/hooks/use-mount-effect.ts`:
```ts
export function useMountEffect(effect: () => void | (() => void)): void {
	// biome-ignore lint/correctness/useExhaustiveDependencies: running exactly once on mount is the entire purpose of this hook.
	React.useEffect(effect, []);
}
```

`docs/crm-plan.md:1230–1239` restates it with codebase-specific commitments: *"sheet open state is
derived from nuqs, never synced; search debounce is nuqs' `limitUrlUpdates`, not a timer;
enrichment polling is `refetchInterval`, not `setInterval`; form reset is a `key`, not a
dependency array."*

### Reality: **6 `useEffect` call sites in the entire monorepo.** Verdict: followed, with one
### honest exception and one unenforced lint rule.

`grep -rn "useEffect" --include="*.ts" --include="*.tsx" apps packages` → 8 lines, of which 2 are
imports. The 6 call sites:

| # | Site | Verdict |
| --- | --- | --- |
| 1 | `packages/ui/src/hooks/use-mount-effect.ts:6` | **Sanctioned.** This *is* the escape hatch. |
| 2 | `packages/ui/src/components/calendar.tsx:193` — `React.useEffect(() => { if (modifiers.focused) ref.current?.focus() }, [modifiers.focused])` | **Exempt.** Vendored shadcn/react-day-picker. `biome.jsonc:13` excludes `packages/ui/src/components` wholesale; the plan explicitly anticipates this (`crm-plan.md:1245`). Also a legitimate Rule-4 DOM sync (imperative focus). |
| 3 | `packages/ui/src/hooks/use-mobile.ts:10` — `matchMedia` subscription | **Justified, and they know it.** `biome.jsonc:14` carries a bespoke single-file exclusion: `"!packages/ui/src/hooks/use-mobile.ts"`. Textbook Rule 4 (browser API subscription). Arguably should have been `useMountEffect` — the deps are `[]` anyway — but it predates the hook (shadcn boilerplate). *This is the one nit.* |
| 4+5 | `packages/ui/src/hooks/use-search-input.ts:18` and `:22` | **Justified.** #22 is a `setTimeout` debounce — a timer is an external system with setup/teardown, exactly Rule 4, and there is no non-effect way to express "N ms after the last keystroke". #18 (`setValue(committed)`) is the weaker one: it's Rule-1-shaped (syncing local state from a prop). But it's genuinely reacting to an *external* change (browser history / another component writing `q`), which derived state can't express — the input must stay locally controlled to be fast. Both are quarantined inside one 29-line hook that every consumer uses, so the pattern is written once and reviewed once. |
| 6 | `apps/app/components/crm/agent-panel.tsx:504` — persisting agent session cursor | **The one real violation, and it's a knowing one.** It's Rule-3-shaped ("state changes → fire a mutation"), but the trigger is an *streaming external* event, not a user click. The code goes to unusual lengths to be safe: a `written` ref cursor (`:502`, `:507–509`) makes it idempotent, and a `latest` ref (`:499–500`) keeps unstable deps out of the array. It reads like someone who tried hard to avoid the effect and couldn't. |

**The whole `apps/app` surface — every page, table, form, sheet — contains exactly ONE
`useEffect`.** That is a genuinely remarkable number for an app this size.

### How they avoid the other N effects a normal app would have

- **Data fetching** (Rule 2) → TanStack Query + tRPC, everywhere, no exceptions.
- **Polling** (Rule 4) → `refetchInterval` as a *function of the data* — `companies-table.tsx:151`,
  `agent-panel.tsx:143`, `company-sheet.tsx:172`, `contact-sheet.tsx:90`,
  `google-connection.tsx:202`. Zero `setInterval` in app code.
- **Modal/sheet/palette/tab/expand state** (Rule 1) → nuqs. `useQueryState("new", parseAsBoolean)`
  (`create-contact-sheet.tsx:41–44`), `useQueryState("k", ...)` (`quick-switcher.tsx:36`),
  `useQueryState("expand"|"hide", parseAsArrayOf(...))` (`data-table.tsx:161–173`),
  `useQueryStates` for the record stack (`record-stack.ts:52`). Nothing is synced *to* the URL;
  the URL *is* the state.
- **Form reset on record change** (Rule 5) → `key`. `record-sheet-host.tsx:28/32/36`
  (`key={recordKey(shown)}`), `agent-panel.tsx:110` (`key={openId ?? NEW_THREAD}`),
  `agent-panel.tsx:159` (`key={thread.data?.status === "working" ? "working" : "settled"}`).
- **Derived state** (Rule 1) → set-state-during-render where a `key` won't do:
  `record-sheet-host.tsx:14–17`, and the "remember where I landed" ref at `agent-panel.tsx:85–88`.

### The gap: **the lint rule was never actually wired up**

`docs/crm-plan.md:1243–1246` specifies it:
> Enforce it with Biome — `noRestrictedImports` banning the `useEffect` named import from `react`,
> scoped to `apps/app/**` and `packages/ui/src/hooks/**`.

`biome.jsonc` has no such rule — the linter block is just:
```jsonc
"linter": {
	"enabled": true,
	"rules": {
		"preset": "recommended"
	}
},
```
(`biome.jsonc:27–32`). The `!packages/ui/src/hooks/use-mobile.ts` exclusion at `:14` is a
*fossil* of the intended rule — someone pre-carved the exemption for a rule that never landed.
`AGENTS.md` also never mentions `useEffect`, despite `crm-plan.md:1252` saying *"Add both rules to
`AGENTS.md`."*

**So the rule holds on discipline alone and it is holding well** — but it is not mechanically
enforced, and the one drift (`agent-panel.tsx:504`) is in the newest, least table-shaped feature.
That is exactly the failure mode you'd predict.

---

## 7. Concrete upgrades for Collecct (`frontend/app/page.tsx`)

Measured baseline: **2,616 lines, 70 `useState`, 17 `useEffect`** in one file. Everything below is
a direct port of a pattern quoted above.

### Correction to the brief
Collecct **does** already have a debounce — `page.tsx:230–233`:
```tsx
useEffect(() => {
	const t = setTimeout(() => setDebouncedQuery(query), 300);
	return () => clearTimeout(t);
}, [query]);
```
It's the right idea and it's only missing (a) the two-way sync back from committed state, (b) the
`commitRef` indirection, (c) extraction into a reusable hook. Drop in `useSearchInput` verbatim
(300 → 250ms) and delete both `query`/`debouncedQuery` state slots.

### The real problem: the list *is* replaced wholesale on every commit
`loadPage` does `setOpps(page.items)` (`page.tsx:256`) and the change effect does
`setLoading(true)` first (`page.tsx:310–315`):
```tsx
useEffect(() => {
	setLoading(true);
	loadPage();
	loadCounts();
	loadDates();
}, [loadPage, loadCounts, loadDates]);
```
Then `page.tsx:884` inserts a **layout-shifting banner above the rows**:
```tsx
{loading && <div style={{ padding: 24, color: "var(--faint)" }}>Loading…</div>}
```
So every 300ms pause while typing: a 24px-padded "Loading…" block is injected, pushing the entire
list down; the rows are then swapped for a new array; and because each row carries
`style={{ animationDelay: `${Math.min(i, 12) * 35}ms` }}` (`page.tsx:907`) the whole list
**re-runs its staggered entry animation** — up to 420ms of cascading motion per search commit.
This is the single biggest perceived-performance defect on the page.

**Ranked fixes:**

1. **Adopt TanStack Query with `placeholderData: (previous) => previous`.** This is the highest-
   leverage change on the list. It replaces `loadPage` / `loadCounts` / `loadDates` / `loadBids`
   / `loadMore` (five `useCallback`s + two `useEffect`s + ~8 `useState` slots for
   `opps`/`total`/`loading`/`loadingMore`/`counts`/`inFlight`/`availableDates`/`bidOpps`) with
   five `useQuery` calls, and makes stale rows survive a refetch for free.

2. **Never render "Loading…" above the rows.** Move it to the footer next to
   `Load more · N of M` as a small spinner, exactly like `TablePagination`. Show the empty/
   loading block only when `visible.length === 0`, in a fixed-height container (`h-32`), so the
   loading, empty, and populated states all occupy the same box. Port `data-table.tsx:480–488`
   verbatim.

3. **Gate the entry animation.** `animationDelay` should apply to the *first* mount of a row, not
   to every list replacement. Simplest fix: drop the stagger entirely once (1) lands — with
   stale rows surviving, there's no gap to paper over. If it's kept, key it so only genuinely new
   ids animate.

4. **Move filters/search/page/date/tab into the URL** (nuqs is already a dependency pattern here;
   otherwise `useSearchParams` + `router.replace`). Right now `filter`, `facets`, `query`,
   `viewingDate`, `selectedId`, `tab` are all `useState` — so a reload, a shared link, or Back
   loses the entire view. `q` should write with `history: "replace"`, page/selection with
   `"push"` (`list-search-params.ts:15–18`). This alone deletes a large fraction of the 70
   `useState`s and, because the server can then read the params, unlocks server prefetch.

5. **Reset paging inside the same commit as the filter change** — `setState({ q: next, page: 1 })`
   (`list-search.tsx:16–18`). Today `loadPage` implicitly resets offset to 0 in a *separate*
   effect pass, so there's a frame where the old offset and new filters disagree.

6. **Prefetch the detail on row hover and focus.** `detailLoading` / `selectedOpp`
   (`page.tsx:225–226`) means clicking a row always waits on a request. Add
   `onMouseEnter` **and** `onFocus` → `queryClient.prefetchQuery(opportunity(id))`
   (`data-table.tsx:512–515` + `record-prefetch.ts`). By the time the click lands the sheet is
   warm and `detailLoading` never fires.

7. **Replace the in-flight `setInterval` poll with `refetchInterval` as a function of the data.**
   `page.tsx:328–330` arms a `setInterval` from `inFlight`. `refetchInterval: (q) =>
   q.state.data?.items.some(isIngesting) ? 3000 : false` (`companies-table.tsx:151–156`) is
   self-arming, self-disarming, cleanup-free, and survives a re-render without restarting.

8. **`tabular-nums` on `P<b>{o.priority_score}</b>`, `money(...)`, and `N of M`**
   (`page.tsx:944`, `:948`, `:956`). These update under polling and currently jitter.

9. **Fix the row grid.** Rows are flex with `flex: 1, minWidth: 0` (`page.tsx:925`) — column
   positions depend on title length, so every list swap reshuffles the meta row. Give the row a
   fixed grid template with explicit column widths, `truncate`, and `overflow-hidden`
   (`contacts-table.tsx` column widths + `data-table.tsx:425` `table-fixed`).

10. **Add a `useMountEffect` hook and a Biome ban on `useEffect`.** 17 effects in one file is
    where the bugs live. Note Collecct should *actually wire the lint rule* — the CRM's own gap
    (§6) is proof that a documented convention without enforcement drifts at exactly the newest
    feature.

11. **Auth-gate via redirect, not effect.** `page.tsx:126–130` does
    `useEffect(() => { if (!isInitializing && !user) window.location.href = "/auth/login" })`.
    That's a full document navigation from a client effect — it guarantees a flash of the splash.
    Do it in middleware or a server component (`requireSession()` in
    `contacts/page.tsx:28` / `(app)/layout.tsx:14`) and the unauthenticated user never downloads
    the console bundle at all.

12. **Add a ⌘K switcher** — 11 lines (`quick-switcher.tsx:39–49`) with the open state in the URL,
    `placeholderData` on the results, `shouldFilter={false}`, and a `>= 2` char gate. Highest
    perceived-speed-per-line-of-code item on this list.

13. **Redirect sheet/modal open-focus to the scroll container** (`detail-sheet.tsx:72–75`) rather
    than letting the first button take the ring.

14. **Two-tier invalidation.** After `setDecision` / `approveCapture`, await only the refetch of
    the record the user is looking at and fire the list/counts/dots refreshes with `void`
    (`cache.ts:31–46`, `settle: "record"`). Today the button stays pending until every
    dependent load finishes.
