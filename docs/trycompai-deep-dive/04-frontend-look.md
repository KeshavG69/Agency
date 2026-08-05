# 04 — Frontend & the "Professional Look" (open-source CRM teardown)

Repo analysed: `scratchpad/crm` — a **Comp AI CRM** monorepo (Turborepo + Bun).
Target for porting ideas: **Collecct** (govcon BD CRM, custom Next.js/React 3-pane console).

All paths below are relative to `scratchpad/crm/`. Line refs are `file:line`.

---

## 0. Stack at a glance (what makes it feel "pro")

From `apps/app/package.json` + `apps/app/components.json` + `apps/app/app/layout.tsx`:

- **Next.js 16.2.12 / React 19.2.4**, App Router, RSC on.
- **Tailwind v4** (CSS-first, **no `tailwind.config`**). PostCSS only: `apps/app/postcss.config.mjs` re-exports `@crm/ui/postcss.config`. Theme lives entirely in `packages/ui/src/styles/globals.css` via `@theme inline`.
- **shadcn/ui**, style `"radix-nova"`, `baseColor: neutral`, `cssVariables: true`, icon library **lucide** — but almost all app chrome uses **`@carbon/icons-react`** (IBM Carbon) for a denser, more "enterprise" icon set. (`components.json`)
- **nuqs 2.8.9** — every list/filter/sheet/tab state is URL-synced (type-safe search params). This is the single biggest "it feels like a real app" lever.
- **tRPC 11 + TanStack Query 5** — server-prefetch + hydrate, so pages arrive filled (no client loading flash), and they **avoid `useEffect` for data** almost entirely.
- **next-themes** for light/dark (class strategy).
- **Geist + Geist Mono** (`next/font/google`), wired to `--font-sans` / `--font-mono`.
- **eve** SDK (`eve/react`, `eve/client`, `eve/tools`) — the agent runtime that powers the Agent tab and its streaming.
- Fonts, tabular-nums everywhere for numbers, `text-xs` as the default body size (dense, spreadsheet-like), `radius: 5px`, near-invisible shadows.

**Design signature:** small type (`text-xs`/`text-sm` dominate), 1px borders everywhere instead of heavy shadows, `bg-card` surfaces on a slightly-off `bg-background`, muted-foreground for secondary text, thin custom scrollbars, and micro-motion (view transitions, icon hover animations, row accent bars).

---

## 1. The shell / layout

### 1a. Root layout — provider stack + fonts
`apps/app/app/layout.tsx:37-60`:

```tsx
export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
	return (
		<html lang="en" suppressHydrationWarning
			className={cn(fontSans.variable, fontMono.variable, "h-full antialiased")}>
			<body className="flex min-h-full flex-col font-sans">
				<NuqsAdapter>
					<TRPCReactProvider>
						<ThemeProvider>
							<TooltipProvider>{children}</TooltipProvider>
							<Toaster richColors />
						</ThemeProvider>
					</TRPCReactProvider>
				</NuqsAdapter>
			</body>
		</html>
	);
}
```

Order matters: `NuqsAdapter` (URL state) → tRPC/Query → theme → tooltip → global `Toaster` (sonner). `suppressHydrationWarning` is required by next-themes.

### 1b. App frame — header + left icon rail + content, all in one `h-svh` column
`apps/app/app/(app)/layout.tsx:16-38`:

```tsx
return (
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
);
```

Key structural moves:
- **`h-svh` + `flex flex-col` + `min-h-0 flex-1`** on the middle row = the app never scrolls as a page; each pane owns its own scroll. This is what makes it feel like an *application* and not a *document*.
- `RecordSheetHost` and `QuickSwitcher` (⌘K) are mounted **once, globally** — record detail is a right-side sheet driven by URL, reachable from anywhere.
- Layout is an **async server component** that gates on `requireGoogleAccess()` before rendering.

### 1c. Top header (48px) — `apps/app/components/app-header.tsx:61-93`
```tsx
<header className="flex h-12 shrink-0 items-center gap-2 border-b px-3 [view-transition-name:app-header]">
	<div className="flex shrink-0 items-center gap-1">
		<Button variant="ghost" size="icon" className="md:hidden" aria-label="Open navigation"
			onClick={() => setMobileNavOpen(true)}><Menu /></Button>
		<Link href="/" aria-label="Homepage"
			className="hidden size-8 items-center justify-center text-foreground md:flex">
			<Logo className="size-5" />
		</Link>
		<Separator orientation="vertical" className="mx-1 h-5 bg-transparent" />
		<span className="min-w-0 truncate font-medium text-sm">{label}</span>
	</div>
	<div className="ml-auto flex shrink-0 items-center gap-1.5">
		<UserMenu user={user} onSignOut={...} />
	</div>
</header>
```
- Workspace label logic (`workspaceLabel`, lines 36-42) avoids "CRM CRM" — nice touch.
- The **theme toggle lives in the avatar dropdown** (`UserMenu`, lines 96-140) via `next-themes` `useTheme()` (`setTheme(isDark ? "light" : "dark")`), not a standalone switch.

### 1d. Left icon rail (56px) — `apps/app/components/app-icon-rail.tsx`
Config-driven nav (lines 34-45): array of `{ title, href, icon, match }`. Active detection is `pathname === href || (prefix && startsWith)` (lines 47-52). Desktop = vertical icon strip with tooltips; mobile = the same items in a `Sheet` drawer.

