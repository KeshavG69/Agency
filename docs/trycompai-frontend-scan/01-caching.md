# FE Notes 01 — Caching & Data Layer

Source: `/private/tmp/claude-501/.../scratchpad/crm` (Comp AI CRM, open-source).
Target: **Collecct** (Next.js 15.5 + React 19, plain `useState` + `axios` via `apiClient`, no TanStack Query, no nuqs).

All file refs are absolute-within-repo, rooted at `apps/app/` unless stated.

---

## 0. Stack and versions

`apps/app/package.json:13-42`

```json
"@tanstack/react-query": "^5.101.2",
"@trpc/client": "^11.18.0",
"@trpc/server": "^11.18.0",
"@trpc/tanstack-react-query": "^11.18.0",
"next": "16.2.12",
"nuqs": "^2.8.9",
"react": "19.2.4",
"react-dom": "19.2.4",
"sonner": "^2.0.7"
```

Notable: this is the **new** `@trpc/tanstack-react-query` proxy API (`trpc.x.y.queryOptions()` / `.queryKey()` / `.pathKey()` / `.mutationOptions()`), *not* the legacy `createTRPCReact` hooks. That matters: every query is an ordinary `useQuery(options)` call, so all TanStack knobs (`placeholderData`, `refetchInterval`, `enabled`, `staleTime`) are available by spreading.

Provider tree — `apps/app/app/layout.tsx:49-56`:

```tsx
<NuqsAdapter>
  <TRPCReactProvider>
    <ThemeProvider>
      <TooltipProvider>{children}</TooltipProvider>
      <Toaster richColors />
    </ThemeProvider>
  </TRPCReactProvider>
</NuqsAdapter>
```

`NuqsAdapter` wraps `TRPCReactProvider` — URL state is the outermost source of truth, and the query layer reads from it.

---

## 1. The QueryClient config

`apps/app/lib/trpc/query-client.ts:1-27` — the whole file:

```ts
import {
	defaultShouldDehydrateQuery,
	QueryClient,
} from "@tanstack/react-query";

export function makeQueryClient(): QueryClient {
	return new QueryClient({
		defaultOptions: {
			queries: { staleTime: 30_000 },
			dehydrate: {
				shouldDehydrateQuery: (query) =>
					defaultShouldDehydrateQuery(query) ||
					query.state.status === "pending",
			},
		},
	});
}

let browserQueryClient: QueryClient | undefined;

export function getQueryClient(): QueryClient {
	if (typeof window === "undefined") {
		return makeQueryClient();
	}
	browserQueryClient ??= makeQueryClient();
	return browserQueryClient;
}
```

### What is set, and what is deliberately left at default

| Option | Value | Effect |
|---|---|---|
| `staleTime` | **30_000** (30s) | For 30s after a fetch, a query is *fresh*: remounting a component, navigating back to a page, or focusing the window will **not** refetch. This is the single biggest "never waits for the network" lever. Cross-referenced in the repo's own docs (`docs/api.md:500`: *"`staleTime` is 30 seconds"*). |
| `gcTime` | not set → **5 min** default | Unmounted query data survives 5 minutes. Open a record sheet, close it, reopen within 5 min → instant, from cache. |
| `retry` | not set → **3 retries, exponential backoff** (capped 30s) | Transient 5xx / network blips self-heal without a visible error. |
| `refetchOnWindowFocus` | not set → **true** | But gated by `staleTime`, so a tab-back within 30s is free; after 30s it silently revalidates in the background while showing cached data. |
| `refetchOnMount` | not set → **true** | Same gating. |
| `refetchOnReconnect` | not set → **true** | |
| `structuralSharing` | default **true** | Refetches that return identical JSON produce the *same object references*, so React bails out of re-rendering rows that did not change. Free render-cost win on the 25-row tables. |

Only two overrides exist globally. Everything else is TanStack defaults. The per-query overrides (§3, §6) are the exceptions, and they are few — the discipline is "one global rule, narrow exceptions".

### The singleton split (SSR-safe)

`getQueryClient()` returns a **new** client per call on the server (`typeof window === "undefined"`), and a **module-level singleton** in the browser. This is the canonical Next.js App Router pattern:

- Server: a fresh client per request → no cross-request cache bleed between users. (Note the server *page* path uses a different accessor, `getServerQueryClient`, see §2.)
- Browser: one client for the whole SPA lifetime → cache survives client-side navigation between `/companies`, `/contacts`, `/deals`. This is why moving between tabs is instant on the second visit.

`??=` rather than `if (!x) x = ...` also means it survives Fast Refresh in dev without dropping the cache.

### `shouldDehydrateQuery` including `pending` — exactly what it buys

```ts
shouldDehydrateQuery: (query) =>
	defaultShouldDehydrateQuery(query) ||
	query.state.status === "pending",
```

`defaultShouldDehydrateQuery` serialises only queries in state `success`. This override adds queries still **in flight** at the moment the RSC finishes rendering.

Why that matters, mechanically:

