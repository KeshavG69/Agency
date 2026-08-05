# 05 — The Agent Tab / Live AI UI

Source: `/private/tmp/claude-501/-Users-keshav-Developer-Others-AI-Agency/ad094025-ce2f-4d46-9f0e-7189842d9f45/scratchpad/crm`
(monorepo: `apps/app` = Next.js UI, `apps/api` = NestJS+tRPC, `apps/agent` = eve agent, `packages/ui` = design system)

Target: **Collecct** — append-only `agent_events` (agent, step, detail, ok, created_at) + `contact_facts`
(PROPOSED, evidence, `rationale`), exposed at `GET /api/intelligence/events/{subject_id}`,
`GET /api/intelligence/suggestions`, `POST /api/intelligence/facts/{id}/decide`. **Agents are Celery, not streaming.**
Every section below is tagged **[PORTS]** (works for a poll-only trail) or **[STREAM-ONLY]**.

---

## 0. The file map

| File | What it is |
| --- | --- |
| `apps/app/components/crm/agent-panel.tsx` (548 lines) | The whole Agent tab: picker → load → transcript → composer |
| `apps/app/components/crm/agent-conversations.tsx` (146) | Thread picker dropdown + delete |
| `apps/app/lib/agent-transcript.ts` (219) | tool-call → human step; tone; sources; pending question; thread resolution |
| `apps/app/lib/agent-session.ts` (93) | snapshot load, `classify()` status machine, `composerState()` |
| `apps/app/lib/agent-record.ts` (74) | record kind → header / filter / empty-state copy |
| `apps/app/lib/agent-bridge.ts` (70) | HS256 bridge token minting |
| `apps/app/app/eve/v1/[...path]/route.ts` (121) | same-origin proxy to the agent (auth enforcement point) |
| `apps/app/components/crm/facts.tsx` (76) | fact suggestion accept/dismiss + provenance tooltip content |
| `packages/ui/src/components/{marker,suggestion,sourced-value,message-scroller,bubble,message,attachment,status-indicator}.tsx` | the primitives |
| `apps/app/test/agent-transcript.spec.ts`, `test/agent-session.spec.ts` | the rules, pinned as tests |
| `docs/agent.md` §"The bridge" (lines 540–720) | the *why*, written out in prose — the single most valuable file here |

**They do NOT use `ai-elements`.** `skills-lock.json:4` pins `vercel/ai-elements` as an installed *skill*
(46 reference docs under `.agents/skills/ai-elements/references/`: `agent.md`, `chain-of-thought.md`,
`task.md`, `tool.md`, `reasoning.md`, `sources.md`, `suggestion.md`, `inline-citation.md`, `confirmation.md`…),
but nothing in `apps/app` or `packages/ui` imports from `@/components/ai-elements/*`. `components.json`
aliases everything to `@crm/ui/components`. They re-implemented the four primitives they actually needed
(`Message`, `Bubble`, `Marker`, `MessageScroller`) inside their own design system. **Read the ai-elements
docs for vocabulary; don't install the package.**

---

## 1. How the transcript is assembled

Three layers, and they are deliberately *not* symmetric.

### 1a. The DB archive (fallback only)

`apps/app/components/crm/agent-panel.tsx:131-135`:

```tsx
	const archive = useQuery({
		...trpc.conversations.events.queryOptions({ id: conversation?.id ?? "" }),
		enabled: conversation !== null,
		staleTime: Number.POSITIVE_INFINITY,
	});
```

Backed by `apps/api/src/conversations/conversations.service.ts:123-145`:

```ts
		const events = await this.db.agentEvent.findMany({
			where: { sessionId: conversation.sessionId },
			orderBy: { emittedAt: "asc" },
			take: input.limit,
			select: { id: true, type: true, data: true, emittedAt: true },
		});

		return events.map((event) => ({
			type: event.type,
			data: event.data,
			meta: { id: event.id, at: event.emittedAt.toISOString() },
		}));
```

Note the shape it re-emits: `{ type, data, meta: { id, at } }` — the *exact* wire shape of a live
stream event. The archive is a replay of the stream, not a second schema.

Written append-only by the agent-side audit hook, `apps/agent/agent/hooks/audit.ts:5-25`:

```ts
export default defineHook({
	events: {
		async "*"(event, ctx) {
			const id = event.meta?.id;
			if (!id) return;
			try {
				await db.agentEvent.createMany({
					data: [{ id, sessionId: ctx.session.id, contactId: currentFocus().contactId,
						type: event.type, data: ("data" in event ? (event.data ?? {}) : {}) as object,
						emittedAt: event.meta?.at ? new Date(event.meta.at) : new Date() }],
					skipDuplicates: true,
				});
```

`skipDuplicates: true` + the event's own id as the PK = idempotent append. Schema
`packages/db/prisma/schema.prisma:339-350`:

```prisma
model AgentEvent {
  id        String  @id
  sessionId String
  contactId String?
  type      String
  data      Json
  emittedAt DateTime
  @@index([sessionId, emittedAt])
  @@index([contactId, emittedAt])
}
```

> **[PORTS] — this is Collecct's `agent_events` almost exactly.** Same append-only table, same
> `(subject, time)` index, same "the archive re-emits the live wire shape" trick. The one thing to steal:
> **make the archived row deserialize into the same object your renderer takes**, so there is one renderer,
> not two.

### 1b. The live session snapshot (the authority)

`apps/app/lib/agent-session.ts:30-49`:

```ts
export async function loadThread(
	sessionId: string,
	headers: Record<string, string>,
	archive: readonly MessageStreamEvent[] = [],
	signal?: AbortSignal,
): Promise<Thread> {
	try {
		const snapshot = await new Client({ headers, host: "" })
			.session({ sessionId, streamIndex: 0 })
			.snapshot({ signal });

		return {
			status: classify(snapshot.session, snapshot.events),
			session: snapshot.session,
			events: snapshot.events,
		} as Thread;
	} catch {
		return { status: "offline", events: archive };
	}
}
```

The archive is **only** used in the `catch`. `docs/agent.md:653-658`:

> **An unreachable agent is `offline`, not `working`.** One is a fact about us and the other a claim about
> the session; stated as the latter it is both untrue and unrecoverable, since the read fails identically
> next time. The transcript then comes from our own `AgentEvent` archive — which is also what makes a
> thread older than eve's 30-day retention still readable — and the composer stays usable.

And `docs/agent.md:621-635` on why they stopped hand-rolling it:

> **A thread is loaded with `session.snapshot()`, not by hand.** One call returns the complete event prefix,
> the cursor that continues from it, and a continuation token *if and only if* eve will accept another turn —
> about 30ms against a hundred-event thread. […] What it replaced is worth remembering, because every panel
> bug of the last day came out of it: a raw `fetch` of `…/stream?startIndex=-1`, parsing the last line into
> a state machine. The endpoint *follows*, so awaiting the body never returned.

### 1c. Polling — the exact numbers

`apps/app/components/crm/agent-panel.tsx:118`:

```tsx
const WORKING_POLL_MS = 3000;
```

`agent-panel.tsx:137-152`:

```tsx
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

- **Poll interval: 3000 ms.** Same constant as the enrichment poll (`enrichment-status.tsx:52`
  `export const ENRICHMENT_POLL_MS = 3_000;`) — one cadence across the product.
- **Polling starts** only after the *first* load returns `status === "working"`. It is a
  self-terminating poll driven by the payload, not a timer someone has to clear.
- **Polling stops** the moment a fetch returns `ready` / `ended` / `offline`. Notably it also stops
  on `offline` — a dead backend is not hammered every 3s.
- `enabled: … && !archive.isPending` — **gating**. `docs/agent.md:615-618`:
  > **Nothing mounts until the list has loaded.** Rendering a thread while the history is still in flight
  > starts a *new* eve session and then remounts onto the real one — which presents as "the history only
  > appears if I refresh".
- `refetchOnWindowFocus: false` — deliberate; a focus refetch would fight the interval.

### 1d. The abandonment cutoff — 90 seconds

`apps/app/lib/agent-session.ts:4` and `:51-70`:

```ts
const ABANDONED_AFTER_MS = 90_000;
```

```ts
export function classify(
	session: SessionState,
	events: readonly MessageStreamEvent[],
	now: number = Date.now(),
): "ready" | "working" | "ended" {
	if (session.continuationToken) return "ready";

	const last = events.at(-1);
	if (!last) return "ended";

	if (last.type === "session.completed" || last.type === "session.failed") {
		return "ended";
	}

	const at = Date.parse(last.meta.at);

	return Number.isNaN(at) || now - at < ABANDONED_AFTER_MS
		? "working"
		: "ended";
}
```

Precedence: **token > terminal event > recency of the last event > "assume working" when undated.**

`docs/agent.md:650-652`:

> **A turn that has gone quiet for 90 seconds is over, not working.** A restarted agent leaves sessions with
> no closing boundary; they never park, and treating them as in-flight locks that thread forever.

Pinned in `apps/app/test/agent-session.spec.ts:33-51`:

```ts
	it("reads a turn still emitting as working", () => {
		const recent = event("message.appended", "2026-08-01T11:59:30.000Z");
		expect(classify(unparked, [recent], NOW)).toBe("working");
	});

	it("retires a turn that stopped mid-sentence", () => {
		const stalled = event("message.appended", "2026-08-01T11:50:00.000Z");
		expect(classify(unparked, [stalled], NOW)).toBe("ended");
	});

	it("does not retire a live turn for want of a timestamp", () => {
		const undated = { type: "step.started", data: {}, meta: { id: "x" } };
		expect(classify(unparked, [undated as MessageStreamEvent], NOW)).toBe("working");
	});
```

> **[PORTS] — this is the single most reusable idea in the repo for Collecct.**
> Collecct has no continuation token and no `session.completed`. The degenerate but *correct* port:
> ```ts
> const ABANDONED_AFTER_MS = 90_000; // tune to your Celery task's slowest step
> function classify(events, now = Date.now()) {
>   const last = events.at(-1);
>   if (!last) return "queued";                       // task enqueued, nothing written yet
>   if (last.step === "done" || last.step === "failed") return "ended";
>   return now - Date.parse(last.created_at) < ABANDONED_AFTER_MS ? "working" : "ended";
> }
> ```
> Have the Celery task write a terminal `agent_events` row (`step: "done" | "failed"`) on *every* exit path
> including the exception handler. The 90s cutoff then only catches hard worker kills. Without the cutoff,
> a killed worker leaves the panel spinning forever — the exact bug `docs/agent.md:650` describes.
>
> One thing Collecct has that this repo does *not*: a Celery task id. If you can expose task state
> (`PENDING/STARTED/RETRY/SUCCESS/FAILURE`) alongside the events, that is a **stronger** authority than
> event recency — the equivalent of eve's `continuationToken`. Use it first, fall back to recency.

### 1e. The re-seed trick that makes a poll behave like a stream

`agent-panel.tsx:157-165`:

```tsx
		<Thread
			key={thread.data?.status === "working" ? "working" : "settled"}
			record={record}
			conversation={conversation}
			thread={thread.data}
			onNewThread={onNewThread}
		/>
```

and `agent-panel.tsx:188-193`:

```tsx
	const agent = useEveAgent({
		headers: recordHeader(record),
		...(thread && "session" in thread
			? { initialSession: thread.session, initialEvents: eventsOf(thread) }
			: { initialEvents: eventsOf(thread) }),
	});