```tsx
// apps/app/components/app-icon-rail.tsx:120-131
<nav aria-label="Primary"
	className="hidden w-14 shrink-0 flex-col items-center gap-1 border-r py-3 md:flex [view-transition-name:app-rail]">
	{ITEMS.map((item) => <RailLink key={item.href} item={item} active={isActive(item, pathname)} />)}
</nav>
```
Active item styling (lines 62-66): `bg-muted text-foreground` vs default `text-muted-foreground`. Each link carries `transitionTypes={["nav-lateral"]}` to trigger a specific view-transition animation on navigation.

### 1e. Page shell — the per-page frame every list/overview uses
`apps/app/components/page-shell.tsx`. `PageShell` (lines 5-23) wraps content in a `PageTransition`, a scrolling `<main>` with responsive padding, and centers a `max-w-7xl` column:

```tsx
<main data-slot="page-shell-scroll"
	className="flex min-w-0 flex-1 flex-col overflow-y-auto px-4 pt-4 pb-4 md:px-6 md:pt-6 md:pb-6">
	<div className="mx-auto flex w-full min-w-0 max-w-7xl flex-1 flex-col gap-6">{...}</div>
</main>
```
The header uses a **2-col grid** (`grid-cols-[minmax(0,1fr)_auto]`) so title+description sit left and actions pin right, wrapping cleanly (lines 25-102). Title is `font-medium text-2xl md:text-3xl tracking-tight text-balance`. Description is `text-muted-foreground text-sm text-balance`. `PageShellContent` opens an `@container/page-content` so children can respond to *pane* width, not viewport.

**Density / typography summary of the professional feel:**
- Global body is `text-foreground bg-background font-sans`; tables/cards default to `text-xs`.
- Numbers are `tabular-nums` everywhere (money, counts, dates, pagination).
- Section titles use a shared token: `SECTION_TITLE = "font-medium text-muted-foreground text-xs uppercase tracking-wider"` (`detail-sheet.tsx:41`).
- Thin custom scrollbars + `scroll-fade` (globals.css:217-239).

---

## 2. THE AGENT TAB (the crown jewel)

This is the most copy-worthy part. The agent's work streams into a chat-like transcript **inside each record's detail sheet** as a tab named "Agent". It shows every step live, the reasoning, the leads it throws away *with reasons*, and asks the human questions inline.

### 2a. Where it mounts (a sheet tab, kept mounted)
`apps/app/components/crm/record-sheet/deal-sheet.tsx:88-94` (contact-sheet.tsx is identical):
```tsx
{ value: "agent", label: "Agent",
  content: <AgentPanel record={{ kind: "deal", id: deal.id }} />,
  keepMounted: true },
```
`keepMounted: true` → `DetailSheetTabs` renders it with `forceMount` once opened (`detail-sheet.tsx:180-226`), so **streaming keeps running when you switch to other tabs**:
```tsx
// detail-sheet.tsx:189-222
const [opened] = useState(() => new Set<string>());
opened.add(value);
...
<TabsContent forceMount={tab.keepMounted && opened.has(tab.value) ? true : undefined}
	className="... data-[state=inactive]:hidden">
```

### 2b. Record → agent addressing (headers, not props threaded through)
`apps/app/lib/agent-record.ts` maps a record kind to a copy block + an HTTP header + a filter field. Note the marketing copy that literally advertises the transparency:
```ts
// agent-record.ts:20-28 (contact)
blurb: "Every step is shown as it happens — including the leads it throws away.",
placeholder: "Are they still there?",
suggestions: ["Who is this person?", "Are they still there?", "What should I know before a call?"],
```
`recordHeader(record)` → `{ "x-crm-contact": id }` (etc.), sent on every agent call so the backend scopes the session to that record (`agent-record.ts:62-64`).

### 2c. AgentPanel — conversation picker + thread (`agent-panel.tsx:79-116`)
```tsx
export function AgentPanel({ record }: { record: AgentRecord }) {
	const conversations = useConversations(recordFilter(record));
	const { thread, setThread } = useRecordSheetView("overview");
	const history = conversations.data ?? [];
	const landedOn = useRef<string | null>(null);
	if (landedOn.current === null && conversations.isSuccess) {
		landedOn.current = history[0]?.id ?? NEW_THREAD;
	}
	const { openId, current } = resolveThread({ conversations: history, fromUrl: thread, landedOn: landedOn.current });
	if (conversations.isPending) return <Loading />;
	return (
		<div className="flex min-h-0 flex-1 flex-col">
			<ConversationPicker conversations={history} current={current}
				onSelect={(c) => setThread(c.id)} onNew={() => setThread(NEW_THREAD)} busy={false} />
			<ThreadWithHistory key={openId ?? NEW_THREAD} record={record} conversation={current}
				onNewThread={() => setThread(NEW_THREAD)} />
		</div>
	);
}
```
Which thread is open is stored in the **URL** (`?thread=`), via `useRecordSheetView` (see §4d). `ConversationPicker` (`agent-conversations.tsx:29-90`) is a dropdown of past conversations (title + relative time), a "New conversation" item, and a trash button to forget one.