1. A server component fires `void queryClient.prefetchQuery(...)` (deliberately **not** awaited — §2).
2. The RSC's JSX resolves and Next starts streaming HTML. The prefetch promise is still unresolved.
3. `dehydrate(getServerQueryClient())` runs. With the default predicate, that in-flight query is **absent** from the dehydrated payload → the browser mounts, sees no cache entry, and issues its **own** HTTP request from scratch. The server's work is thrown away and the user waits for a full second round trip.
4. With the override, the pending query **is** dehydrated — React Query streams the promise (React 19 + Next's streaming SSR serialise it), and when the server-side fetch resolves, the result is pushed into the already-hydrated browser cache. The client component's `useQuery` transitions from pending → success **without ever opening a socket**.

So the override is what makes `void prefetchQuery` actually *worth* doing. Without it, the `void`-ed prefetches would be pure waste (server does the work, client redoes it). With it, you get the ideal shape: **the page shell streams immediately, and secondary data lands via the streamed promise rather than a client fetch.**

Concretely on `/contacts` this converts three would-be client requests (`contacts.list`, `users.list`, `companies.options`) into zero.

---

## 2. Server prefetch → hydrate: the `await` vs `void` split

### The plumbing

`apps/app/lib/trpc/server.ts:1-32` — the whole file:

```ts
import "server-only";
import { createTRPCClient, httpBatchLink, type TRPCClient } from "@trpc/client";
import {
	createTRPCOptionsProxy,
	type TRPCOptionsProxy,
} from "@trpc/tanstack-react-query";
import type { AppRouter } from "api/app-router";
import { cookies } from "next/headers";
import { cache } from "react";
import { API_URL } from "@/lib/env";
import { makeQueryClient } from "./query-client";

export const getServerQueryClient = cache(makeQueryClient);

export function getServerTrpc(): TRPCOptionsProxy<AppRouter> {
	const client = createTRPCClient<AppRouter>({
		links: [
			httpBatchLink({
				url: `${API_URL}/api/trpc`,
				headers: async () => {
					const cookie = (await cookies()).toString();
					return cookie ? { cookie } : {};
				},
			}),
		],
	});

	return createTRPCOptionsProxy<AppRouter>({
		client,
		queryClient: getServerQueryClient,
	});
}
```

Two details worth stealing:

- **`cache(makeQueryClient)`** — React's `cache()` memoises per-request. So `page.tsx` and `layout.tsx` and any nested RSC all get *the same* QueryClient within one request, but different requests get different ones. That's what lets `layout.tsx` and `page.tsx` each render their own `<HydrateClient>` and have the prefetches accumulate into one dehydrated payload.
- **`headers: async () => cookies().toString()`** — the server-side tRPC client forwards the incoming session cookie so server prefetches are authenticated as the browsing user. Without this the prefetch would 401 and the client would silently refetch.

`apps/app/lib/trpc/hydrate.tsx:1-12` — the whole file:

```tsx
import "server-only";
import { dehydrate, HydrationBoundary } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { getServerQueryClient } from "./server";

export function HydrateClient({ children }: { children: ReactNode }) {
	return (
		<HydrationBoundary state={dehydrate(getServerQueryClient())}>
			{children}
		</HydrationBoundary>
	);
}
```

Nine lines. Note `HydrateClient` is used **multiple times per page** (`app/(app)/page.tsx:38` and `:48`; `app/(app)/settings/sso/page.tsx:54` and `:61`) — because `getServerQueryClient` is request-cached, each boundary dehydrates the same accumulating client, and duplicate keys are harmless (hydration is idempotent per key).

`apps/app/lib/trpc/client.tsx:27-44` (browser side):

```tsx
export function TRPCReactProvider({ children }: { children: ReactNode }) {
	const queryClient = getQueryClient();
	const [trpcClient] = useState(() =>
		createTRPCClient<AppRouter>({
			links: [httpBatchLink({ url: "/api/trpc" })],
		}),
	);
	...
}
```

`useState(() => ...)` for the tRPC client — created once, never re-created on re-render.

### The await/void split, page by page

**`app/(app)/companies/page.tsx:32-37`:**

```tsx
const trpc = getServerTrpc();
const queryClient = getServerQueryClient();
await queryClient.prefetchQuery(
	trpc.companies.list.queryOptions(companiesSearchParams.toInput(values)),
);
void queryClient.prefetchQuery(trpc.users.list.queryOptions());
```

**`app/(app)/contacts/page.tsx:32-40`:**

```tsx
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

**`app/(app)/deals/page.tsx:32-40`** — identical shape: `await deals.list`, `void users.list`, `void companies.options`.

**`app/(app)/page.tsx` (Overview) `:26-32`:**

```tsx
const trpc = getServerTrpc();
const queryClient = getServerQueryClient();

await Promise.all([
	queryClient.prefetchQuery(trpc.users.me.queryOptions()),
	queryClient.prefetchQuery(trpc.dashboard.summary.queryOptions({ scope })),
]);
```

**`app/(app)/settings/page.tsx:27-32`** — all four awaited in a `Promise.all`:
`workspace.get`, `settings.agentModel`, `settings.modelCatalog`, `settings.researchKey`.

**`app/(app)/settings/members/page.tsx:33-38`** — `Promise.all([workspace.get, workspace.members])`, both awaited.

**`app/(app)/settings/sso/page.tsx:35-40`** — `Promise.all([sso.settings, sso.list])`, both awaited.

**`app/(app)/settings/connections/page.tsx:29-32`** — a nice trick, awaiting the *searchParams promise* concurrently with the prefetch:

```tsx
const [{ error }] = await Promise.all([
	searchParams,
	queryClient.prefetchQuery(trpc.google.status.queryOptions()),
]);
```

### The rule, extracted

> **`await` the query whose absence would render an empty page. `void` everything that only fills in chrome.**

- **Awaited** = the *primary payload* the page exists to show. `companies.list`, `contacts.list`, `deals.list`, `dashboard.summary`, `users.me` (the greeting), `workspace.members`, `sso.list`, `google.status`. If these were `void`-ed, the HTML would stream with an empty table and the user would see a flash of "no results" before hydration filled it. Awaiting means **the first HTML byte already contains the rows** — SEO-irrelevant here, but perceptually it means zero loading state on a cold load or a shared link.
- **`void`-ed** = *secondary/derived* data that only populates filter dropdowns and avatars. `users.list` (owner facet options), `companies.options` (company facet options). The table renders correctly without them — the facet menus just show fewer options for a few hundred ms. Blocking the whole page's HTML on them would be a net loss.

Crucially the `void`-ed ones are **not** wasted, because of the `pending` dehydrate override (§1). They are streamed. So the split is not "prefetch vs don't" — it is **"block the HTML on it, or stream it"**. Three tiers, effectively:

1. `await` → in the first HTML byte, zero client fetch.
2. `void` + pending-dehydrate → not in the first byte, but streamed in, still zero client fetch.
3. Nothing → client fetch on mount.

Note that `Promise.all` is used everywhere two or more queries are awaited — never sequential `await`s. All server-side prefetches also go through a single `httpBatchLink`, so concurrent prefetches within a tick collapse into **one HTTP POST** to the API (§6).

### The nuqs → prefetch bridge

The awaited prefetch is fed by URL state parsed **on the server**:

`app/(app)/companies/page.tsx:30`:
```tsx
const values = await companiesSearchParams.load(searchParams);
```
then `:34-36` `trpc.companies.list.queryOptions(companiesSearchParams.toInput(values))`.

And the client component derives the *same* input from the *same* parsers — `components/data-table/use-table-query.ts:22` + `:59`:

```ts
const [state, setState] = useQueryStates(parsers);
...
return { query, input: toInput(values) };
```

Because `toInput` is a pure function shared by both sides (`components/data-table/list-search-params.ts:97-111`), the server's prefetch key and the client's `useQuery` key are **byte-identical**. That is the whole trick — if the shapes drifted by one field, the cache would miss and every page load would double-fetch. Sharing one `toInput` makes the match structural rather than accidental.

`docs/crm-plan.md:928-930` states the intent:
> *"Parsed nuqs values feed the prefetch, so the first paint is already filtered and sorted — no loading flash on a shared link. Subsequent filter changes stay shallow and are served by TanStack Query."*

---

## 3. `placeholderData: (previous) => previous` — every occurrence

Seven sites. All of them are on a query whose **key changes as the user manipulates the view**.

| File:line | Query | Key varies by |
|---|---|---|
| `app/(app)/companies/companies-table.tsx:150` | `companies.list` | q, sort, dir, page, owner, industry, enrichment |
| `app/(app)/contacts/contacts-table.tsx:122` | `contacts.list` | q, sort, dir, page, owner, company |
| `app/(app)/deals/deals-table.tsx:131` | `deals.list` | q, sort, dir, page, owner, stage, closing, status tab |
| `app/(app)/settings/members/members-table.tsx:143` | `workspace.members` | q, sort, dir, page, role |
| `app/(app)/settings/sso/sso-table.tsx:136` | `sso.list` | q, sort, dir, page |
| `app/(app)/dashboard-summary.tsx:61` | `dashboard.summary` | `scope` toggle (me / everyone) |
| `components/crm/quick-switcher.tsx:54` | `search.quick` | the typed query string, per keystroke (debounced upstream) |

Canonical form — `app/(app)/companies/companies-table.tsx:148-157`:

```tsx
const companies = useQuery({
	...trpc.companies.list.queryOptions(input),
	placeholderData: (previous) => previous,
	refetchInterval: (query) =>
		query.state.data?.rows.some((row) =>
			isEnriching(row.enrichmentStatus, row.queued),
		)
			? ENRICHMENT_POLL_MS
			: false,
});
```

### What it prevents

Without it: changing sort/page/filter mints a **new cache key**. TanStack has no data for that key, so `data` becomes `undefined` and `isPending` becomes `true`. The table's `rows={companies.data?.rows ?? []}` collapses to `[]`. You get:

- the table **collapsing to zero height**, then snapping back → layout shift, scroll position lost
- the empty-state copy ("No companies match this view.") flashing for ~150ms on every page change
- focus loss on the sort header the user just clicked

With `(previous) => previous`: the *old key's* data is handed over as placeholder for the *new* key. `data` stays populated across the transition, `isPending` stays `false`, and instead `isPlaceholderData` / `isFetching` go true. The table keeps showing the previous page's rows, dimmed/spinner'd, until the new ones arrive. **This is TanStack v5's replacement for v4's `keepPreviousData: true`.**

The tables express the loading state exclusively through `isFetching`, never `isPending` — `companies-table.tsx:200`, `contacts-table.tsx:164`, `deals-table.tsx:181`, `members-table.tsx:182`, `sso-table.tsx:161`:

```tsx
loading={companies.isFetching}
```

That is the deliberate pairing: `placeholderData` keeps `data` populated, so the *only* honest signal left is `isFetching`, which drives a subtle spinner rather than a skeleton swap.

Reinforced downstream in the UI package — `packages/ui/src/components/data-table.tsx:192`:

```tsx
const deferredRows = useDeferredValue(rows);
```

React 19 `useDeferredValue` on top of `placeholderData`: even the *swap itself* is deprioritised, so typing in the search box never blocks input on re-rendering 25 rows. Two independent layers of "don't jank".

The quick-switcher is the most aggressive use — `components/crm/quick-switcher.tsx:51-55`:

```tsx
const results = useQuery({
	...trpc.search.quick.queryOptions({ q: query }),
	enabled: open && query.trim().length >= 2,
	placeholderData: (previous) => previous,
});
```

`enabled` gates on ≥2 chars (no request for "a"), and `placeholderData` means the command palette's result list never blanks between keystrokes — it shows the previous matches until the new ones land. Combined with `staleTime: 30s`, backspacing to a previously-typed prefix is served **entirely from cache, zero network**.

Search input debounce lives one layer up — `packages/ui/src/hooks/use-search-input.ts:22-26`:

```ts
useEffect(() => {
	if (value === committedRef.current) return;
	const timer = setTimeout(() => commitRef.current(value), delayMs);
	return () => clearTimeout(timer);
}, [value, delayMs]);
```

250ms default. So: keystroke → local state (instant) → 250ms debounce → nuqs URL write → new query key → `placeholderData` holds the old rows → fetch. Four layers between a keypress and a spinner.

---

## 4. Row-hover prefetch (`usePrefetchRecord`)

`components/crm/record-sheet/record-prefetch.ts:1-32` — the whole file:

```ts
"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useCallback } from "react";
import { useTRPC } from "@/lib/trpc/client";
import type { RecordRef } from "./record-stack";

export function usePrefetchRecord() {
	const trpc = useTRPC();
	const queryClient = useQueryClient();

	return useCallback(
		({ kind, id }: RecordRef) => {
			switch (kind) {
				case "company":
					void queryClient.prefetchQuery(
						trpc.companies.byId.queryOptions({ id }),
					);
					return;
				case "contact":
					void queryClient.prefetchQuery(
						trpc.contacts.byId.queryOptions({ id }),
					);
					return;
				case "deal":
					void queryClient.prefetchQuery(trpc.deals.byId.queryOptions({ id }));
					return;
			}
		},
		[trpc, queryClient],
	);
}
```

### How it is triggered

Wired at the table level — `app/(app)/companies/companies-table.tsx:201-202`:

```tsx
onRowHover={(row) => prefetchRecord({ kind: "company", id: row.id })}
onRowClick={(row) => openRecord({ kind: "company", id: row.id })}
```

Same pair at `contacts-table.tsx:165-166` and `deals-table.tsx:182-183`. Not present on `members-table` / `sso-table` (those rows open nothing).

The DOM wiring is in the shared table — `packages/ui/src/components/data-table.tsx:510-515`:

```tsx
<TableRow
	onClick={clickable ? handleClick : undefined}
	onMouseEnter={
		onRowHover ? () => onRowHover(row) : undefined
	}
	onFocus={onRowHover ? () => onRowHover(row) : undefined}
```

**`onFocus` as well as `onMouseEnter`** — keyboard tabbing through rows prefetches too. That's the accessibility-equivalent of hover, and it's easy to forget.

### Why it works so well here

The click target opens a **sheet, not a route** — `useOpenRecord` writes to a nuqs param (`record-stack.ts:75-84`) which `RecordSheetHost` (`record-sheet-host.tsx:11-43`) reads to mount `<CompanySheet companyId={...}>`. The sheet then calls `useQuery(trpc.companies.byId...)` with the **same key** the hover already warmed.

Timing: hover → mouse travel + click ≈ 150-400ms; the `byId` fetch is typically well inside that. So by the time the sheet's mount animation starts, `query.isPending` is already `false` and `RecordSheetFrame loading={query.isPending}` (`company-sheet.tsx:253`) never shows its spinner. The sheet appears fully populated. **That is the single most "it never waits" moment in the app.**

`prefetchQuery` is a no-op if the data is already fresh (`staleTime` 30s), so re-hovering the same row repeatedly costs nothing — no debounce needed. And `void` on the promise means hover handlers never block.

Complementary: `record-stack.ts` keeps a **stack** of open records in one URL param (`record=company:abc,contact:def`), so drilling company → contact → back is pure URL manipulation over an already-warm cache. Zero network on the way back.

---

## 5. Invalidation — `apps/app/lib/trpc/cache.ts` in full

This is the most interesting file in the repo. Reproduced complete (`apps/app/lib/trpc/cache.ts:1-188`):

```ts
"use client";

import { type QueryKey, useQueryClient } from "@tanstack/react-query";
import { useTRPC } from "./client";

type Settle = "all" | "record";

type Options = {
	settle?: Settle;
};

type RemovedRecord = { kind: "company" | "contact" | "deal"; id: string };

export type CrmCache = {
	company(id?: string, options?: Options): Promise<void>;
	contact(id?: string, options?: Options): Promise<void>;
	deal(id?: string, options?: Options): Promise<void>;
	removed(record: RemovedRecord): Promise<void>;
	activity(options?: Options): Promise<void>;
	google(options?: Options): Promise<void>;
	settings(options?: Options): Promise<void>;
	workspace(options?: Options): Promise<void>;
	sso(options?: Options): Promise<void>;
	everything(): Promise<void>;
};

export function useCrmCache(): CrmCache {
	const trpc = useTRPC();
	const queryClient = useQueryClient();

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

	const activityKeys = () => [
		trpc.activities.timeline.pathKey(),
		trpc.activities.timelineCounts.queryKey(),
		trpc.activities.myTasks.queryKey(),
	];

	const listKeys = () => [
		trpc.companies.list.queryKey(),
		trpc.contacts.list.queryKey(),
		trpc.deals.list.queryKey(),
		trpc.search.quick.queryKey(),
	];

	return {
		company: (id, options) =>
			run(
				[
					id
						? trpc.companies.byId.queryKey({ id })
						: trpc.companies.byId.queryKey(),
				],
				[
					...listKeys(),
					trpc.contacts.byId.queryKey(),
					trpc.deals.byId.queryKey(),
					trpc.dashboard.summary.queryKey(),
				],
				options,
			),

		contact: (id, options) =>
			run(
				[
					id
						? trpc.contacts.byId.queryKey({ id })
						: trpc.contacts.byId.queryKey(),
				],
				[
					...listKeys(),
					trpc.companies.byId.queryKey(),
					trpc.deals.byId.queryKey(),
				],
				options,
			),

		deal: (id, options) =>
			run(
				[id ? trpc.deals.byId.queryKey({ id }) : trpc.deals.byId.queryKey()],
				[
					...listKeys(),
					trpc.companies.byId.queryKey(),
					...activityKeys(),
					trpc.dashboard.summary.queryKey(),
				],
				options,
			),

		removed: ({ kind, id }) => {
			const goneKey = {
				company: trpc.companies.byId,
				contact: trpc.contacts.byId,
				deal: trpc.deals.byId,
			}[kind].queryKey({ id });
			const gone = JSON.stringify(goneKey);

			for (const record of [
				trpc.companies.byId,
				trpc.contacts.byId,
				trpc.deals.byId,
			]) {
				void queryClient.invalidateQueries({
					queryKey: record.queryKey(),
					predicate: (query) => JSON.stringify(query.queryKey) !== gone,
				});
			}

			void queryClient.invalidateQueries({
				queryKey: goneKey,
				exact: true,
				refetchType: "none",
			});

			return run(
				[...listKeys(), ...activityKeys(), trpc.dashboard.summary.queryKey()],
				[],
			);
		},

		activity: (options) =>
			run(
				activityKeys(),
				[
					...listKeys(),
					trpc.companies.byId.queryKey(),
					trpc.contacts.byId.queryKey(),
					trpc.deals.byId.queryKey(),
					trpc.dashboard.summary.queryKey(),
				],
				options,
			),

		google: (options) =>
			run(
				[trpc.google.status.queryKey()],
				[
					...activityKeys(),
					...listKeys(),
					trpc.companies.byId.queryKey(),
					trpc.contacts.byId.queryKey(),
					trpc.dashboard.summary.queryKey(),
				],
				options,
			),

		settings: (options) =>
			run(
				[
					trpc.settings.agentModel.queryKey(),
					trpc.settings.researchKey.queryKey(),
				],
				[],
				options,
			),

		workspace: (options) =>
			run(
				[trpc.workspace.get.queryKey(), trpc.workspace.members.queryKey()],
				[],
				options,
			),

		sso: (options) =>
			run(
				[trpc.sso.list.pathKey()],
				[trpc.sso.settings.queryKey(), trpc.sso.signInOptions.queryKey()],
				options,
			),

		everything: () => queryClient.invalidateQueries(),
	};
}
```

### 5a. The central abstraction: name *what changed*, not *which keys*

Every call site says `cache.deal(id)` / `cache.company(id)` / `cache.activity()`. **No component ever writes a query key.** 28 call sites across the app, one fan-out table.

The repo documents the failure this fixes — `docs/api.md:482-489`:

> *"Invalidate on the client, in the mutation's `onSuccess` — but through `useCrmCache()` … Say what changed … and the module owns the fan-out. Twelve hand-written key lists is how they drifted: a stage change did not refresh the timeline entry it writes, creating a deal did not refresh the board, and nothing refreshed the overview, so a rep could close a deal and watch their own numbers not move. **A new mutation adds a call there, not a new list of keys.**"*

This is the single highest-leverage idea in the file. In a CRM the entity graph is dense — a deal's stage change touches the deal, the company's open-deal count, the dashboard's pipeline total, the timeline (a stage-change activity row is written), and the search index. A per-component key list cannot stay correct.

### 5b. The record-vs-rest split and `settle: "all" | "record"`

`run(record, rest, options)` takes **two** key lists:

- **`record`** — the thing the user was directly manipulating (its `byId`).
- **`rest`** — everything else that transitively went stale (lists, counters, dashboard, sibling records).

Then:

```ts
const awaited = settle === "all" ? [...record, ...rest] : record;
const behind  = settle === "all" ? []                  : rest;

for (const queryKey of behind) {
	void queryClient.invalidateQueries({ queryKey });   // fire and forget
}

return Promise.all(
	awaited.map((queryKey) => queryClient.invalidateQueries({ queryKey })),
).then(() => undefined);
```

`invalidateQueries` returns a promise that resolves **when the triggered refetches complete**. So awaiting it = "block until the screen is actually correct".

- **`settle: "all"` (default)** — the returned promise resolves only after *every* affected view has refetched. Callers `await cache.x()` before closing a dialog / firing a toast, so the user never sees "Saved!" over stale numbers.
- **`settle: "record"`** — only the edited record's refetch is awaited; the lists/dashboard/counters are invalidated `void`-style and catch up on their own.

`docs/api.md:504-509` on the intent:
> *"Pass `{ settle: "record" }` when the caller is an inline editor. The default waits for every affected view to refetch, which is right when the point of the action *is* the view changing (a card moving between board columns); `"record"` waits only for the edited record so the field's spinner clears as soon as the value under it is right, and lets the table behind the sheet catch up on its own."*

**Both branches invalidate the same keys.** `settle` changes only *what the caller waits on*. That is the elegant part: correctness is identical, only perceived latency differs.

`settle: "record"` call sites (all inline editors):
- `components/crm/record-sheet/company-sheet.tsx:344` — `companies.update`
- `components/crm/record-sheet/contact-sheet.tsx:303` — contact update
- `components/crm/record-sheet/deal-sheet.tsx:184` — deal update
- `components/crm/facts.tsx:61` — accept/dismiss an agent-suggested fact
- `app/(app)/settings/connections/google-connection.tsx:230` — `setAutoCreate` toggle

Default `settle: "all"` call sites (where the *view change is the point*): create sheets (`create-company-sheet.tsx:62`, `create-contact-sheet.tsx:63`, `create-deal-sheet.tsx:72`), stage changes (`stage-change.tsx:49`, `stage-stepper.tsx:31`), activity logging (`activity-composer.tsx:63`, `timeline-entry.tsx:51`, `dashboard-summary.tsx:66`), enrichment triggers (`enrichment-actions.tsx:26,40,86`), settings/workspace/sso saves.

The create-sheet pattern shows why the await matters — `app/(app)/companies/create-company-sheet.tsx:59-72`:

```tsx
const create = useMutation(
	trpc.companies.create.mutationOptions({
		onSuccess: async (company) => {
			await cache.company(company.id);
			toast.success(`${company.name} added.`);
			await setOpen(null);
			setName("");
			setDomain("");
			setOwnerId(UNASSIGNED);
			openRecord({ kind: "company", id: company.id });
		},
		onError: (error) => toast.error(error.message),
	}),
);
```

`await cache.company(company.id)` **before** closing the sheet and opening the record. So the sheet slides shut onto a table that already contains the new row, and the record sheet that opens is already populated. No flash, no "where did my company go?".

### 5c. `queryKey()` with no argument — the prefix trick

`trpc.companies.byId.queryKey()` (no `{id}`) produces a **partial** key — `[["companies","byId"]]` — which `invalidateQueries` matches as a prefix, hitting **every** cached `byId` regardless of id. That's how `cache.contact(id)` at line 87 invalidates *all* company `byId` entries: editing a contact can change the company's contact count and primary-contact flag, and you don't know which company without extra bookkeeping. Prefix-invalidate and let `staleTime`/mount-state decide what actually refetches (only *mounted* queries refetch by default; unmounted ones are just marked stale).

Cheap, correct, and no dependency graph to maintain.

### 5d. `pathKey()` vs `queryKey()` — the infinite-query trap

Note line 49 and line 181 use **`pathKey()`**, everything else uses `queryKey()`:

```ts
trpc.activities.timeline.pathKey(),   // :49
trpc.sso.list.pathKey(),              // :181
```

`docs/api.md:510-517` explains:

> *"An infinite query needs `pathKey()`, not `queryKey()`. tRPC stamps the query type into the key, so `queryKey()` yields `{ type: "query" }` and `infiniteQueryOptions` caches under `{ type: "infinite" }` — the two cannot partially match, and invalidating with the wrong one is silent: it reports success, refetches the sibling non-infinite queries, and leaves the infinite one stale until a reload. `pathKey()` carries no type and matches both, which is what you want whenever a procedure is read both ways (`activities.timeline` is, as a paged history and as a pinned top-ten)."*

Confirmed in the timeline — `components/crm/timeline/timeline.tsx:157-171` reads the *same* procedure two ways:

```tsx
const pinned = useQuery({
	...trpc.activities.timeline.queryOptions({
		...anchor,
		filter: "upcoming",
		limit: 10,
	}),
	enabled: tab === "all",
});

const history = useInfiniteQuery({
	...trpc.activities.timeline.infiniteQueryOptions(
		{ ...anchor, filter: historyFilter(tab) },
		{ getNextPageParam: (page) => page.nextCursor ?? undefined },
	),
});
```

A **silent** invalidation failure is the worst kind. This is the highest-value gotcha in the whole file.

### 5e. `removed()` — the one call that skips a key

```ts
removed: ({ kind, id }) => {
	const goneKey = { company: …, contact: …, deal: … }[kind].queryKey({ id });
	const gone = JSON.stringify(goneKey);

	for (const record of [trpc.companies.byId, trpc.contacts.byId, trpc.deals.byId]) {
		void queryClient.invalidateQueries({
			queryKey: record.queryKey(),
			predicate: (query) => JSON.stringify(query.queryKey) !== gone,
		});
	}

	void queryClient.invalidateQueries({
		queryKey: goneKey,
		exact: true,
		refetchType: "none",
	});

	return run([...listKeys(), ...activityKeys(), trpc.dashboard.summary.queryKey()], []);
},
```

Three distinct moves:

**(1) The `predicate` trick — "invalidate everything of this shape *except* this one".**
`invalidateQueries` has no "exclude" option. The workaround: prefix-match all three `byId` families, then filter with `predicate`, comparing `JSON.stringify(query.queryKey) !== gone`. `JSON.stringify` is a cheap structural-equality check on a small array key; it's exact because tRPC generates keys deterministically (fixed field order). Note the predicate **narrows** the `queryKey` prefix match — both must pass.

Why exclude the deleted record's own entry? Because a prefix invalidation would trigger a **refetch** of `companies.byId({id: deleted})`, and that query is *still mounted* for the duration of the sheet's close animation. The refetch 404s and the sheet renders "this record could not be loaded" on its way out — a visible error for a successful action.

**(2) `refetchType: "none"` on the deleted record.** `docs/api.md:494-503`:

> *"The deleted record's own `byId` entry is invalidated with `refetchType: "none"`, which is the one place that option is right: its query is still mounted for the moment it takes the sheet to animate shut, so a refetch asks the API for a row that no longer exists and the sheet reads the 404 as 'this record could not be loaded' on its way out. Marking it stale without refetching keeps the closing sheet quiet **and** stops the entry being served from cache — `staleTime` is 30 seconds, so leaving it untouched meant a rep who reopened the record from a stale link or the back button read half a minute of a record that no longer exists."*

So it threads a needle: **mark stale (so the back button can't serve a ghost) without refetching (so the closing sheet can't 404).** Note `exact: true` here so it hits *only* that entry.

**(3) The widest fan-out in the file, all awaited.** `run([...listKeys(), ...activityKeys(), dashboard.summary], [])` — everything in the `record` slot, `rest` empty, so *everything* is awaited. Justified because "deleting is rare and touches more than any edit does" (`docs/api.md:502-503`). A deletion invalidates sibling records too (a colleague list, a deal's attendee list) — hence step (1) hitting all three `byId` families, not just the deleted kind.

Call site — `components/crm/record-sheet/record-actions.tsx:45-54`:

```tsx
const handlers = {
	onSuccess: (deleted: { name: string }) => {
		toast.success(
			`${deleted.name || `The ${NOUN[record.kind]}`} was deleted.`,
		);
		void cache.removed(record);
		close();
	},
	onError: (error: { message: string }) => toast.error(error.message),
};
```

Interesting: `void cache.removed(record)` — *not* awaited here, and `close()` fires immediately. A delete is the one action where you want the sheet gone **now**; the correctness work happens behind the animation.

### 5f. NO optimistic updates — verified

I grepped the entire repo:

```
grep -rn "onMutate\|onSettled" --include="*.tsx" --include="*.ts" apps/app  →  0 results
grep -rn "setQueryData\|cancelQueries"                                       →  0 results
```

**Zero `onMutate`. Zero `onSettled`. Zero `setQueryData`. Zero `cancelQueries`.** There is not one optimistic update in the application. (`docs/crm-plan.md:1214` mentions "an optimistic row patch" as a plan; the shipped code does not do it.)

Every mutation is the same three-line shape:

```tsx
useMutation(trpc.x.y.mutationOptions({
  onSuccess: async (...) => { await cache.z(id); toast.success("…"); },
  onError:  (error) => toast.error(error.message),
}))
```

#### What they do *instead*

**(i) `pending variables` as a scoped optimistic UI.** `components/crm/inline-field.tsx:25-33`:

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

and `:65`:

```ts
const shown = saving ? draft.trim() : (value ?? "");
```

While the mutation is in flight, the field renders the **local draft** plus a spinner (`:99`); once it settles, it renders the server value. TanStack exposes `mutation.variables` during `isPending`, so `savingField` derives *which specific fields* are being saved from the mutation payload itself — no parallel state. Used at `company-sheet.tsx:352` (`const isSaving = savingField(update)`).

This is optimistic *display* without optimistic *cache writes*. Same perceived latency, none of the rollback machinery.

**(ii) `settle: "record"`** (§5b) so the field's spinner clears the instant the record is right, without waiting on the table behind the sheet.

**(iii) `placeholderData`** (§3) so lists never blank during the post-mutation refetch.

#### Why this is arguably better

1. **A CRM's write is not a pure function of its input.** `deals.setStage` writes an activity row, bumps `lastActivityAt`, recomputes the company's `openDealCount`, moves the dashboard pipeline total, may fire enrichment. An optimistic patch would have to reimplement all of that server logic in the browser — and the reimplementation drifts from the server the first time a business rule changes. The bug it produces is the nastiest kind: the UI is confidently wrong until you reload.

2. **The rollback path is where optimistic UIs actually break.** `onMutate` + `cancelQueries` + snapshot + `onError` restore + `onSettled` invalidate is ~20 lines *per mutation*, each with its own race (two concurrent edits to the same record; a rollback restoring a snapshot taken before a *different* successful mutation). Multiply by the ~28 mutation sites here.

3. **Their latency budget doesn't need it.** With `staleTime: 30s` + hover prefetch + `placeholderData` + `httpBatchLink`, the only thing the user waits on is the mutation round trip itself (~100-200ms local), during which `savingField` already shows the new value. Optimistic caching would buy maybe 100ms on an interaction that already looks instant.

4. **Server truth arrives with the refresh.** After `cache.deal(id)` the numbers on screen are the server's, not a guess. In a CRM where a rep reads a dollar figure off the dashboard and repeats it on a call, "eventually correct" is not acceptable and "confidently wrong for 400ms" is worse than "correct 400ms later".

5. **The complexity budget went somewhere better.** The ~250 lines of `onMutate` plumbing they didn't write became the 188-line `cache.ts` that guarantees *no view is ever forgotten*. Correct-and-fast beats fast-and-maybe-correct.

**Where it costs them:** offline/high-latency. On a 500ms connection, ticking a task checkbox on the dashboard (`dashboard-summary.tsx:196-198`) shows a disabled checkbox for half a second before the row disappears. That's the trade they accepted.

---

## 6. Dedup, batching, retry, polling

### Batching / dedup

- **`httpBatchLink`** on both clients — `client.tsx:31` (`url: "/api/trpc"`) and `server.ts:18` (`url: ${API_URL}/api/trpc`). Every tRPC call issued in the same tick collapses into **one HTTP POST**. On `/contacts` the client's three queries (`contacts.list`, `users.list`, `companies.options`) are one request, not three. On the server the awaited + `void`-ed prefetches likewise batch.
- **TanStack request dedup** is structural: two components calling `useQuery` with the same key share one in-flight promise. `trpc.users.list.queryOptions()` is called from `companies-table.tsx:158`, `contacts-table.tsx:124`, `deals-table.tsx:133`, `company-sheet.tsx:340`, `create-company-sheet.tsx:57` — one fetch, five consumers.
- **`prefetchQuery` is a no-op on fresh data**, so repeated row hovers cost nothing (§4).
- The Next API route is a **transparent streaming proxy** — `app/api/[...path]/route.ts:14-95`. It strips hop-by-hop headers, forwards `Set-Cookie` via `getSetCookie()`, and passes `text/event-stream` straight through unbuffered (`:79-85`) for the agent stream. No response caching layer at all; `docs/api.md:479` — *"There is no HTTP response cache in front of tRPC. Freshness is TanStack Query's job."*

### Retry

Never configured anywhere. TanStack default: **3 retries**, exponential backoff `min(1000 * 2^n, 30000)`, queries only (mutations default to 0 retries — correct, you don't want a double-create).

### Polling — four independent `refetchInterval` sites, all self-terminating

All four use the **function form** of `refetchInterval`, which is re-evaluated after every fetch and returns `false` to stop. No `setInterval`, no cleanup, no "am I still mounted" checks.

**(a) The Agent tab — 3000ms, stops when the thread stops working.**
`components/crm/agent-panel.tsx:118`:
```ts
const WORKING_POLL_MS = 3000;
```
`components/crm/agent-panel.tsx:131-152`:
```tsx
const archive = useQuery({
	...trpc.conversations.events.queryOptions({ id: conversation?.id ?? "" }),
	enabled: conversation !== null,
	staleTime: Number.POSITIVE_INFINITY,
});

const thread = useQuery<ThreadState>({
	queryKey: ["agent-thread", conversation?.sessionId],
	enabled: conversation !== null && !archive.isPending,
	staleTime: 0,
	refetchOnMount: "always",
	refetchOnWindowFocus: false,
	refetchInterval: (query) =>
		query.state.data?.status === "working" ? WORKING_POLL_MS : false,
	queryFn: ({ signal }) =>
		loadThread(
			conversation?.sessionId ?? "",
			recordHeader(record),
			(archive.data ?? []) as never,
			signal,
		),
});
```

Interval **3000ms**; stop condition **`query.state.data?.status !== "working"`**.

This is the most interesting query in the app — a **two-tier** design:

- `archive` (the immutable transcript of a finished conversation) → **`staleTime: Number.POSITIVE_INFINITY`**. Fetched once, cached forever. Past messages can't change, so never revalidate them. This is the *only* infinite staleTime in the codebase and it's exactly right.
- `thread` (the live head) → **`staleTime: 0` + `refetchOnMount: "always"` + `refetchOnWindowFocus: false`**. Always refetch on mount (an agent may have progressed while the tab was closed), poll while working, but **don't** refetch on window focus — the 3s poll already covers liveness and a focus-triggered refetch would just double up.
- `enabled: conversation !== null && !archive.isPending` — sequences them: the live thread doesn't load until the archive it's built on top of is present (`archive.data` is passed into `loadThread`).
- The `queryFn` takes `{ signal }` and forwards it, so an in-flight poll is **aborted** when the query unmounts or is superseded.
- It's a **hand-written `queryKey: ["agent-thread", conversation?.sessionId]`** — the one place they leave the tRPC proxy, because the data is assembled client-side from the archive plus the live bridge.

Also note the panel is mounted with `keepMounted: true` on its tab (`company-sheet.tsx:246`, `contact-sheet.tsx:131`) — so switching to the Overview tab does not kill an in-progress agent run; the poll keeps going.

**(b) Companies table — 3000ms while any row is enriching.**
`app/(app)/companies/companies-table.tsx:151-156`:
```tsx
refetchInterval: (query) =>
	query.state.data?.rows.some((row) =>
		isEnriching(row.enrichmentStatus, row.queued),
	)
		? ENRICHMENT_POLL_MS
		: false,
```

**(c) Company sheet — 3000ms while that record is enriching.**
`components/crm/record-sheet/company-sheet.tsx:170-178`:
```tsx
const query = useQuery({
	...trpc.companies.byId.queryOptions({ id: companyId }),
	refetchInterval: (current) => {
		const record = current.state.data;
		return record && isEnriching(record.enrichmentStatus, record.queued)
			? ENRICHMENT_POLL_MS
			: false;
	},
});
```

**(d) Contact sheet — identical.** `components/crm/record-sheet/contact-sheet.tsx:88-96`.

Shared predicate and constant — `components/crm/enrichment-status.tsx:48-52`:
```ts
export function isEnriching(status: EnrichmentStatus, queued = false): boolean {
	return status === "RUNNING" || (status === "PENDING" && queued);
}

export const ENRICHMENT_POLL_MS = 3_000;
```

**(e) Google connections — 5000ms while any source is syncing.**
`app/(app)/settings/connections/google-connection.tsx:200-206`:
```tsx
const status = useQuery({
	...trpc.google.status.queryOptions(),
	refetchInterval: (query) =>
		query.state.data?.sources.some((source) => isSyncing(source.status))
			? SYNC_POLL_MS
			: false,
});
```
`components/crm/sync-status.tsx:53`: `export const SYNC_POLL_MS = 5_000;`

### The rule behind all of it

`docs/api.md:523-531`:

> *"Background writes the browser cannot see — enrichment finishing, most obviously — are not invalidations at all, because no client action caused them. Poll for those: `refetchInterval` while the record's status is `PENDING`/`RUNNING`, and stop once it settles. Use `isEnriching()` and `ENRICHMENT_POLL_MS` … so the rule is one definition. **A list polls too, not just the record sheet** — the company sheet polled and the companies table did not, so a newly added company's logo and industry appeared in the sheet and stayed blank in the table behind it until a reload."*

The clean taxonomy:
- **Client caused the write** → invalidate (`useCrmCache`).
- **Server/background caused the write** → poll, with a stop condition read out of the data itself.

And a nice touch at `google-connection.tsx:237-252` — after `syncNow`, it compares a failure signature before and after the invalidation by reading `queryClient.getQueryData(trpc.google.status.queryKey())` directly, and escalates the UI copy only if the same failure persists. Reading the cache post-invalidation as a way to detect "nothing changed".

---

## 7. Translation notes for Collecct

Collecct today: Next.js 15.5 + React 19, `useState` + `axios` (`apiClient`), no TanStack Query, no nuqs. The pipeline page has ~12 `useState` hooks and manual `load*()` functions.

That shape has four structural problems, and each of the patterns above kills one:

| Problem in Collecct today | Pattern that fixes it |
|---|---|
| Every navigation refetches from zero → spinner every time | `staleTime` + browser-singleton QueryClient (§1) |
| Filter/sort/page change blanks the table | `placeholderData: (previous) => previous` (§3) |
| First paint is empty; data arrives after hydration | RSC prefetch + `HydrationBoundary` + pending-dehydrate (§1, §2) |
| After a save, some panel elsewhere is stale until reload | `useCrmCache()`-style named invalidation (§5) |

### 7.1 Adopt TanStack Query (the prerequisite)

**Requires:** `npm i @tanstack/react-query @tanstack/react-query-devtools`; a `lib/query-client.ts` (copy `query-client.ts` verbatim — it's 27 lines and version-agnostic); a `QueryClientProvider` in the root layout.

You do **not** need tRPC. The proxy API just generates keys and fetchers; with axios you write them by hand. Build a thin equivalent so keys are never inline:

```ts
// lib/query-keys.ts
export const qk = {
  opportunities: {
    all:  () => ["opportunities"] as const,
    list: (input: OppListInput) => ["opportunities", "list", input] as const,
    byId: (id?: string) => id ? ["opportunities","byId",id] as const
                              : ["opportunities","byId"] as const,
  },
  contacts: { /* … */ },
  dashboard:{ summary: () => ["dashboard","summary"] as const },
} as const;
```

The `byId(id?)` overload is what makes prefix invalidation (§5c) work.

**Buys:** dedup, caching, retry, background revalidation, `isFetching` vs `isPending`, devtools — all for free. Deletes the ~12 `useState` hooks, the `loading`/`error` booleans, the manual `useEffect` fetch, and the race conditions between overlapping `load*()` calls (currently: whichever axios response lands last wins, regardless of which request was newer — TanStack's key-scoped fetching makes that impossible).

**Cost:** one dependency (~13kB gzip), and every `load*()` becomes a `queryFn`. Migrate one page at a time; the two systems coexist fine.

### 7.2 The QueryClient config — copy it, adjust `staleTime`

Copy `query-client.ts` as-is. The only judgement call is `staleTime`. 30s suits a CRM where a colleague might be editing. For Collecct's pipeline (SAM.gov opportunities that change on a **daily** cron), 30s is if anything conservative — 60s–120s is defensible for the list, with `dashboard`/`summary` shorter.

Keep the `shouldDehydrateQuery` pending override **only if** you also adopt RSC prefetch (§7.4). Standalone it does nothing.

**Buys:** the "never waits" feeling comes ~80% from this one number. Navigating pipeline → bid → back to pipeline within 30s = zero requests.

### 7.3 `placeholderData` on the pipeline list — do this first

Highest ratio of payoff to effort in the whole document. One line:

```tsx
const opportunities = useQuery({
  queryKey: qk.opportunities.list(input),
  queryFn: ({ signal }) => apiClient.get("/opportunities", { params: input, signal })
                                    .then(r => r.data),
  placeholderData: (previous) => previous,
});
```

**Requires:** the query key must actually contain the filter/sort/page state (see §7.5). If Collecct currently keeps filters in `useState` and refetches into the *same* variable, there is no key to change and `placeholderData` has nothing to do — you'd get the behaviour for free, but also the bug where an old response overwrites a new one.

**Buys:** the memory-noted per-user faceted filters (agency / NAICS / set-aside / value / deadline) stop blanking the table on every toggle. Pair with `loading={q.isFetching}` and **never** branch on `isPending` for a list.

**Bonus, ~free:** wrap the rows in `useDeferredValue` (`packages/ui/src/components/data-table.tsx:192`) so typing in the search box doesn't block on re-rendering the table.

### 7.4 RSC prefetch + hydrate

**Requires:**
- Pipeline `page.tsx` becomes a server component that reads `searchParams`, calls the API server-side (axios works fine server-side; forward the auth cookie explicitly, exactly as `server.ts:20-23` does), seeds a request-scoped QueryClient via `cache(makeQueryClient)`, and wraps the client table in `<HydrationBoundary>`.
- A shared pure `toInput(values)` used by **both** the server prefetch and the client `useQuery`, so the keys match byte-for-byte. If they diverge, you get a guaranteed double-fetch — and it fails *silently*, just slowly. Worth a unit test asserting `serverKey === clientKey`.
- Auth: Collecct's `apiClient` presumably attaches a token from browser storage. Server-side you must read the session cookie via `next/headers` instead. **This is the main integration cost** and it's where most of the work will be.

**Buys:** a shared pipeline link (`/pipeline?agency=DOD&naics=541512`) renders **already filtered** in the first HTML byte. No spinner, no flash.

**The await/void rule for Collecct's pipeline page:**
```tsx
// the rows are the page — block on them
await queryClient.prefetchQuery({ queryKey: qk.opportunities.list(input), queryFn: … });
// facet options / owners / agency list — chrome, stream them
void queryClient.prefetchQuery({ queryKey: qk.users.list(), queryFn: … });
void queryClient.prefetchQuery({ queryKey: qk.agencies.list(), queryFn: … });
```
Always `Promise.all` when awaiting more than one. Without a batch link the `void`-ed ones are separate HTTP calls — still fine, they're concurrent and off the critical path.

**If you skip this:** everything else still works. This is the most invasive change and the one with the smallest marginal gain once §7.2/§7.3 are in. Do it last.

### 7.5 nuqs (the enabler for §7.3 and §7.4)

Not strictly required, but the CRM's prefetch story **depends** on filter state living in the URL, because that's the only thing a server component can read.

Memory note says Collecct's filters are currently `localStorage`-persisted. That's incompatible with server prefetch (the server can't see localStorage) and with sharing a filtered view. nuqs gives you: shareable links, back-button correctness, server-readable state, and a stable query key — all from the same source.

**Requires:** `npm i nuqs`, `<NuqsAdapter>` in the root layout **outside** the QueryClientProvider (`app/layout.tsx:49-56`), and a `createListSearchParams`-style factory. `components/data-table/list-search-params.ts:64-113` is a genuinely good, copyable factory — ~50 lines producing `{ parsers, load, toInput }`, with `load` for the server and `parsers` for `useQueryStates` on the client.

**Migration path if you want to keep localStorage:** hydrate the URL from localStorage once on first visit, then let the URL be authoritative. Don't run both as sources of truth.

**Also worth copying:** `record-stack.ts:30-49` — encoding a *stack* of open records into one URL param (`record=opp:123,contact:456`) so drill-down and back-navigation are pure URL edits over a warm cache. Directly applicable to Collecct's bid → contact → company drilling.

### 7.6 Hover prefetch

**Requires:** TanStack Query (§7.1) and a detail view keyed by id. ~25 lines total (`record-prefetch.ts` is 32 lines including imports).

```tsx
const prefetch = useCallback((id: string) => {
  void queryClient.prefetchQuery({
    queryKey: qk.opportunities.byId(id),
    queryFn: ({ signal }) => apiClient.get(`/opportunities/${id}`, { signal }).then(r => r.data),
  });
}, [queryClient]);

<tr onMouseEnter={() => prefetch(row.id)} onFocus={() => prefetch(row.id)} />
```

Don't forget **`onFocus`** alongside `onMouseEnter`.

**Buys:** opening a bid detail feels instantaneous. Highest perceived-quality-per-line ratio in this document — genuinely ~10 lines.

**Caveat:** if a Collecct detail endpoint is expensive (server-side enrichment, SAM.gov passthrough), hover-prefetching 25 rows as the mouse sweeps down could hammer it. Two mitigations: a ~100ms hover delay before firing, or make sure the endpoint is a cheap DB read. `staleTime` already prevents repeat fetches of the same row.

### 7.7 The invalidation module — adopt this even before TanStack

The *idea* transfers even to plain `useState`: **one module that names what changed and owns the fan-out.** Today, a Collecct save presumably calls whichever `load*()` functions the author remembered. That is exactly the "twelve hand-written key lists" failure (`docs/api.md:486-489`) — it drifts silently and produces "I closed the bid and my dashboard total didn't move".

**With TanStack**, port `cache.ts` almost verbatim, substituting entities:

```ts
export function useCollecctCache() {
  const qc = useQueryClient();

  const run = (record: QueryKey[], rest: QueryKey[], { settle = "all" } = {}) => {
    const awaited = settle === "all" ? [...record, ...rest] : record;
    const behind  = settle === "all" ? [] : rest;
    for (const k of behind) void qc.invalidateQueries({ queryKey: k });
    return Promise.all(awaited.map(k => qc.invalidateQueries({ queryKey: k })))
                  .then(() => undefined);
  };

  const listKeys = () => [
    qk.opportunities.list(), qk.contacts.list(), qk.companies.list(),
  ];

  return {
    opportunity: (id?: string, o?) => run(
      [qk.opportunities.byId(id)],
      [...listKeys(), qk.dashboard.summary(), qk.bids.list()],
      o,
    ),
    // contact, company, activity, removed, everything …
  };
}
```

**Adopt `settle: "record"` for inline editors** (Collecct's bid field edits) and the default for anything where the point *is* the list moving (creating an opportunity, changing a bid/no-bid verdict — the latter moves the pipeline board *and* the dashboard, so await both).

**Adopt `removed()` wholesale** if Collecct has delete + a detail sheet with a close animation. The `predicate` exclusion + `refetchType: "none"` combination is non-obvious and you *will* hit the "sheet 404s on its way out" bug otherwise.

**The `pathKey` trap (§5d)** only bites if you read the same endpoint both paged and infinite. If Collecct's timeline/activity feed is infinite-scrolled and also read as a "recent 10", make sure the invalidation key is the shared prefix, not the type-stamped one.

### 7.8 Skip optimistic updates

Collecct's writes are *more* server-derived than this CRM's — a bid/no-bid verdict triggers analyst scoring, a SharePoint folder creation, and a dashboard recompute. Guessing that client-side is not viable.

**Copy the substitute instead:** `savingField(mutation)` (`inline-field.tsx:25-33`) — render `mutation.variables` while `isPending`, then the server value. ~8 lines, gives you the perceived instant edit with none of the rollback machinery. Pair with `settle: "record"` so the spinner clears on the record's refetch, not the whole page's.

### 7.9 Polling for background work

Directly applicable: Collecct's daily SAM.gov ingest, the analyst batch, mail triage, and enrichment are all background writes with no client action to hang an invalidation on.

**Requires:** the list/detail payload must carry a status field the client can read (`enrichmentStatus`, `queued`). If Collecct's opportunities don't expose "analyst is scoring this", add it — the stop condition has to be derivable from the data.

```tsx
refetchInterval: (query) =>
  query.state.data?.rows.some(r => isProcessing(r.analystStatus))
    ? ANALYST_POLL_MS
    : false,
```

**Two things to copy exactly:**
1. **One shared `isProcessing()` + one shared `POLL_MS` constant** (`enrichment-status.tsx:48-52`). Duplicating the predicate is how the list and the detail view drift apart.
2. **Poll the list, not just the detail** (`docs/api.md:528-531`). The exact bug they hit — detail refreshed, list behind it stayed blank until reload — is one Collecct will hit with analyst verdicts.

The function-form `refetchInterval` returning `false` beats any `setInterval`: no cleanup, no stale-closure, no "is the component still mounted", and it stops the instant the data says so.

### 7.10 Suggested order

1. **`placeholderData` on the pipeline list** — one line, kills the worst jank. *(Needs §7.1 first, or the filter state moved into the key.)*
2. **TanStack Query + `staleTime: 30_000`** on the pipeline + bid detail. Deletes most of the 12 `useState` hooks.
3. **`useCollecctCache()`** before the mutation count grows. Retrofitting it later means auditing every call site.
4. **Hover prefetch** — ~10 lines, biggest perceived win.
5. **nuqs** for filters (unblocks 6, fixes shareable links, kills the localStorage/URL split).
6. **RSC prefetch + hydrate + pending-dehydrate** — most invasive, smallest marginal gain once 1–5 land.
7. **Polling** for analyst/enrichment status, once a status field exists on the payload.

Steps 1–4 are additive and can land independently. 5–6 are coupled.

---

## Appendix — file inventory

| File | Lines | Role |
|---|---|---|
| `apps/app/lib/trpc/query-client.ts` | 27 | staleTime 30s, pending dehydrate, SSR/browser split |
| `apps/app/lib/trpc/hydrate.tsx` | 12 | `<HydrateClient>` = `HydrationBoundary(dehydrate(serverClient))` |
| `apps/app/lib/trpc/server.ts` | 32 | `cache()`d server QueryClient, cookie-forwarding tRPC client |
| `apps/app/lib/trpc/client.tsx` | 45 | Provider, `httpBatchLink`, devtools in dev only |
| `apps/app/lib/trpc/cache.ts` | 188 | The invalidation module — `settle`, `removed()`, `predicate` |
| `apps/app/lib/trpc/types.ts` | 4 | `RouterOutputs` inference |
| `apps/app/components/crm/record-sheet/record-prefetch.ts` | 32 | Hover prefetch |
| `apps/app/components/crm/record-sheet/record-stack.ts` | 134 | Record stack in one nuqs param |
| `apps/app/components/data-table/list-search-params.ts` | 113 | Shared parser/`toInput` factory (server + client) |
| `apps/app/components/data-table/use-table-query.ts` | 60 | Client half of the same |
| `packages/ui/src/hooks/use-search-input.ts` | 29 | 250ms debounce |
| `packages/ui/src/components/data-table.tsx` | ~610 | `useDeferredValue`, `onMouseEnter`/`onFocus` hover hooks |
| `apps/app/components/crm/enrichment-status.tsx` | 57 | `isEnriching()` + `ENRICHMENT_POLL_MS = 3_000` |
| `apps/app/components/crm/sync-status.tsx` | ~55 | `isSyncing()` + `SYNC_POLL_MS = 5_000` |
| `apps/app/components/crm/agent-panel.tsx` | 548 | Two-tier agent query, 3000ms working poll |
| `apps/app/components/crm/inline-field.tsx` | ~140 | `savingField()` — pending-variables display |
| `docs/api.md:477-531` | — | The written rationale for the whole invalidation design |