```

Each poll returns the **complete event prefix**, not a delta. `initialEvents` re-seeds the renderer.
The `key` forces a clean remount exactly once, on the working→settled edge.

> **[PORTS]** Collecct's `GET /api/intelligence/events/{subject_id}` returning the *whole ordered list*
> every 3s is the same contract. Render from `events` as a pure function; do not accumulate into local state.
> Full-list-every-poll is cheap (these lists are tens of rows) and removes an entire class of
> "the trail is missing a step" bugs. Only add `?since=` when a subject exceeds ~500 events.

---

## 2. Surviving a page reload

Two independent durable handles: the **session id** (which conversation) and the **continuation token**
(whether it will take another turn). Both live in `AgentConversation`
(`packages/db/prisma/schema.prisma`):

```prisma
model AgentConversation {
  contactId String?  companyId String?  dealId String?
  userId String
  sessionId         String  @unique
  continuationToken String?
  streamIndex       Int     @default(0)
  title        String?
  messageCount Int     @default(0)
  createdAt     DateTime @default(now())
  lastMessageAt DateTime @default(now())
  @@index([contactId, lastMessageAt]) @@index([companyId, lastMessageAt]) @@index([dealId, lastMessageAt])
}
```

The write-back hook, `agent-panel.tsx:471-548` (`useSavedConversation`). The cursor guard at `:502-509`:

```tsx
	const written = useRef<string | null>(null);

	useEffect(() => {
		if (!sessionId) return;

		const cursor = `${sessionId}:${token ?? ""}:${messages}`;
		if (written.current === cursor) return;
		written.current = cursor;
```

— a composite cursor, so the same `(session, token, message-count)` never posts twice.

`isNew` is decided by session-id comparison, not by emptiness (`agent-panel.tsx:497`):

```tsx
	const isNew = conversation === null || conversation.sessionId !== sessionId;
```

Server upsert keyed on the session id, `conversations.service.ts:90-110`:

```ts
		const conversation = await this.db.agentConversation.upsert({
			where: { sessionId: input.sessionId },
			create: { sessionId: input.sessionId, continuationToken: input.continuationToken ?? null, … },
			update: { continuationToken: input.continuationToken ?? null,
				streamIndex: input.streamIndex ?? 0, messageCount: input.messageCount ?? 0,
				lastMessageAt: new Date() },
```

Which thread is open lives in the **URL**, via `nuqs` — `record-stack.ts:30-36` and `:128-131`:

```ts
const params = {
	record: parseAsArrayOf(parseAsString, ",").withDefault([]),
	tab: parseAsString,
	add: parseAsStringLiteral(RECORD_FORMS),
	thread: parseAsString,
	[TIMELINE_PARAM]: timelineTabParser,
};
```
```ts
	const setThread = useCallback(
		(next: string | null) => void setParams({ thread: next }),
		[setParams],
	);
```

`docs/agent.md:607-623`:

> - **Resuming.** The panel passes the saved cursor as `initialSession`, so reopening a contact continues
>   last week's thread rather than starting another. eve keeps sessions for 30 days.
> - **Replay from the start.** `streamIndex: 0` on resume, deliberately — the saved index is where the
>   *last reader* stopped, and a reopened thread should show what was said in it, not only what has
>   happened since.
> - **Which thread is open lives in the URL** (`?thread=`), like every other view state in the sheet, so a
>   refresh keeps your place and a conversation is a link.
> - **The thread the panel landed on is captured once.** Re-deriving "the latest" as the list changes would
>   swap the open conversation out from under a live answer the moment the first save adds a row.

That "captured once" rule, `agent-panel.tsx:85-94`:

```tsx
	const landedOn = useRef<string | null>(null);
	if (landedOn.current === null && conversations.isSuccess) {
		landedOn.current = history[0]?.id ?? NEW_THREAD;
	}

	const { openId, current } = resolveThread({
		conversations: history,
		fromUrl: thread,
		landedOn: landedOn.current,
	});
```

`agent-transcript.ts:200-219`:

```ts
export const NEW_THREAD = "new";

export function resolveThread<T extends { id: string }>({
	conversations, fromUrl, landedOn,
}: { conversations: readonly T[]; fromUrl: string | null; landedOn: string | null }):
	{ openId: string | null; current: T | null } {
	const openId = fromUrl ?? landedOn;
	if (!openId || openId === NEW_THREAD) return { openId, current: null };
	return { openId, current: conversations.find((row) => row.id === openId) ?? null };
}
```

**The keep-mounted rule** — `contact-sheet.tsx:127-132`:

```tsx
				{
					value: "agent",
					label: "Agent",
					content: <AgentPanel record={{ kind: "contact", id: contact.id }} />,
					keepMounted: true,
				},
```

`detail-sheet.tsx:216-222`:

```tsx
				<TabsContent
					key={tab.value}
					value={tab.value}
					forceMount={
						tab.keepMounted && opened.has(tab.value) ? true : undefined
					}
					className="flex min-h-0 flex-1 flex-col overflow-hidden outline-none data-[state=inactive]:hidden"
				>
```

`docs/agent.md:624-631`:

> **The panel is not unmounted when you switch tabs.** It holds a live stream, and Radix drops an inactive
> tab by default — which aborts the stream mid-answer, so the reply landed in the durable session with
> nothing attached to receive it. That is the "I went to another tab and the answer never came back" bug…
> `keepMounted` on the tab descriptor (`detail-sheet.tsx`) keeps it alive; it renders nothing until the tab
> is opened once, so flicking through records costs nothing.

> **[PORTS]** The URL `?thread=` / landed-on-once / upsert-by-durable-id pattern all port directly.
> **[STREAM-ONLY-ish]** `keepMounted` matters *less* for Collecct — a poll survives unmount because the
> events are in Postgres/Mongo, not in a socket. Still worth it for the *cheaper* reason: `forceMount` +
> `opened.has()` means remounting the tab doesn't re-fire the load and flash a spinner. Do it, but the
> stakes are lower.
> **[N/A]** `continuationToken` has no Collecct analogue. Its job — "may the user send another message?" —
> is answered in Collecct by "is there a running Celery task for this subject?".

---

## 3. Tool call → human step, and how a REJECTION renders differently

### 3a. The verb table

`apps/app/lib/agent-transcript.ts:22-56` — the whole mapping, verbatim:

```ts
const VERBS: Record<string, string> = {
	read_crm_history: "Read our emails and meetings with them",
	read_company_history: "Read everything we have on the company",
	read_deal_history: "Read the deal and where it has been",
	search_crm: "Looked the record up in the CRM",
	resolve_linkedin_profile: "Searched for their LinkedIn profile",
	get_linkedin_profile: "Read a LinkedIn profile",
	get_contact_work_history: "Read their work history",
	fetch_contact_photo: "Fetched their profile picture",
	find_contact_socials: "Searched for their other profiles",
	set_contact_socials: "Checked a profile against the account itself",
	identify_contact: "Put a name to the address",
	record_fact: "Recorded what it found",
	write_brief: "Wrote the background",
	write_workspace_profile: "Wrote up who we are",
	research_person: "Researched them on the web",
	research_company: "Read the company's site",
	enrich_company: "Looked up the company",
	schedule_recheck: "Decided when to look again",
	record_job_change: "Raised a job change",
	list_outstanding_work: "Looked for outstanding work",

	load_skill: "Read its instructions for this",
	web_search: "Searched the web",
	web_fetch: "Read a web page",
	todo: "Updated its plan",
	ask_question: "Asked a question",
	agent: "Handed part of the job to a helper",
	connection_search: "Looked for a tool it could use",
	bash: "Ran a command",
	read_file: "Read a file",
	write_file: "Wrote a file",
	glob: "Looked for files",
	grep: "Searched inside the files",
};
```

Note: **past tense, sentence case, no jargon, no tool name, no arguments.** "Looked the record up in the
CRM", not `search_crm({query: "..."})`. The whole aesthetic of the panel is in this table.

Fallback + reason splice, `agent-transcript.ts:58-61` and `:129-135`:

```ts
function humanise(tool: string): string {
	const words = tool.replace(/_/g, " ");
	return words.charAt(0).toUpperCase() + words.slice(1);
}
```
```ts
export function describe(part: EveMessagePart): string {
	const tool = toolName(part);
	const verb = VERBS[tool] ?? humanise(tool);
	const reason = output(part)?.reason;

	return typeof reason === "string" ? `${verb} — ${reason}` : verb;
}
```

**Coverage is enforced by a test that reads the tools directory** —
`apps/app/test/agent-transcript.spec.ts:299-334`:

```ts
	const authored = readdirSync(
		new URL("../../agent/agent/tools", import.meta.url),
	)
		.filter((file) => file.endsWith(".ts"))
		.map((file) => file.replace(/\.ts$/, ""));

	it("covers every tool the agent ships with", () => {
		expect(authored.length).toBeGreaterThan(0);
		for (const tool of [...authored, ...BUILT_INS]) {
			expect(TOOL_VERBS[tool]).toBeString();
		}
	});

	it("writes them as sentences, not as slugs", () => {
		for (const [tool, verb] of Object.entries(TOOL_VERBS)) {
			expect(verb, tool).not.toContain("_");
			expect(verb[0], tool).toBe(verb[0]?.toUpperCase() ?? "");
		}
	});
```

`docs/agent.md:457-459`:

> Adding a fourth record kind is an entry in `sessionPreamble`, a read beside the other three, and a line in
> `TOOL_VERBS` (`apps/app/lib/agent-transcript.ts`), which a test enforces so no tool ever shows a rep a
> bare slug.

### 3b. The `stored:false` → warning mapping

`agent-transcript.ts:137-147`:

```ts
export function outcomeTone(part: EveMessagePart): Tone {
	if ("state" in part && part.state === "output-error") return "warning";

	const result = output(part);
	if (!result) return "neutral";

	if (result.applied === true || result.written === true) return "success";
	if (result.stored === false || result.written === false) return "warning";

	return "neutral";
}
```

Three tones, three icons — `agent-panel.tsx:350-354`:

```tsx
const TONE_ICONS: Record<Tone, CarbonIcon> = {
	neutral: CircleDash,
	success: Checkmark,
	warning: Warning,
};
```

The test spells out the intent, `agent-transcript.spec.ts:118-125`:

```ts
	it("reads a refusal as a warning, because that is the interesting half", () => {
		expect(
			outcomeTone(tool("record_fact", { output: { stored: false } }) as never),
		).toBe("warning");
		expect(
			outcomeTone(tool("write_brief", { output: { written: false } }) as never),
		).toBe("warning");
	});
```

And the reason text is *the tool's own prose*. `apps/agent/agent/lib/facts.ts` returns, verbatim, the
strings a rep will read as `"Recorded what it found — …"`:

```ts
			reason: "Below the floor for keeping — not stored. Find a source that identifies them, or leave the field alone.",
```
```ts
			reason: "A person has already dismissed this exact value. Do not offer it again.",
```
```ts
			reason: "Already on the record, from this same source. Nothing changed.",
```
```ts
			reason: `A person already filled in ${field}. That outranks anything found on the web.`,
```

The empty-state blurb makes the promise explicit — `agent-record.ts:21-23`:

```ts
		blurb:
			"Every step is shown as it happens — including the leads it throws away.",
```

> **[PORTS] — steal this wholesale.** Collecct's `agent_events` already has `ok: boolean` and `detail`.
> Map: `ok === true` → success ✓, `ok === false` → **warning ⚠, not error**, and render `detail` as the
> em-dash suffix on the verb. The insight is that a *refusal is content*, not a failure: "Recorded what it
> found — A person already filled in title. That outranks anything found on the web." is the line that
> makes a user trust the agent. Keep a `STEP_VERBS: Record<step, string>` table in the FE and write the
> same directory-scanning test against your Celery task/step registry.

### 3c. Grouping and stable ids

`agent-transcript.ts:69-118`:

```ts
export function toTranscript(
	messages: readonly EveMessage[],
): TranscriptMessage[] {
	return messages
		.map((message) => ({
			id: message.id,
			mine: message.role === "user",
			items: message.parts.flatMap((part, index): TranscriptItem[] => {
				const id = partId(message.id, part, index);

				if (part.type === "text") {
					const text = part.text.trim();
					if (!text) return [];
					return [{ kind: "said", id, mine: message.role === "user", text }];
				}

				if (part.type.startsWith("tool-") || part.type === "dynamic-tool") {
					const state = "state" in part ? part.state : undefined;

					return [
						{
							kind: "did",
							id,
							label: describe(part),
							tone: outcomeTone(part),
							pending:
								state === "input-streaming" || state === "input-available",
							sources: sourcesOf(part),
						},
					];
				}

				return [];
			}),
		}))
		.filter((message) => message.items.length > 0);
}

function partId(messageId: string, part: EveMessagePart, index: number): string {
	const callId =
		"toolCallId" in part && typeof part.toolCallId === "string"
			? part.toolCallId
			: null;

	return callId ? `${messageId}:${callId}` : `${messageId}:${index}`;
}
```

Two rules worth naming:
- **Empty text parts are dropped** (`if (!text) return []`), and a message with zero items is dropped.
  No blank bubbles during a stream.
- **`toolCallId` is preferred for the React key** so the same step keeps its identity across
  `input-available` → `output-available`. Pinned at `agent-transcript.spec.ts:50-74` ("gives a tool call
  the same id across its streaming states").

The `TranscriptItem` union, `agent-transcript.ts:3-20`:

```ts
export type TranscriptItem =
	| { kind: "said"; id: string; mine: boolean; text: string }
	| {
			kind: "did";
			id: string;
			label: string;
			tone: Tone;
			pending: boolean;
			sources: Source[];
	  };

export type Tone = "neutral" | "success" | "warning";

export type Source = {
	url: string;
	title: string;
	network: "linkedin" | "github" | "web";
};
```

Rendering, `agent-panel.tsx:362-398` — chat bubble for `said`, a thin one-line `Marker` for `did`:

```tsx
	return (
		<div className="space-y-1.5">
			<Marker>
				<MarkerIcon>
					{item.pending ? <Spinner /> : <Icon icon={TONE_ICONS[item.tone]} />}
				</MarkerIcon>
				<MarkerContent>{item.label}</MarkerContent>
			</Marker>

			{item.sources.length > 0 ? <Sources sources={item.sources} /> : null}
		</div>
	);
```

`Marker` (`packages/ui/src/components/marker.tsx`) is deliberately quiet:
`"…flex min-h-4 w-full items-center gap-2 text-left text-xs text-muted-foreground…"` — 12px, muted,
16px icon. **Steps are subordinate to speech.** This is the whole visual grammar: prose in bubbles,
work in a muted single-line rail.

> **[PORTS]** `TranscriptItem` is the right abstraction for Collecct: one flat discriminated union,
> `did`-only if your agents never speak. Collecct's rows map 1:1 —
> `{kind:"did", id: event._id, label: VERBS[step] + (detail ? ` — ${detail}` : ""), tone: ok ? "success" : "warning", pending: false, sources: sourcesFrom(event)}`.
> **`pending` is the only stream-dependent field** — see §7 for how to fake it.

---

## 4. Inline questions

### 4a. Finding the question

The agent parks on a tool call carrying `toolMetadata.eve.inputRequest`.
`agent-transcript.ts:175-184`:

```ts
export function pendingQuestion(messages: readonly EveMessage[]) {
	for (const part of messages.at(-1)?.parts ?? []) {
		if (part.type !== "dynamic-tool") continue;

		const request = part.toolMetadata?.eve?.inputRequest;
		if (request) return request;
	}

	return null;
}
```

**Only the last message is scanned** — that is the mechanism by which an answered question disappears.
Test at `agent-transcript.spec.ts:205-216`:

```ts
	it("ignores a question from an earlier message that has moved on", () => {
		const asked = message([
			{ type: "dynamic-tool", toolName: "ask_question",
			  toolMetadata: { eve: { inputRequest: request } } },
		]);
		const answered = message([{ type: "text", text: "Thanks." }]);

		expect(pendingQuestion([asked, answered])).toBeNull();
	});
```

The request shape (`agent-transcript.spec.ts:185-189`):

```ts
	const request = {
		requestId: "req_1",
		prompt: "Which one?",
		options: [{ id: "a", label: "The first" }],
	};
```

### 4b. Rendering it and sending the answer back

`agent-panel.tsx:433-469` in full:

```tsx
function Question({
	question,
	agent,
}: {
	question: NonNullable<ReturnType<typeof pendingQuestion>>;
	agent: ReturnType<typeof useEveAgent>;
}) {
	return (
		<Message>
			<AgentAvatar />
			<MessageContent>
				<Bubble variant="tinted">
					<BubbleContent>{question.prompt}</BubbleContent>
				</Bubble>

				<div className="flex flex-wrap gap-2">
					{(question.options ?? []).map((option) => (
						<Button
							key={option.id}
							variant="outline"
							size="sm"
							onClick={() =>
								void agent.send({
									inputResponses: [
										{ requestId: question.requestId, optionId: option.id },
									],
								})
							}
						>
							{option.label}
						</Button>
					))}
				</div>
			</MessageContent>
		</Message>
	);
}
```

Mounted *outside* the message loop, as its own scroller item — `agent-panel.tsx:239-243`:

```tsx
							{question ? (
								<MessageScrollerItem messageId={question.requestId}>
									<Question question={question} agent={agent} />
								</MessageScrollerItem>
							) : null}
```

Design notes:
- The question is a **tinted bubble** (`variant="tinted"`) — visually distinct from the agent's normal
  `variant="ghost"` prose and the user's `variant="secondary"` bubble.
- Options are plain outline buttons in a wrapping row. **No modal, no dialog, no blocking overlay.**
  It lands in the transcript where the reading eye already is.
- The answer travels on the *same* `agent.send()` channel as a typed message — one send path,
  `{ message }` or `{ inputResponses }`.
- `question.options ?? []` — a question with no options degrades to prose + the free-text composer.

> **[PORTS]** All of this ports. Collecct's version: an `agent_events` row (or a separate
> `agent_questions` collection) with `{ request_id, prompt, options[], answered_at }`; the poll surfaces it;
> the click POSTs `{request_id, option_id}`; the Celery task is blocked on that document (or the answer
> re-enqueues a continuation task). Keep the two rules: **only surface the *newest unanswered* request**
> (their `messages.at(-1)` is the poll-world equivalent of `WHERE answered_at IS NULL ORDER BY created_at
> DESC LIMIT 1`), and **render it inline in the trail, never as a modal**.
> Do optimistically hide it on click and invalidate — at a 3s poll the round trip is otherwise visible.

---

## 5. Suggestions with provenance

Two distinct surfaces, and the split is the good idea.

### 5a. Applied facts → a dotted underline + hover tooltip

`apps/app/components/crm/facts.tsx:32-41`:

```tsx
export function provenanceFor(fact: Fact) {
	return (
		<Provenance
			claim={fact.value}
			reasons={fact.evidence.map((item) => item.detail)}
			observedAt={dateFormat.format(new Date(fact.observedAt))}
			sourceUrl={fact.sourceUrl}
		/>
	);
}
```

`packages/ui/src/components/sourced-value.tsx`:

```tsx
export const SOURCED_VALUE = "underline decoration-dotted underline-offset-4";

export function SourcedValue({ children, source }: { children: React.ReactElement; source: React.ReactNode }) {
	return (
		<Tooltip>
			<TooltipTrigger asChild>{children}</TooltipTrigger>
			<TooltipContent className="block max-w-sm px-3 py-2 text-left">
				{source}
			</TooltipContent>
		</Tooltip>
	);
}

export function Provenance({ claim, reasons, observedAt, sourceUrl }) {
	return (
		<span className="flex flex-col gap-1.5">
			<span className="font-medium">{claim}</span>

			{reasons.length > 0 ? (
				<span className="flex flex-col gap-0.5 opacity-80">
					{reasons.map((reason) => (<span key={reason}>{reason}</span>))}
				</span>
			) : null}

			{observedAt || sourceUrl ? (
				<span className="flex flex-wrap items-center gap-x-2 opacity-60">
					{observedAt ? <span>{observedAt}</span> : null}
					{sourceUrl ? (<span className="truncate">{hostOf(sourceUrl)}</span>) : null}
				</span>
			) : null}
		</span>
	);
}
```

Three tiers of opacity: **claim (100%) → reasons (80%) → when + where (60%)**. Host only, not the full URL.

Wiring — `inline-field.tsx:65-117`. The underline appears only when there is provenance, the field is not
being edited, and there is a value:

```tsx
	const sourced = Boolean(provenance) && !editing && shown !== "";
```
```tsx
				{shown ? (
					<span className={cn("truncate", sourced && SOURCED_VALUE)}>
						{render ? render(shown) : shown}
					</span>
```
```tsx
	const body =
		sourced && provenance ? (
			<SourcedValue source={provenance}>{control}</SourcedValue>
		) : (
			control
		);
```

### 5b. Proposed facts → an inline accept/dismiss row under the field

`facts.tsx:20-30` splits them:

```tsx
export function factsByField(facts: Fact[]) {
	const applied = new Map<string, Fact>();
	const proposed = new Map<string, Fact>();

	for (const fact of facts) {
		const bucket = fact.status === "APPLIED" ? applied : proposed;
		if (!bucket.has(fact.field)) bucket.set(fact.field, fact);
	}

	return { applied, proposed };
}
```

(the server pre-sorts `orderBy: { observedAt: "desc" }` at `contacts.service.ts:185`, so first-wins = newest)

`facts.tsx:43-76`:

```tsx
	const decide = useMutation(
		trpc.contacts.decideFact.mutationOptions({
			onSuccess: (result) => {
				toast.success(
					result.applied
						? "Added to the record."
						: "Dismissed — it won't be suggested again.",
				);
				return cache.contact(contactId, { settle: "record" });
			},
			onError: (error) => toast.error(error.message),
		}),
	);

	return (
		<Suggestion
			value={fact.value}
			rationale={fact.evidence.map((item) => item.detail).join(" · ")}
			pending={decide.isPending}
			onAccept={() => decide.mutate({ factId: fact.id, decision: "accept" })}
			onDismiss={() => decide.mutate({ factId: fact.id, decision: "dismiss" })}
		/>
	);
```

`packages/ui/src/components/suggestion.tsx` — value + rationale left, ✓/✕ icon buttons right,
spinner replaces both while pending:

```tsx
		<div data-slot="suggestion" className="flex min-w-0 items-start gap-2 py-1 text-muted-foreground text-xs">
			<div className="min-w-0 flex-1 space-y-0.5">
				<p className="truncate"><span className="text-foreground">{value}</span></p>
				{rationale ? <p className="text-pretty">{rationale}</p> : null}
			</div>
			<div className="flex shrink-0 items-center gap-1">
				{pending ? (<Spinner className="size-3" />) : (
					<>
						<Button variant="ghost" size="icon-xs" onClick={onAccept} aria-label="Accept"><Icon icon={Checkmark} /></Button>
						<Button variant="ghost" size="icon-xs" onClick={onDismiss} aria-label="Dismiss"><Icon icon={Close} /></Button>
					</>
				)}
			</div>
		</div>
```

Attached per-field via a tiny helper — `contact-sheet.tsx:290-299`:

```tsx
	const agentProps = (field: string) => {
		const fact = applied.get(field);
		const suggestion = proposed.get(field);
		return {
			provenance: fact ? provenanceFor(fact) : undefined,
			suggestion: suggestion ? (
				<FactSuggestion fact={suggestion} contactId={contact.id} />
			) : undefined,
		};
	};
```
```tsx
						{...agentProps("title")}
```

**The suggestion is not in the agent panel at all.** It is on the Overview tab, under the field it
concerns. The panel is where you watch; the record is where you decide.

### 5c. Where "why we think this" comes from

`apps/agent/agent/lib/evidence.ts` — a fixed weighted vocabulary, each kind carrying a
**rep-readable label**:

```ts
export const WEIGHTS: Record<EvidenceKind, Weighting> = {
	"profile.email-match": { weight: 0.95, primary: true, label: "their email address is on the profile" },
	"linkedin.employer-and-name": { weight: 0.85, primary: true, label: "LinkedIn: employer and name both match" },
	"crm.thread-reply": { weight: 0.85, primary: true, label: "they replied on a thread we have" },
	"crm.signature-block": { weight: 0.8, primary: true, label: "their own email signature says so" },
	"github.account-identity": { weight: 0.8, primary: true, label: "the GitHub account names them or their employer" },
	"crm.meeting-attendance": { weight: 0.7, primary: true, label: "they attended a meeting on our calendar" },
	"web.cited-claim": { weight: 0.4, primary: false, label: "a cited web source states it" },
	"handle.name-form": { weight: 0.35, primary: false, label: "the handle is a form of their name" },
	"search.cites-profile": { weight: 0.35, primary: false, label: "a search for them cites this profile" },
	"employer-only": { weight: 0.2, primary: false, label: "the employer matches, the name does not" },
	contradiction: { weight: 0, primary: false, label: "another source disagrees" },
};
```

Noisy-or combination and banding:

```ts
	const combined = evidence.reduce(
		(remaining, item) => remaining * (1 - WEIGHTS[item.kind].weight),
		1,
	);
	let score = Math.min(CEILING, 1 - combined);
	if (contradicted) score = Math.min(score, CONTRADICTED);
```
```ts
export const BAND_FLOOR = { VERIFIED: 0.85, PROBABLE: 0.55, POSSIBLE: 0.3 };

export function bandFor(score: number, hasPrimary: boolean): FactBand | null {
	if (score >= BAND_FLOOR.VERIFIED && hasPrimary) return FactBand.VERIFIED;
	if (score >= BAND_FLOOR.PROBABLE) return FactBand.PROBABLE;
	if (score >= BAND_FLOOR.POSSIBLE) return FactBand.POSSIBLE;
	return null;
}
```

The `rationale` string — **generated from the labels, not written by the LLM**:

```ts
	const list = joinWords(reasons);
	return hasPrimary
		? capitalise(list)
		: `${capitalise(list)} — but nothing that identifies them directly.`;
```

`applies = band === VERIFIED` → written straight to the record with provenance; anything lower →
PROPOSED → the accept/dismiss row. `apps/agent/agent/lib/facts.ts`:

```ts
	const applies = scored.band === FactBand.VERIFIED;
```

Dismissal is permanent and the agent is told so, `facts.ts`:

```ts
			reason: "A person has already dismissed this exact value. Do not offer it again.",
```

The API select exposes exactly what the UI needs — `contacts.service.ts:183-198`:

```ts
			facts: {
				where: { status: { in: [FactStatus.APPLIED, FactStatus.PROPOSED] } },
				orderBy: { observedAt: "desc" },
				select: { id: true, field: true, value: true, score: true, band: true,
					evidence: true, method: true, sourceUrl: true, status: true, observedAt: true },
			},
```

And `decideFact` (`contacts.service.ts:511-565`) is a transaction: supersede the current APPLIED
(`FactStatus.SUPERSEDED, supersededAt`), stamp the decision (`decidedById`, `decidedAt`), write the column.
It 409s on a double-decide:

```ts
		if (fact.status !== FactStatus.PROPOSED) {
			throw new ConflictException("That suggestion has already been settled.");
		}
```

### 5d. Sources on a transcript step

`agent-transcript.ts:149-173`:

```ts
export function sourcesOf(part: EveMessagePart): Source[] {
	const result = output(part);
	if (!result) return [];

	const urls = new Set<string>();
	for (const key of ["sourceUrl", "profileUrl", "url"]) {
		const value = result[key];
		if (typeof value === "string" && /^https?:\/\//.test(value)) {
			urls.add(value);
		}
	}

	return [...urls].map((url) => {
		const title = hostOf(url);
		return {
			url, title,
			network: title.includes("linkedin") ? ("linkedin" as const)
				: title.includes("github") ? ("github" as const)
					: ("web" as const),
		};
	});
}
```

Rendered as small attachment chips with a brand icon — `agent-panel.tsx:356-360` and `:400-421`:

```tsx
const SOURCE_ICONS: Record<Source["network"], CarbonIcon> = {
	linkedin: LogoLinkedin,
	github: LogoGithub,
	web: Document,
};
```
```tsx
					<AttachmentTrigger asChild>
						<a href={source.url} target="_blank" rel="noreferrer noopener">
							<span className="sr-only">Open {source.title}</span>
						</a>
					</AttachmentTrigger>
```

> **[PORTS] — all of §5 ports; none of it touches streaming.**
> Collecct already has `evidence[]` + `rationale` on `contact_facts` and
> `POST /api/intelligence/facts/{id}/decide`. Copy:
> 1. **`rationale` should be assembled from a fixed weighted vocabulary, not free-written by the model.**
>    A `WEIGHTS` table with a `label` per evidence kind gives you a consistent, reviewable sentence and a
>    score in the same pass. Free-text rationales drift and can't be scored.
> 2. **Two surfaces, not one.** Accepted → dotted-underline + hover `Provenance` on the field.
>    Proposed → `Suggestion` row *under that field*. Never a global "review 14 suggestions" inbox.
> 3. **DISMISSED is a fact the agent must read**, so it stops re-proposing. Their `stored:false` reason
>    string closes the loop back into the transcript.
> 4. Auto-apply only above a hard band floor (`VERIFIED && hasPrimary`); everything else is a proposal.
> 5. `settle: "record"` on the cache invalidation — await the record refetch, fire the list refetches
>    behind it, so the row updates instantly and the tables catch up (`apps/app/lib/trpc/cache.ts`).

---

## 6. Empty / loading / failed / ended states

**Six** distinguishable states. Enumerating them is the point.

| State | Trigger | Render |
| --- | --- | --- |
| Loading list | `conversations.isPending` | centered `<Spinner />` |
| Loading thread | `archive.isPending \|\| thread.isPending` | centered `<Spinner />` |
| Empty thread | `messages.length === 0 && !busy` | `<Idle>` — title, blurb, 3 suggestion chips |
| Working (someone else's turn) | `thread.status === "working" && !busy` | footer line, composer locked |
| Ended | `thread.status === "ended"` | footer line + **Start a new conversation** button |
| Failed | `agent.error` | red line + a cause-specific hint |

`agent-panel.tsx:96` / `:154-155` / `:168-174`:

```tsx
	if (conversations.isPending) return <Loading />;
```
```tsx
	if (conversation && (archive.isPending || thread.isPending))
		return <Loading />;
```
```tsx
function Loading() {
	return (
		<div className="flex flex-1 items-center justify-center">
			<Spinner />
		</div>
	);
}
```

**Empty state carries the pitch and the first move** — `agent-panel.tsx:298-333`, copy from
`agent-record.ts`:

```tsx
			<EmptyContent layout="row">
				{copy.suggestions.map((suggestion) => (
					<Button key={suggestion} variant="outline" size="sm" onClick={() => onAsk(suggestion)}>
						{suggestion}
					</Button>
				))}
			</EmptyContent>
```

`agent-record.ts:16-56` — per-record copy, so the empty state is never generic:

```ts
	contact: {
		title: "Ask about this person",
		blurb: "Every step is shown as it happens — including the leads it throws away.",
		placeholder: "Are they still there?",
		suggestions: ["Who is this person?", "Are they still there?", "What should I know before a call?"],
	},
	company: {
		title: "Ask about this company",
		blurb: "It reads their site and our own history with them, and shows its working.",
		placeholder: "What do they sell?",
		suggestions: ["What do they do?", "Who do we know here?", "What has changed recently?"],
	},
	deal: {
		title: "Ask about this deal",
		blurb: "It can read the thread, the meetings and the people on both sides of it.",
		placeholder: "Where has this stalled?",
		suggestions: ["Where does this stand?", "Who else should be involved?", "What is the risk here?"],
	},
```

A test forbids inlining that copy — `agent-session.spec.ts:149-156`:

```ts
		it("takes its copy from the record, never from a literal", () => {
			for (const kind of ["contact", "company", "deal"] as const) {
				const copy = recordCopy(kind);
				for (const literal of [copy.title, copy.blurb, copy.placeholder]) {
					expect(source()).not.toContain(literal);
				}
			}
		});
```

**Working, but not by you** — `agent-panel.tsx:253-258`:

```tsx
			{thread?.status === "working" && !busy ? (
				<p className="border-t px-5 py-2 text-muted-foreground text-xs">
					Still working on the last question. Your next one can go in when it
					finishes.
				</p>
			) : null}
```

**Ended** — `agent-panel.tsx:260-269`:

```tsx
			{ended ? (
				<div className="flex items-center justify-between gap-3 border-t px-5 py-2">
					<p className="text-muted-foreground text-xs">
						This conversation has ended.
					</p>
					<Button variant="outline" size="sm" onClick={onNewThread}>
						Start a new conversation
					</Button>
				</div>
			) : null}
```

`docs/agent.md:659-666`:

> **An ended thread gets a button, not a locked box.** Ended and working both disable the composer and mean
> completely different things: one is a wait of seconds, the other is permanent. `composerState()` keeps
> them apart, and an ended thread offers **Start a new conversation** […] The transcript stays on screen
> throughout.

`agent-session.ts:83-93`:

```ts
export function composerState(
	thread: Thread | undefined,
	busy: boolean,
): ComposerState {
	const ended = thread?.status === "ended";

	return {
		ended,
		locked: busy || ended || thread?.status === "working",
	};
}
```

Note `offline` is **not** locked — `agent-session.spec.ts:88-93`:

```ts
	it("lets somebody type when the agent could not be reached", () => {
		expect(composerState({ status: "offline", events: [] }, false)).toEqual({
			locked: false, ended: false,
		});
	});
```

**Failure with a diagnosis** — `agent-panel.tsx:335-348`:

```tsx
function Failure({ message }: { message: string }) {
	const hint = message.includes("not reachable")
		? "Start it with `bun run dev`, or check AGENT_URL."
		: message.includes("not configured")
			? "Set AGENT_BRIDGE_SECRET for both the app and the agent."
			: null;

	return (
		<div className="border-t px-5 py-3 text-xs">
			<p className="text-destructive">{message}</p>
			{hint ? <p className="text-muted-foreground text-xs">{hint}</p> : null}
		</div>
	);
}
```

The error strings it keys on come from the proxy, `app/eve/v1/[...path]/route.ts:11-14` and `:82-89`:

```ts
			{ error: "The research agent is not configured for this install." },
```
```ts
				error: "The research agent is not reachable.",
```

**Note: the failure banner is a footer line, not a replacement.** The transcript stays on screen.
That is the rule throughout — *nothing ever wipes the trail*.

### The "long job elsewhere in the app" pattern

For work that isn't in the panel, `detail-sheet.tsx:302-330`:

```tsx
export function DetailSheetPending({ fields, running }: { fields: string[]; running: boolean }) {
	if (fields.length === 0) return null;
	return (
		<div className="flex flex-col gap-1.5 rounded-md bg-muted/40 p-3">
			<div className="flex items-center gap-2">
				<span aria-hidden className={cn("size-1.5 shrink-0 rounded-full",
					running ? "bg-primary" : "bg-muted-foreground")} />
				<span className="font-medium text-xs">
					{running ? "Agent is researching" : "Not known yet"}
				</span>
			</div>
			<p className="text-pretty text-muted-foreground text-xs/5">{fields.join(", ")}</p>
		</div>
	);
}
```

— i.e. **name the gaps and say whether anything is being done about them.** And the status chip vocabulary,
`enrichment-status.tsx:7-22`:

```ts
const PRESENTATION: Record<EnrichmentStatus, { label: string; tone: StatusTone; busy?: boolean }> = {
	PENDING: { label: "Not researched", tone: "neutral" },
	RUNNING: { label: "Researching", tone: "info", busy: true },
	COMPLETE: { label: "Enriched", tone: "success" },
	FAILED: { label: "Enrichment failed", tone: "error" },
	SKIPPED: { label: "Nothing found", tone: "neutral" },
};

const QUEUED = { label: "Queued", tone: "neutral" as StatusTone, busy: false };
```

`SKIPPED: "Nothing found"` is a *distinct, non-error* terminal state. And the kick-off toast sets the
expectation explicitly — `enrichment-actions.tsx:26-33`:

```tsx
					result.queued
						? "Looking it up — this page will update when it finishes."
						: "Already running.",
```

> **[PORTS] — this whole section is the most directly applicable to Collecct.** Your Celery panel needs the
> *same six states*, and the states you'll be tempted to collapse are the ones that matter: **queued ≠
> running**, **ended ≠ working**, **offline ≠ failed**, **"nothing found" ≠ "failed"**.
> Copy `EnrichmentIndicator`'s `PRESENTATION` map verbatim as your Celery-state → chip table, and copy the
> "this page will update when it finishes" toast — for a minutes-long Celery job it is the single cheapest
> thing you can ship.

---

## 7. What makes it feel LIVE

Seven mechanisms, ranked by how much they buy for a **poll-based** trail.

**1. Incremental append with stable ids.** `partId` prefers `toolCallId`; steps keep identity as they
resolve, so a resolving step is an in-place icon swap (spinner → ✓/⚠), never a re-render flash.
**[PORTS]** — key on your `agent_events._id`.

**2. Auto-scroll that yields.** `agent-panel.tsx:221-249`:

```tsx
			<MessageScrollerProvider autoScroll defaultScrollPosition="end">
				<MessageScroller className="flex-1">
					<MessageScrollerViewport>
						<MessageScrollerContent className="gap-3 px-5 py-4">
```
```tsx
					<MessageScrollerButton />
```

`docs/agent.md:667-673`:

> **`autoScroll` and nothing else.** The scroller is a state machine (`following-bottom`,
> `free-scrolling`, `anchored-to-message`) and `scrollAnchor` selects the third, which *stops it following
> the bottom* — the answer then streams below the fold while the modes fight over each new row. Left alone,
> `autoScroll` follows the tail while the reader is at the bottom and releases the moment they scroll away,
> which lights the jump-to-end button.

`docs/agent.md:674-677`:

> **One `MessageScrollerItem` per message, not per part.** The row is what the scroller measures; a row per
> tool call adds a boundary every few hundred milliseconds during an answer.

**[PORTS]** — with a 3s poll appending 1–3 rows, follow-the-tail + a jump-to-end button is exactly right,
and cheaper to get right than with a stream. **Do not add `scrollAnchor`.**

**3. A pending spinner *inside* the step row.** `agent-panel.tsx:390`:

```tsx
					{item.pending ? <Spinner /> : <Icon icon={TONE_ICONS[item.tone]} />}
```

**[STREAM-ONLY as written]** — `pending` comes from `state === "input-streaming" | "input-available"`,
which a Celery task never reports.
**Poll-world substitute:** when `thread.status === "working"`, append a **synthetic trailing pending row**
after the last real event. It occupies the same visual slot, keeps the spinner at the tail where the eye
is, and costs one line:
```ts
const items = [...real, ...(status === "working" ? [{kind:"did", id:"__working", label:"Working…", tone:"neutral", pending:true, sources:[]}] : [])];
```
Even better if your Celery task writes a `step.started` row before each step and the FE renders the *last*
row as pending while `status === "working"` — then the spinner carries a real label ("Searching SAM.gov…")
instead of a generic one. **This is the single highest-leverage change for making a poll feel like a
stream.**

**4. The send button is the working indicator.** `agent-panel.tsx:206` and `:290`:

```tsx
	const busy = agent.status === "submitted" || agent.status === "streaming";
```
```tsx
					{busy ? <Spinner /> : <Icon icon={Send} />}
```

Plus the composer clears optimistically *before* the send resolves — `agent-panel.tsx:212-217`:

```tsx
	const ask = (message: string) => {
		if (!message.trim() || locked) return;
		opening.current ||= message.trim();
		setDraft("");
		void agent.send({ message: message.trim() });
	};
```

**[PORTS]** — clear the input immediately, spin the button, optimistically append the user's own row.
At a 3s poll this is *mandatory*, not polish.

**5. Status chips with a pulsing dot / spinner.** `packages/ui/src/components/status-indicator.tsx`:

```tsx
			{busy ? (
				<Spinner className="size-3 shrink-0" />
			) : (
				<IndicatorDot tone={tone} color={color} pulse={pulse} bloom={bloom} aria-hidden="true" />
			)}
			<span className="truncate">{label}</span>
```
```tsx
				pulse && "animate-pulse",
				bloomClass(bloom),
```

**[PORTS]** — the dot has a `bloom` glow and an optional `animate-pulse`; a busy chip swaps to a spinner
entirely. Cheap, and reads as alive at a glance from across a table.

**6. Nothing ever blanks.** `placeholderData: (previous) => previous` on polled lists
(`companies-table.tsx:149`), the transcript persisting through failure and through "ended", and the
`archive → offline` fallback. A polled surface that flashes a skeleton every 3s reads as *broken*, not live.
**[PORTS] — critical.** Use `placeholderData: keepPreviousData` on the events query.

**7. The whole product polls at one cadence.** `WORKING_POLL_MS = 3000` (`agent-panel.tsx:118`),
`ENRICHMENT_POLL_MS = 3_000` (`enrichment-status.tsx:52`), used on the contact sheet
(`contact-sheet.tsx:90-95`), the company sheet (`company-sheet.tsx:172-176`) and the companies table
(`companies-table.tsx:151-156`) — all with the identical self-terminating shape:

```tsx
		refetchInterval: (current) => {
			const record = current.state.data;
			return record && isEnriching(record.enrichmentStatus, record.queued)
				? ENRICHMENT_POLL_MS
				: false;
		},
```

**[PORTS]** — one constant, one predicate, `false` to stop. Collecct should have exactly one
`AGENT_POLL_MS` and one `isWorking(status)`.

---

## 8. Verdict for Collecct: what to take, what to skip

### Take (all poll-safe)

1. **`agent-transcript.ts` as a module.** A `VERBS`/`STEP_VERBS` table, a `describe()` that splices the
   `detail`, an `outcomeTone()` that maps `ok:false` → **warning**, a `sourcesOf()` that scrapes URLs, and a
   flat `TranscriptItem` union. ~200 lines, entirely framework-free, and testable without a browser.
2. **The `classify()` status machine + a 90s abandonment cutoff**, adapted: task-state first, terminal event
   second, event recency third, "working" when undated. Plus `composerState()` splitting *ended* from *busy*.
3. **The self-terminating 3s poll** (`refetchInterval: data => working ? 3000 : false`), one constant
   app-wide, `placeholderData: keepPreviousData`, `refetchOnWindowFocus: false`.
4. **Full-list-every-poll + render as a pure function.** No local accumulation.
5. **The synthetic trailing pending row** while `status === "working"` — §7.3. This is what buys "alive".
6. **Six named states**, each with its own copy. Especially: *queued*, *nothing found*, *offline*, *ended*.
7. **Nothing ever wipes the trail** — errors are footers, not replacements.
8. **The two-surface provenance split**: dotted underline + hover `Provenance` for accepted, inline
   `Suggestion` (✓/✕, spinner while pending) under the field for proposed. Never a suggestions inbox.
9. **A weighted evidence vocabulary generating `rationale`**, not an LLM-written sentence.
10. **Inline questions in the trail**, never modal; newest-unanswered only; optimistic hide on answer.
11. **URL-held view state** (`?thread=`), landed-on captured once, upsert by durable id.
12. **The test that scans the tools/steps directory and fails if a step has no English sentence.**

### Skip / re-derive

- **`useEveAgent`, `initialSession`, `continuationToken`, `streamIndex`** — eve-specific. Collecct's
  "can I send?" is "is a Celery task running for this subject?".
- **`part.state === "input-streaming"`** and the `Thread key={working|settled}` remount — artefacts of
  seeding a streaming hook. A pure render from polled events needs neither.
- **`keepMounted` / `forceMount`** — worth doing, but for the cheap reason (avoid a re-load flash), not the
  expensive one (a dropped socket). Poll state lives in the DB.
- **`ai-elements`** — not used here either. Read `.agents/skills/ai-elements/references/{task,chain-of-thought,tool,sources,inline-citation,confirmation}.md`
  for naming and layout ideas, then build the four primitives (`Message`, `Bubble`, `Marker`,
  `MessageScroller`) into your own design system, as this repo did.
- **The `/eve/v1/*` same-origin proxy + HS256 bridge token** — only relevant if Collecct's agent runs as a
  separate process the browser must reach. If the trail is served by the existing FastAPI under the same
  auth, there is nothing to port.

### The one design sentence to carry over

`apps/app/lib/agent-record.ts:21-23`:

```ts
		blurb:
			"Every step is shown as it happens — including the leads it throws away.",
```

The panel is not a chat log. It is an **audit trail that happens to be legible**, and the refusals are the
part that earns trust. Collecct's `ok:false` rows are the most valuable thing in `agent_events` — render
them as warnings with their `detail` attached, never hide them.