### 2d. Persistence across reloads — 3 sources reconciled
`ThreadWithHistory` (`agent-panel.tsx:120-166`) is the key. It merges:
1. **Archived events** from our own DB via tRPC: `trpc.conversations.events` (`staleTime: Infinity`).
2. **Live session snapshot** from the agent host via the `eve` client, in `loadThread` (`agent-session.ts:30-49`):
```ts
const snapshot = await new Client({ headers, host: "" })
	.session({ sessionId, streamIndex: 0 }).snapshot({ signal });
return { status: classify(snapshot.session, snapshot.events), session: snapshot.session, events: snapshot.events };
// on throw → { status: "offline", events: archive }   ← falls back to DB copy
```
3. **Polling while working** — the thread query re-fetches every 3s only when live:
```ts
// agent-panel.tsx:118, 143-144
const WORKING_POLL_MS = 3000;
refetchInterval: (query) => query.state.data?.status === "working" ? WORKING_POLL_MS : false,
```
`classify()` (`agent-session.ts:51-70`) decides `ready | working | ended`: a session with a `continuationToken` is `ready`; otherwise it inspects the last event (`session.completed`/`session.failed` → ended) and an **abandonment timeout** (`ABANDONED_AFTER_MS = 90_000`) → if the last event is older than 90s it's `ended`, else `working`. This is how a reload mid-run correctly resumes vs. shows a finished transcript.

The `Thread` component (`agent-panel.tsx:176-296`) then hands those into the live hook:
```ts
const agent = useEveAgent({
	headers: recordHeader(record),
	...(thread && "session" in thread
		? { initialSession: thread.session, initialEvents: eventsOf(thread) }
		: { initialEvents: eventsOf(thread) }),
});
const busy = agent.status === "submitted" || agent.status === "streaming";
const messages = toTranscript(agent.data.messages);
const question = pendingQuestion(agent.data.messages);
const { locked, ended } = composerState(thread, busy);
```

### 2e. Saving a session back to our DB (the only real `useEffect` in the feature)
`useSavedConversation` (`agent-panel.tsx:471-548`) writes `{ sessionId, continuationToken, streamIndex, messageCount, title }` back through `trpc.conversations.save` whenever they change, keyed by a `cursor` string to dedupe, and invalidates the list on first insert so the picker updates. This is what makes conversations survive reload and appear in history.

### 2f. Turning raw agent messages into a transcript (`lib/agent-transcript.ts`)
This file is the heart of "show the reasoning + rejected leads." `toTranscript` (lines 69-105) flattens each eve message's `parts` into two item kinds:
```ts
export type TranscriptItem =
	| { kind: "said"; id: string; mine: boolean; text: string }
	| { kind: "did"; id: string; label: string; tone: Tone; pending: boolean; sources: Source[] };
export type Tone = "neutral" | "success" | "warning";
```
- **text parts** → `said` (a chat bubble).
- **`tool-*` / `dynamic-tool` parts** → `did` (a step marker), with:
  - `label = describe(part)` — a **human verb** from a lookup table plus the tool's own `reason`:
```ts
// agent-transcript.ts:22-56 (excerpt of VERBS)
const VERBS = { resolve_linkedin_profile: "Searched for their LinkedIn profile",
  get_linkedin_profile: "Read a LinkedIn profile", record_fact: "Recorded what it found",
  research_person: "Researched them on the web", ask_question: "Asked a question",
  web_search: "Searched the web", web_fetch: "Read a web page", /* ...built-ins too... */ };
// 129-135
export function describe(part) {
	const verb = VERBS[toolName(part)] ?? humanise(tool);
	const reason = output(part)?.reason;
	return typeof reason === "string" ? `${verb} — ${reason}` : verb;  // "verb — why"
}
```
  - `tone = outcomeTone(part)` — **this is the rejected-lead mechanism**:
```ts
// agent-transcript.ts:137-147
export function outcomeTone(part) {
	if ("state" in part && part.state === "output-error") return "warning";
	const result = output(part);
	if (!result) return "neutral";
	if (result.applied === true || result.written === true) return "success";
	if (result.stored === false || result.written === false) return "warning"; // ← thrown-away lead
	return "neutral";
}
```
  - `sources = sourcesOf(part)` — pulls `sourceUrl`/`profileUrl`/`url` out of the tool output and classifies host into `linkedin | github | web` (lines 149-173).
  - `pending` = tool state is `input-streaming`/`input-available` → show a spinner.

**The backend contract that feeds this** (confirmed in `apps/agent/agent/tools/*`):
- `record_fact.ts:59-67` returns `{ stored, applied, band, score, rationale, reason? }`.
- `set_contact_socials.ts:66-97` pushes rejected candidates into a `rejected[]` and returns `{ written, outcomes, rejected }`; each unverifiable profile yields `stored:false + reason` → **renders as a warning step "…— {reason}"**. Its tool description even says: *"Rejects anything it cannot corroborate — a rejection is a correct outcome, not a problem to work around."*

So "show rejected leads with reasons" = **a normal tool call whose output says `stored:false` + a `reason` string; the transcript renders it as a warning-toned step marker.** No special path.

### 2g. Unanswered questions surfaced inline (human settles them)
Detection (`agent-transcript.ts:175-184`):
```ts
export function pendingQuestion(messages) {
	for (const part of messages.at(-1)?.parts ?? []) {
		if (part.type !== "dynamic-tool") continue;
		const request = part.toolMetadata?.eve?.inputRequest;
		if (request) return request;   // { requestId, prompt, options: [{id,label}] }
	}
	return null;
}
```
Render (`agent-panel.tsx:433-469`) — a tinted agent bubble + option buttons; clicking answers the agent:
```tsx
function Question({ question, agent }) {
	return (
		<Message><AgentAvatar />
			<MessageContent>
				<Bubble variant="tinted"><BubbleContent>{question.prompt}</BubbleContent></Bubble>
				<div className="flex flex-wrap gap-2">
					{(question.options ?? []).map((option) => (
						<Button key={option.id} variant="outline" size="sm"
							onClick={() => void agent.send({ inputResponses: [{ requestId: question.requestId, optionId: option.id }] })}>
							{option.label}
						</Button>
					))}
				</div>
			</MessageContent>
		</Message>
	);
}
```

### 2h. Rendering a step vs a message (`agent-panel.tsx:350-431`)
```tsx
const TONE_ICONS: Record<Tone, CarbonIcon> = { neutral: CircleDash, success: Checkmark, warning: Warning };

function Item({ item }: { item: TranscriptItem }) {
	if (item.kind === "said") {
		return item.mine ? (
			<Message align="end"><MessageContent><Bubble variant="secondary" align="end">
				<BubbleContent>{item.text}</BubbleContent></Bubble></MessageContent></Message>
		) : (
			<Message><AgentAvatar /><MessageContent><Bubble variant="ghost">
				<BubbleContent><Markdown>{item.text}</Markdown></BubbleContent></Bubble></MessageContent></Message>
		);
	}
	return (
		<div className="space-y-1.5">
			<Marker>
				<MarkerIcon>{item.pending ? <Spinner /> : <Icon icon={TONE_ICONS[item.tone]} />}</MarkerIcon>
				<MarkerContent>{item.label}</MarkerContent>
			</Marker>
			{item.sources.length > 0 ? <Sources sources={item.sources} /> : null}
		</div>
	);
}
```
- **Steps** are muted `Marker` rows (a small icon + one line of text) — reasoning reads as a quiet activity log.
- **Agent prose** is markdown in a borderless `ghost` bubble; **user** messages are `secondary` right-aligned bubbles.
- **Sources** render as `Attachment` chips with per-network icons (LinkedIn/GitHub/web), linking out.

### 2i. Streaming scroller + composer states
- `MessageScroller` (packages/ui/src/components/message-scroller.tsx) wraps `@shadcn/react/message-scroller` — an autoscrolling viewport (`autoScroll defaultScrollPosition="end"`) with a floating "scroll to end" button, `content-visibility:auto` on items for perf.
- **Idle empty state** (`agent-panel.tsx:298-333`) uses `Empty` with the record's title/blurb and **suggestion buttons** that seed the first question.
- **Composer** (`agent-panel.tsx:271-293`) is a bordered input + send button; `locked` disables it while busy/working/ended (`composerState`, `agent-session.ts:83-93`). Extra affordances: a "Still working on the last question…" hint, an "This conversation has ended → Start a new conversation" row, and a `Failure` banner that maps known errors to setup hints (lines 335-348).

### 2j. Streaming transport (how bytes get there)
The app **proxies** the browser↔agent stream through a Next route that mints a short-lived JWT, so the agent host is never exposed and the SSE passes through untouched:
- `apps/app/app/eve/v1/[...path]/route.ts` — `export const dynamic = "force-dynamic"`, strips hop-by-hop headers, reads `x-crm-contact|company|deal`, mints a bridge token (`mintBridgeToken`, `lib/agent-bridge.ts`, HS256, 120s TTL) with the record id in the claims, then `fetch(target, { body: request.body, duplex: "half" })` and returns `upstream.body` **without buffering** (streams through). `content-encoding`/`content-length` are stripped so SSE isn't broken.
- `apps/app/app/api/[...path]/route.ts` — the tRPC/API proxy; note lines 79-85 pass `text/event-stream` bodies straight through and only gunzip/brotli-decode non-stream responses.

**Agent-tab component inventory to copy:** `agent-panel.tsx`, `agent-conversations.tsx`, `lib/agent-transcript.ts`, `lib/agent-session.ts`, `lib/agent-record.ts`, `lib/agent-bridge.ts`, `app/eve/v1/[...path]/route.ts`; UI primitives `message.tsx`, `bubble.tsx`, `marker.tsx`, `message-scroller.tsx`, `attachment.tsx`, `suggestion.tsx`, `empty.tsx`, `markdown.tsx`, `spinner.tsx`.

---

## 3. Deals / Contacts / Companies list views + nuqs

One shared `DataTable` renders all three; each page differs only by columns + a tiny search-params config. **All table state (search, sort, dir, page, tab, facets, hidden columns, expanded rows) lives in the URL.**

### 3a. The search-params factory (`components/data-table/list-search-params.ts`)
`createListSearchParams({ defaultSort, defaultDir, pageSize, tabId?, facetIds?, facetDefaults? })` returns `{ parsers, load, toInput, config }`. It composes nuqs parsers:
```ts
// list-search-params.ts:15-18 + 84-89
export const searchParsers = {
	q: parseAsString.withDefault(""),
	page: parseAsInteger.withDefault(1).withOptions({ history: "push" }),
};
const parsers = { ...searchParsers,
	sort: parseAsString.withDefault(defaultSort),
	dir: parseAsStringLiteral(SORT_DIRECTIONS).withDefault(defaultDir),
	...extras };   // each tab/facet: parseAsString.withDefault("all")
```
Per-view config is a one-liner, e.g. `deals-search-params.ts`:
```ts
export const dealsSearchParams = createListSearchParams({
	defaultSort: "createdAt", defaultDir: "desc",
	tabId: "status", facetIds: ["owner", "stage", "closing"] as const,
});
```
`contacts-search-params.ts`: `facetIds: ["owner", "company"]`, no tab. The same file exports a `.load()` (server loader) used by the page for SSR prefetch and a `.toInput()` that trims `q`, clamps `page`, and injects `pageSize` for the query.

### 3b. The client hook that binds URL ⇄ table (`components/data-table/use-table-query.ts`)
`useTableQuery(searchParams)` calls `useQueryStates(parsers)` once and returns `{ query, input }`. `query` is a `TableQueryState` (packages/ui/src/lib/table-query.ts) exposing `sort/dir/page/tab/filters` plus imperative setters that **always reset `page:1`** on any filter/sort change:
```ts
// use-table-query.ts:42-57
toggleSort: (id) => setState((prev) => prev.sort === id
	? { ...prev, dir: prev.dir === "asc" ? "desc" : "asc", page: 1 }
	: { ...prev, sort: id, dir: defaultDir, page: 1 }),
setPage: (next) => setState((prev) => ({ ...prev, page: next })),
setTab: (value) => { if (!tabId) return; setState((prev) => ({ ...prev, [tabId]: value, page: 1 })); },
setFilter: (id, value) => setState((prev) => ({ ...prev, [id]: value, page: 1 })),
```
`input` is the server-shaped query object; feeding it to `useQuery` means the URL is the single source of truth and back/forward works.

### 3c. The list page (server component, prefetch + hydrate) — `deals/page.tsx`
```tsx
export default async function DealsPage({ searchParams }: { searchParams: Promise<SearchParams> }) {
	await requireSession();
	const values = await dealsSearchParams.load(searchParams);
	const trpc = getServerTrpc();
	const queryClient = getServerQueryClient();
	await queryClient.prefetchQuery(trpc.deals.list.queryOptions(dealsSearchParams.toInput(values)));
	void queryClient.prefetchQuery(trpc.users.list.queryOptions());       // fire-and-forget facets
	void queryClient.prefetchQuery(trpc.companies.options.queryOptions({ q: "" }));
	return (
		<PageShell className="min-h-0">
			<PageShellHeader>
				<PageShellHeading><PageShellTitle>Deals</PageShellTitle>
					<PageShellDescription>The pipeline, and everything that has already closed.</PageShellDescription>
				</PageShellHeading>
				<PageShellActions><CreateDealSheet /></PageShellActions>
			</PageShellHeader>
			<PageShellContent className="min-h-0">
				<HydrateClient><DealsTable /></HydrateClient>
			</PageShellContent>
		</PageShell>
	);
}
```
The server prefetches with the *exact same query key* the client will use, so `DealsTable` mounts already-populated (no spinner on first paint).

### 3d. Columns are data; the table component is generic (`deals-table.tsx`)
Columns are a plain array of `DataTableColumn<Row>` with `{ id, header, cell, sortable, width, align, hideBelow, defaultHidden, hideable }` (deals-table.tsx:32-121). Facets are derived and **filtered to non-empty options via `facetCounts`** so the UI never offers a filter that returns nothing:
```tsx
// deals-table.tsx:137-159 (excerpt)
{ id: "owner", label: "Owner",
  options: (users.data ?? []).map((u) => ({ value: u.id, label: u.name }))
	.filter((o) => (facetCounts?.owner?.[o.value] ?? 0) > 0) },
```
The query uses `placeholderData: (previous) => previous` so paging/sorting **keeps the old rows visible** (no flash) while the next page loads; `loading={deals.isFetching}` drives a subtle spinner in pagination. A `meta` slot shows "N deals · $X open pipeline". Row hover **prefetches the detail record**; row click opens the record sheet:
```tsx
onRowHover={(row) => prefetchRecord({ kind: "deal", id: row.id })}
onRowClick={(row) => openRecord({ kind: "deal", id: row.id })}
```

### 3e. The shared DataTable (`packages/ui/src/components/data-table.tsx`, 611 lines — the reusable engine)
Highlights worth copying verbatim:
- **Column visibility + expanded rows are ALSO URL state** (its own nuqs keys, independent of the page):
```tsx
// data-table.tsx:161-173
const [expandedIds, setExpandedIds] = useQueryState("expand", parseAsArrayOf(parseAsString).withDefault([]));
const [hidden, setHidden] = useQueryState("hide", parseAsArrayOf(parseAsString).withDefault(defaultHiddenIds));
```
- **Responsive filter bar**: search input + a mobile "Filters (n)" collapse button + tab dropdown + facet dropdowns + Sort dropdown + Columns dropdown, laid out with `lg:contents` so on desktop they flow onto one row but stay collapsible on mobile (lines 214-421). Active-filter count is computed and shown.
- **Sticky, card-framed table**: container `min-h-0 flex-1 overflow-auto rounded-lg border bg-card`; header `sticky top-0 z-10 bg-muted` with an inset bottom border via box-shadow (lines 423-478). `table-fixed` + per-column `width` classes + `hideBelow` responsive hiding.
- **Sortable headers** are ghost buttons with an arrow indicator (`SortIndicator`, lines 118-138; `ArrowUp/ArrowDown/ArrowsVertical`), and set `aria-sort`.
- **Empty/loading body**: a single full-width cell showing a `Spinner` when loading else the `empty` message (lines 480-488).
- **Expandable rows** (used elsewhere) with a chevron, sub-rows on `bg-muted/30`.
- **Row accent** on hover (`ROW_ACCENT`, packages/ui/src/lib/row-accent.ts): a 2px left bar fades in and the first cell nudges right 4px — a signature micro-interaction:
```ts
"[&>td:first-child]:before:w-0.5 [&>td:first-child]:before:bg-foreground [&>td:first-child]:before:opacity-0",
"[&:hover>td:first-child]:before:opacity-100", "[&:hover>td:first-child]:pl-5",
```
- **Pagination** (`packages/ui/src/components/table-pagination.tsx`): "Showing 1–25 of 240" range text (tabular-nums), Prev (ghost) / "page / total" / Next (`variant="contrast"`), only shown when `totalPages > 1`, plus an optional `meta` slot.

### 3f. Debounced search input (`components/data-table/list-search.tsx` + `hooks/use-search-input.ts`)
`ListSearch` binds `q` via `useQueryStates(searchParsers)` and a 250ms-debounced local mirror so typing is snappy but the URL/query updates only after a pause, resetting `page:1`. `use-search-input.ts` is the reusable debounce (local state + timeout + sync when the committed value changes externally).

---

## 4. Theming — light/dark, tokens, shadcn customization

### 4a. All tokens in one CSS file — `packages/ui/src/styles/globals.css`
Tailwind v4 CSS-first. Structure:
- `@import "tailwindcss"; @import "tw-animate-css"; @import "shadcn/tailwind.css";`
- `@source "../../**/*.{ts,tsx}";` (content scan; replaces `content` in old config)
- `@custom-variant dark (&:is(.dark *));` — **class-based dark mode** (matches next-themes `attribute="class"`).
- `:root { … }` light tokens (lines 9-74), `.dark { … }` dark tokens (lines 76-139).
- `@theme inline { --color-*: var(--*) … }` (lines 141-207) exposes each token as a Tailwind color/utility (so `bg-primary`, `text-muted-foreground`, `border-border`, `bg-sidebar`, `text-success`, etc. all exist).

Palette identity (light → dark):
```css
--primary: #006b4f;              /* deep green brand, same in both themes */
--background: #ffffff → #0f0f0f;
--card: #ffffff → #171717;       /* card sits ABOVE background in dark */
--muted: #f4f4f4 → #1f1f1f;  --muted-foreground: #6b6b6b → #a0a0a0;
--border: #e2e2e2 → #2a2a2a;  --ring: #006b4f → #40be96;
--radius: 5px;  --font-sans: var(--font-geist-sans, …);
/* status + severity ramps in oklch: --success/--warning/--info + --severity-* */
/* chart-1..5, sidebar-* mirror; near-flat shadows (opacity ~0.04–0.12) */
```
Extras baked into globals.css that add polish (all optional but nice):
- **Custom thin scrollbars** (lines 217-239).
- **`bloom-*` utilities** — soft glow used on the dashboard value meters (lines 242-254).
- **Carbon icon hover motion** — `.cds-icon[data-motion="…"]` gives icons spring/pop/spin/wiggle on hover of the enclosing button (lines 256-333); respects `prefers-reduced-motion`.
- **View-transition keyframes** for `nav-forward/back/lateral` route animations (lines 399-481) — paired with React's `<ViewTransition>` (see §5c).
- A whole **`.link-hover--*` underline-animation kit** (lines 483-799).

### 4b. Theme provider (`components/theme-provider.tsx`)
```tsx
<NextThemesProvider attribute="class" defaultTheme="system" enableSystem disableTransitionOnChange {...props}>
```
`disableTransitionOnChange` prevents color-transition flash on toggle. Toggle is invoked from the header avatar menu (§1c).

### 4c. shadcn customization
- `components.json` style `"radix-nova"` (a shadcn registry style), `baseColor: neutral`, primitives imported from `radix-ui` and (for chat) `@shadcn/react/*`.
- Components use a **`cva` variant + `data-slot` + `cn()`** pattern throughout. Buttons have extra variants beyond stock shadcn: `contrast` (`bg-foreground text-background` — the "primary neutral" CTA used for Next/table actions) and sizes `xs`, `icon-xs`, `icon-sm`, `icon-lg` (packages/ui/src/components/button.tsx:10-35). Icons inside buttons auto-size and gain padding via `has-data-[icon=inline-start|end]` selectors.
- `cn` = `twMerge(clsx(...))` (packages/ui/src/lib/utils.ts).

---

## 5. Reusable patterns worth copying

### 5a. Server-prefetch + hydrate = almost no data `useEffect`
`lib/trpc/server.ts` (`getServerTrpc` with `cache()`-wrapped query client), `lib/trpc/hydrate.tsx` (`<HydrateClient>` = `HydrationBoundary state={dehydrate(getServerQueryClient())}`), `lib/trpc/query-client.ts` (`staleTime: 30_000`, and it dehydrates *pending* queries too so streaming SSR works). Pattern per page: server `await prefetchQuery(sameKey)` → wrap the client component in `<HydrateClient>` → the client `useQuery(sameKey)` reads the warm cache. Result: filled first paint, and effects are reserved for genuinely external sync (debounce timer, the one conversation-save effect).

### 5b. Composition over configuration
`PageShell*`, `DetailSheet*`, `DashboardRow/StatGroup/Card*`, `Empty*`, `Marker*`, `Message*`, `Bubble*` are all **slot component families** (`data-slot` + small wrappers) you assemble, not big prop-bags. E.g. `detail-sheet.tsx` exports ~18 named parts; a sheet is authored declaratively (see `deal-sheet.tsx`).

### 5c. Motion via native View Transitions (`components/page-transition.tsx`)
Uses React's experimental `<ViewTransition>` (enabled by `next.config.ts` `experimental.viewTransition: true`) with named types (`nav-forward/back/lateral`) matched to the CSS keyframes in globals.css. Nav links pass `transitionTypes={[...]}`; header/rail get stable `[view-transition-name:…]` so they don't animate.

### 5d. The record "sheet stack" is URL state (`record-sheet/record-stack.ts`)
A right-side detail sheet system driven entirely by nuqs: `?record=deal:abc,contact:def` is a **stack** (drill from a deal into a contact and Back returns). Also parses `?tab=`, `?thread=`, `?add=`, timeline tab. `useRecordStack()` gives `{ stack, top, open, close, closeAll }`; `useOpenRecord()` is the one-liner used by every table/cell; `useRecordSheetView(fallbackTab)` reads/writes the active tab + thread. Because it's URL state, detail views are shareable, survive reload, and back/forward works.

### 5e. Human-in-the-loop **field suggestions** (separate from chat questions)
Beyond the chat "questions," the agent proposes **facts** that a rep accepts/dismisses inline on the record. `facts.tsx` splits facts into `applied` (written, shown with a dotted-underline + provenance tooltip) vs `proposed` (a `Suggestion` row with ✓/✕). `contact-sheet.tsx:290-299` wires `agentProps(field)` onto each `InlineField`, so e.g. the Title/LinkedIn fields show the agent's evidence and a one-click accept:
```tsx
// components/ui suggestion.tsx — the accept/dismiss row
<Suggestion value={fact.value} rationale={reasons.join(" · ")} pending={...}
	onAccept={() => decide.mutate({ factId, decision: "accept" })}
	onDismiss={() => decide.mutate({ factId, decision: "dismiss" })} />
```
Provenance (`sourced-value.tsx`) = a tooltip listing the claim, the evidence lines, when observed, and the source host. `SOURCED_VALUE = "underline decoration-dotted underline-offset-4"` marks agent-sourced values. `DetailSheetPending` (`detail-sheet.tsx:302-330`) shows a pulsing "Agent is researching / Not known yet" chip for fields not yet found.

### 5f. Enrichment status + polling (`enrichment-status.tsx`)
Maps `PENDING/RUNNING/COMPLETE/FAILED/SKIPPED` → `StatusIndicator` tone + label + busy-spinner; `ENRICHMENT_POLL_MS = 3000`; `isEnriching()` drives `refetchInterval` on the record query (`contact-sheet.tsx:90-95`) so the sheet live-updates while the agent works, then stops. `StatusIndicator` (packages/ui) = a small colored dot (with `bloom`) + truncating label; the reusable atom for every status pill.

### 5g. Dashboard / overview polish
`sales-dashboard.tsx` + `dashboard-summary.tsx`: a bordered `StatGroup` of 4 `StatCard`s (big `text-3xl tabular-nums` value + colored ± delta glyph + description), then charts (`AreaTrend`, `DonutStat`) with **real empty states** ("No deals closed or created yet"), then two `CardPanel`s (in-progress deals with a `ValueMeter` bloom bar, overdue tasks) + a recent-activity table. Loading = a centered `Spinner`; skeleton variant exists (`DashboardSkeleton`). Overview scope is a nuqs `ToggleGroup` ("Me/Everyone", `overview-scope.tsx`), greeting is a `useSuspenseQuery` on `users.me`.

### 5h. Small reusable helpers
`packages/ui/src/lib/format.ts`: `formatMoney`/`formatMoneyCompact` (Intl, 0 fractional if whole), `formatPercent`, `relativeTimeFromIso` ("2h ago"/"in 3d"), `formatDay`, `initialsFromName`, `formatCount` (pluralize). Tables render dates with `suppressHydrationWarning` since relative times differ server/client.

---

## 6. What would upgrade Collecct (and how)

Collecct today: custom 3-pane console, top-bar section nav, Bid sidebar, Pipeline list with **faceted filters persisted in localStorage**. Already on Next.js + Tailwind. Mapping their wins onto ours:

1. **Move filter/sort/page/tab state from localStorage → URL via nuqs.** Biggest, cheapest upgrade. Adopt `createListSearchParams` + `useTableQuery` + the shared `DataTable` almost verbatim. Payoffs: shareable Pipeline links (a filtered view of opportunities can be pasted into Slack/email), working browser back/forward, deep-linkable filtered states for the daily SAM.gov digest, and server-prefetchable pages. Collecct's facets (agency / NAICS / set-aside / value / deadline) map 1:1 onto `facetIds`; keep localStorage only as an *optional default*, with the URL as source of truth. Translation note: their `facetCounts`-driven "hide empty options" is a govcon win — never show a NAICS filter that returns 0 opportunities.

2. **Adopt the Agent-activity tab pattern for the Analyst/Relation/Mail agents.** This is the closest thing to what Collecct's CEO-orchestrator agents need: a transcript that streams steps, shows reasoning as quiet `Marker` rows, renders **rejected leads/bids with reasons** (bid/no-bid: a "no-bid" is just a tool result with `stored:false + reason` → warning marker — directly serves the backlog item "better analyst bid/no-bid decisions" by making the *why* visible), and asks the human questions inline (`ask_question` → option buttons). Copy `agent-transcript.ts` (the message→transcript mapper — engine-agnostic; just needs your agent's message parts shaped like eve's `{type, state, output}`), `agent-session.ts` (snapshot + `classify` + 90s abandonment + 3s poll), and the `Marker`/`Bubble`/`Message`/`MessageScroller` primitives. Persist sessions to our DB exactly like `useSavedConversation` so a rep can reload mid-run. Since our engine is **Agno**, not eve, we'd swap `useEveAgent`/`eve/client` for an Agno streaming client but keep the *entire* presentation layer + persistence/polling logic.

3. **Human-in-the-loop for BD, two ways:** (a) inline chat questions for ambiguous calls, and (b) the **fact-suggestion** pattern (`facts.tsx` + `Suggestion` + `Provenance`) for agent-proposed CRM field values (contact title, company UEI/NAICS, teaming-partner links) that a rep accepts/dismisses with one click and full provenance tooltip. This fits the govcon need to keep humans accountable for what lands in the record, and gives the "source" trail auditors like.

4. **Steal the shell + tokens for the "professional look" without a rebrand.** Port `globals.css`'s token architecture (Tailwind v4 `@theme inline` + `:root`/`.dark`, oklch status ramps, near-flat shadows, thin scrollbars) and swap `--primary` from their green `#006b4f` to Collecct's brand. Adopt `PageShell*` (centered `max-w-7xl`, 2-col header grid, `@container` content), the `h-svh` app frame with independent pane scroll, and `text-xs`/`tabular-nums` density. `next-themes` class strategy + the avatar-menu toggle is a 30-min add for dark mode.

5. **The record detail-sheet-as-URL-stack** (`record-stack.ts`) is a strong fit for Collecct's 3-pane console: opening an Opportunity/Company/Contact as a right-side sheet with a drill-in stack (Bid → Company → Contact, Back to return), all shareable via `?record=`. `keepMounted` tabs mean the agent keeps streaming while the rep reads the Overview/Activity tabs.

6. **Cheap polish to copy directly:** `ROW_ACCENT` hover bar on Pipeline rows; `placeholderData: keepPrevious` so paging never flashes; row-hover `prefetchQuery` for instant detail open; `StatusIndicator` dots for opportunity stage/enrichment; `StatCard` KPI row for the dashboard; real `Empty` states with seed-action buttons; `formatMoneyCompact`/`relativeTimeFromIso`; the SSE-proxy route (`app/eve/v1/[...path]/route.ts`) as a template for proxying our Agno stream with a short-lived signed token so the agent host stays private.

**Caveats / translation notes:** their data layer is tRPC+TanStack Query; Collecct may be on a different API — but nuqs, the `DataTable`, the theming, and the whole Agent-tab *presentation* are transport-agnostic and portable. The `eve`-specific bits are only: `useEveAgent`, `eve/client` snapshot, and `toolMetadata.eve.inputRequest` shape — isolate those behind our own hook and the rest drops in. Carbon icons vs. our current set is a taste call (they're denser/more enterprise). Don't port the `.link-hover--*` kit unless wanted — it's decorative weight.

---

## Appendix — file map (by concern)

- **Shell:** `app/layout.tsx`, `app/(app)/layout.tsx`, `components/app-header.tsx`, `components/app-icon-rail.tsx`, `components/mobile-nav.tsx`, `components/page-shell.tsx`, `components/page-transition.tsx`, `components/theme-provider.tsx`.
- **Agent tab:** `components/crm/agent-panel.tsx`, `components/crm/agent-conversations.tsx`, `lib/agent-transcript.ts`, `lib/agent-session.ts`, `lib/agent-record.ts`, `lib/agent-bridge.ts`, `app/eve/v1/[...path]/route.ts`; primitives `packages/ui/src/components/{message,bubble,marker,message-scroller,attachment,suggestion,empty,markdown,spinner}.tsx`.
- **Lists + nuqs:** `components/data-table/{list-search-params.ts,use-table-query.ts,list-search.tsx}`, `app/(app)/{deals,contacts,companies}/{page.tsx,*-table.tsx,*-search-params.ts}`, `packages/ui/src/components/{data-table,table,table-pagination}.tsx`, `packages/ui/src/lib/{table-query.ts,row-accent.ts}`, `packages/ui/src/hooks/use-search-input.ts`.
- **Record sheet:** `components/detail-sheet.tsx`, `components/crm/record-sheet/{record-sheet-host,deal-sheet,contact-sheet,record-parts,record-prefetch}.tsx`, `components/crm/record-sheet/record-stack.ts`, `components/crm/{facts,inline-field,enrichment-status,social-links}.tsx`, `packages/ui/src/components/sourced-value.tsx`.
- **Theming:** `packages/ui/src/styles/globals.css`, `apps/app/components.json`, `packages/ui/src/components/button.tsx`, `packages/ui/src/lib/utils.ts`.
- **Dashboard:** `app/(app)/{page.tsx,dashboard-summary.tsx,sales-dashboard.tsx,overview-greeting.tsx,overview-scope.tsx}`, `packages/ui/src/components/{dashboard,card,stat-card,status-indicator}.tsx`, `packages/ui/src/lib/format.ts`.
- **Data wiring:** `lib/trpc/{server.ts,client.tsx,hydrate.tsx,query-client.ts}`, `app/api/[...path]/route.ts`, `next.config.ts`, `proxy.ts`.
