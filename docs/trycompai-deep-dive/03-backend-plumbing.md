# 03 — Backend Plumbing (NestJS API, tRPC, Agent Bridge, Gmail/Calendar Ingestion)

Repo under study: `.../scratchpad/crm` — **"CRM" by trycompai**, an open-source *agentic-first* CRM.
Tagline from `README.md`: *"A durable research agent is the product. The database is just where it writes things down."*

**Stack (their side):** Bun runtime, NestJS (Express adapter) + `nestjs-trpc`, Prisma + Postgres, Better Auth (Google OAuth + `organization` + `sso` plugins), `@nestjs/cache-manager` (Redis optional), Vercel Cron. The research agent (`apps/agent`, "eve") is a **separate deployment** that leases work from a Postgres table.

**Monorepo layout (relevant apps/packages):**
- `apps/api` — the NestJS HTTP/tRPC API (my focus).
- `apps/app` — Next.js frontend; also hosts the **agent proxy route** that mints signed bridge tokens.
- `apps/agent` — the "eve" research agent (TypeScript, separate process on `:2000`); owns all vendor clients, the confidence model, and the fact writes.
- `packages/auth` — Better Auth setup (`@crm/auth`).
- `packages/db` — Prisma schema + client (`@crm/db`), shared by API **and** agent.

> **TARGET = "Collecct"** (govcon BD CRM): Python + FastAPI-style routers + Agno agents + Celery + FalkorDB/MongoDB/Redis + Next.js, Microsoft (Outlook+SharePoint via Composio) instead of Google, multi-tenant orgs/RBAC, Celery beat for cron. Translation notes are inline as **[COLLECCT]** callouts.

---

## 0. The load-bearing design rule (read this first)

The entire backend is organized around one rule, stated in `docs/api.md:42-67` (heading **"Intelligence never lives in the API"**):

```
This is an **agentic-first platform**. The API serves HTTP, auth, tRPC and the
Google sync. It does not research, enrich, score, summarise, match identities or
decide anything about a person or a company — not as a fallback, not "just the
cheap bit", not behind a flag. That work belongs to the eve agent in
`apps/agent`, which owns the vendor clients, the confidence model and the writes.

Nest's half of the contract is to **report that something happened** — a thread
was ingested, a company was created, an attendee is unknown — and let the agent
decide what it means. A Nest service that calls an enrichment API is a bug...
`apps/api/src/enrichment/` is gone. What replaced it is
`apps/api/src/agent/agent-trigger.service.ts` — one service with one verb, which
writes an `AgentTask` row saying *this happened* and why it might matter. A row
rather than an HTTP call: the agent leases work from that table already, so the
row *is* the message, and it survives the agent being down, redeployed, or
slower than the request that produced it.
```

**How it's enforced in code (not just prose):**
1. There is **no `apps/api/src/enrichment/` directory** — it was deleted. The API's only agent-facing surface is `apps/api/src/agent/` (three tiny services, below).
2. The agent module (`apps/api/src/agent/agent.module.ts`) exposes exactly three providers: `AgentTriggerService` (writes a queue row), `AgentQueueService` (reads "is this queued?"), `ResearchKeyService` (delegates a key-check to the agent). None call a vendor.
3. Even things that *look* like intelligence are pushed over the bridge. Verifying a Context.dev API key would mean a vendor client in the API, so `ResearchKeyService` (`research-key.service.ts:11-16`) instead asks the agent:
   ```
   Checking a Context key means calling Context, and a vendor client in this
   process is the thing `docs/api.md` forbids. So the agent — which owns that
   client... is asked, and this service only carries the question there and the
   answer back.
   ```
4. **Signature-block parsing** — an "intelligence" task — is NOT in the API. The API's MIME code (`apps/api/src/google/mime.ts`) only strips quoted history and flattens HTML. Extracting a signature into a *fact* is an agent evidence-kind (`crm.signature-block`, `apps/agent/agent/lib/evidence.ts:38-42`). The Gmail sync stores the raw body; the agent reads it and decides.
5. The historical justification is a real outage: two identity matchers were copied across `apps/api` and `apps/agent` and drifted until "one of them matched every employer on earth" (`docs/api.md:53-56`). The fix was to make identity-matching single-sourced in the agent.

> **[COLLECCT]** This is the single most important pattern to preserve. In Collecct the equivalent boundary is **FastAPI routers/services must not call Agno agents, Explorium, SAM.gov, or any LLM directly.** They write a row (Mongo doc / FalkorDB node) or push a Celery task *description*, and the Agno agents (Analyst/Relation/Mail) lease and interpret it. Your `backend/utils/company_enrich.py` (currently uncommitted, per git status) is exactly the kind of thing this repo would call a bug if it lived in the request-serving layer — it belongs behind the agent/worker boundary, not in a router path.

---

## 1. Architecture

### 1.1 Module structure (`apps/api/src`)

Bootstrapping chain: `main.ts` → `create-app.ts` → `AppModule`.

`apps/api/src/main.ts:4-16` — trivial bootstrap; the real config is in `create-app.ts`:
```ts
export async function createApp(): Promise<NestExpressApplication> {
	const app = await NestFactory.create<NestExpressApplication>(
		AppModule,
		new ExpressAdapter(),
		{ bodyParser: false, logger: new ContextLogger() },   // bodyParser:false — Better Auth needs raw body
	);
	app.use(helmet());
	app.useGlobalPipes(
		new ValidationPipe({
			whitelist: true,
			forbidNonWhitelisted: true,
			transform: true,
			transformOptions: { enableImplicitConversion: true },
		}),
	);
	return app;
}
```
(`apps/api/src/create-app.ts:11-29`) — note there's a **second entrypoint** `apps/api/api/index.ts` (Vercel serverless: builds the app once, caches the Express instance).

`apps/api/src/app.module.ts:28-57` — feature modules, one folder per domain. **`LoggingModule` must be first** (the `docs/api.md:26-30` rule: Better Auth mounts its handler during module config, before Nest middleware, so logging has to be installed before it):
```ts
imports: [
	LoggingModule,                                   // MUST stay first
	ConfigModule.forRoot({ isGlobal: true, cache: true, validate: validateEnv }),
	AppCacheModule,
	DatabaseModule,
	CrmModule,
	BetterAuthModule.forRoot({ auth, middleware: logAuthRoute }),
	AuthModule, HealthModule, TrpcModule,
	UsersModule, CompaniesModule, ContactsModule, ConversationsModule,
	DealsModule, ActivitiesModule, DashboardModule, SearchModule,
	GoogleModule, SettingsModule, WorkspaceModule, SsoModule, BackfillModule,
],
```

**Per-domain folder shape** (consistent across `contacts/`, `companies/`, `deals/`, `google/`, etc.):
- `*.module.ts` — Nest module wiring.
- `*.router.ts` — the tRPC "controller": thin, validates zod input, delegates to service.
- `*.service.ts` — all Prisma work + business logic; throws Nest `HttpException`s.
- `*.contracts.ts` — zod schemas + inferred types.

**Cross-cutting infra folders:** `trpc/` (context, middlewares, error formatting, `list-input` helpers), `logging/` (ContextLogger, request-id ALS, interceptors), `crm/` (shared value canonicalizers, activity-stamp recompute, enrichment-log), `agent/` (the queue-write boundary), `google/` (all ingestion), `config/env.validation.ts`, `cache/`, `database/`, `health/`, `backfill/`.

> **[COLLECCT]** Their "one folder per domain, router = thin, service = logic, contracts = schemas" maps cleanly onto FastAPI: `routers/<domain>.py` (thin, Pydantic-validated) + `services/<domain>.py` (Mongo/Falkor logic) + `models/<domain>.py` (Pydantic). You already have `backend/models/verdict.py` etc. Keep the router→service split; don't let Mongo queries leak into route handlers.

### 1.2 tRPC wired end-to-end (router → generated type → client)

**"tRPC is the data surface; REST is for auth and health"** (`docs/api.md:299-330`). The *only* REST controllers are `/api/auth/*` (Better Auth), `/health`, and `/internal/sync/google` (cron). Everything the app reads/writes goes through `nestjs-trpc` routers mounted at `/api/trpc`.

**Module registration** (`apps/api/src/trpc/trpc.module.ts:11-31`):
```ts
TRPCModule.forRoot({
	basePath: "/api/trpc",
	context: TrpcContext,
	logger: new ContextLogger(),
	errorFormatter: formatTrpcError,
	onError: TrpcErrorHandler,
	globalMiddlewares: [LoggingMiddleware, DomainErrorMiddleware],
}),
```

**Context** runs once per request and resolves the Better Auth session from the raw Node headers (`apps/api/src/trpc/trpc.context.ts:8-18`):
```ts
@Injectable()
export class TrpcContext implements TRPCContext {
	async create(opts: ContextOptions): Promise<BaseTrpcContext> {
		const req = "req" in opts ? opts.req : undefined;
		const session = req
			? await auth.api.getSession({ headers: fromNodeHeaders(req.headers) }).catch(() => null)
			: null;
		return { req, session };
	}
}
```
Context types (`trpc/context.types.ts`): `BaseTrpcContext = { req?, session }`; `AuthedTrpcContext = BaseTrpcContext & { user }`.

**A representative router** — `apps/api/src/contacts/contacts.router.ts:22-66` (VERBATIM, this is the canonical pattern):
```ts
@Router({ alias: "contacts" })
@UseMiddlewares(AuthMiddleware)
export class ContactsRouter {
	constructor(
		@Inject(ContactsService) private readonly contacts: ContactsService,
	) {}

	@Query({ input: contactListInput })
	async list(@Input() input: z.infer<typeof contactListInput>) {
		return this.contacts.list(input);
	}

	@Query({ input: contactIdInput })
	async byId(@Input("id") id: string) {
		return this.contacts.byId(id);
	}

	@Mutation({ input: contactCreateInput })
	async create(@Input() input: z.infer<typeof contactCreateInput>) {
		return this.contacts.create(input);
	}

	@Mutation({ input: contactUpdateArgs })
	async update(@Input() input: z.infer<typeof contactUpdateArgs>) {
		return this.contacts.update(input.id, input.data);
	}

	@Mutation({ input: contactIdInput })
	async delete(@Input("id") id: string) {
		return this.contacts.delete(id);
	}

	@Mutation({ input: contactIdInput })
	async enrich(@Input("id") id: string) {
		return this.contacts.enrich(id);
	}

	@Mutation({ input: factDecisionInput })
	async decideFact(
		@Ctx() ctx: AuthedTrpcContext,
		@Input() input: z.infer<typeof factDecisionInput>,
	) {
		return this.contacts.decideFact(input, ctx.user.id);
	}
}
```

**Conventions enforced (`docs/api.md:305-330` + the `nestjs-trpc` SKILL):**
- **One router per module**, file named `*.router.ts` so the codegen glob finds it. `@Router({ alias })` sets the client path segment. `@UseMiddlewares(AuthMiddleware)` at the **class** guards every procedure — *"A router with no `AuthMiddleware` is public — there is no other guard."*
- **Routers are thin;** Prisma lives in the service.
- **Services throw Nest `HttpException`** (`NotFoundException`, `ConflictException`, …); `DomainErrorMiddleware` maps them to tRPC codes (so the service doesn't know it's over tRPC).
- **The router type is generated, not hand-written.** `bun run --filter=api trpc:generate` writes `src/generated/server.ts`; the app imports `type { AppRouter } from "api/app-router"`. Critical deploy gotcha (`docs/api.md:324-330`): **`src/generated/server.ts` is committed** and `build` must *never* regenerate it — the generator ships a native binary needing GLIBC 2.39 (newer than Vercel's build image). Only `check-types` and `dev` run the generator.

**The two-systems mental model** (`nestjs-trpc` SKILL.md:23-35): runtime (Nest DI — router must be in `providers`) and type-gen (Rust CLI static analysis — decorators must be literal) **fail independently**. A router missing from `providers` = 404 at runtime; a router the CLI didn't see = client with no types.

**Shared list contract** (`apps/api/src/trpc/list-input.ts:3-9`) — every list procedure takes this and returns `{ rows, total, facetCounts }`; filtering/sorting/pagination happen in Prisma (`docs/api.md:314-318` — "Never return a whole table and filter in the browser"; never interpolate `sort` into a Prisma field — resolve via `resolveOrderBy` against an allow-list, see `contacts.service.ts:93-104` `SORTABLE`):
```ts
export const listInput = z.object({
	q: z.string().default(""),
	sort: z.string().default(""),
	dir: z.enum(["asc", "desc"]).default("asc"),
	page: z.number().int().min(1).default(1),
	pageSize: z.number().int().min(1).max(100).default(25),
});
```

**Middlewares** (order: global → router → procedure → handler):
- `AuthMiddleware` (`trpc/middlewares/auth.middleware.ts:12-26`): throws `TRPCError({ code: "UNAUTHORIZED" })` if no `ctx.session.user`, else injects `user` into ctx and stamps the request-id logger:
  ```ts
  const user = ctx.session?.user;
  if (!user) throw new TRPCError({ code: "UNAUTHORIZED" });
  setRequestUserId(user.id);
  const nextCtx: AuthedTrpcContext = { ...ctx, user };
  return opts.next({ ctx: nextCtx });
  ```
- `DomainErrorMiddleware` (`trpc/middlewares/domain-error.middleware.ts:38-58`): global; catches an `HttpException` in the failed result's `cause` and re-throws as a mapped `TRPCError` (`statusToTrpcCode`: 400→BAD_REQUEST, 401→UNAUTHORIZED, 403→FORBIDDEN, 404→NOT_FOUND, 409→CONFLICT, 429→TOO_MANY_REQUESTS, else INTERNAL_SERVER_ERROR).

> **[COLLECCT]** FastAPI equivalent: `AuthMiddleware` → an auth dependency (`Depends(get_current_user)`) that 401s and injects the user + org into request state; `DomainErrorMiddleware` → a FastAPI exception handler mapping your domain exceptions to HTTP codes. The `listInput`/`{rows,total,facetCounts}` contract is worth copying verbatim as a Pydantic base for every list endpoint (you already have opportunity pagination + faceted filters per memory). **Note their explicit multi-tenancy stance is the opposite of yours** — see §5.4; you must thread `organizationId` everywhere they deliberately don't.

### 1.3 Freshness / caching model
No HTTP response cache in front of tRPC. Freshness is TanStack Query's job on the client (invalidate query keys in `onSuccess` via a central `useCrmCache()`), and `@nestjs/cache-manager` is used **deliberately per-value** by services that opt in — never as a global interceptor (`docs/api.md:477-532`). Reference pattern = `AuthService.getProfile` (§5.5). Background writes the browser can't see (enrichment finishing) are **polled**, not invalidated (`refetchInterval` while status is `PENDING`/`RUNNING`).

---

## 2. Agent ↔ App boundary (dispatch, durable session bridge, signed tokens)

This is the crux. There are **two distinct paths** across the `apps/api`/`apps/app` ↔ `apps/agent` boundary, and they use the **same secret (`AGENT_BRIDGE_SECRET`) two different ways.**

```
                         AGENT_BRIDGE_SECRET
                                 │
        ┌────────────────────────┴─────────────────────────┐
        │ (A) machine→machine: raw Bearer                    │ (B) per-user chat: HS256 JWT
        │                                                    │
  apps/api  ──POST /internal/crm/dispatch──►  apps/agent   apps/app (Next route) ──►  apps/agent
  (poke, verify-key)   Authorization: Bearer <SECRET>       /eve/v1/* proxy   Authorization: Bearer <JWT-signed-with-SECRET>
```

### 2.1 How the API kicks off / schedules agent work

**The queue row IS the message.** The API never HTTP-calls the agent to *ask* it to research; it writes an `AgentTask` row and (best-effort) "pokes" the agent to wake up early.

`apps/api/src/agent/agent-trigger.service.ts` — one service, verbs like `companyCreated`, `contactCreated`, `meetingSoon`, `workspaceChanged`, `backfill`. Each calls the private `enqueue()` (`:146-193`), which **de-dupes on `(kind, subject, finishedAt IS NULL)`** then inserts and pokes:
```ts
private async enqueue(task: { contactId?; companyId?; kind; reason; priority; budget }): Promise<void> {
	const pending = await this.db.agentTask.findFirst({
		where: { kind: task.kind, finishedAt: null,
			...(task.contactId ? { contactId: task.contactId } : {}),
			...(task.companyId ? { companyId: task.companyId } : {}) },
		select: { id: true },
	});
	if (pending) return;                                   // already queued — no duplicate
	await this.db.agentTask.create({ data: {
		contactId: task.contactId ?? null, companyId: task.companyId ?? null,
		kind: task.kind, reason: task.reason, priority: task.priority,
		budget: task.budget, dueAt: new Date() } });
	this.logger.log({ message: "Agent task queued", kind: task.kind, ... });
	this.poke();
}
```

**The poke** (`agent-trigger.service.ts:195-215`) — fire-and-forget, 2s timeout, failure is fine because the cron will pick the row up anyway:
```ts
private poke(): void {
	const agent = bridge();
	if (!agent) return;                                    // no secret → no bridge → no poke
	const missed = (error) => this.logger.debug({
		message: "Agent poke did not land; the cron will pick this up", ... });
	try {
		void fetch(agent.url("/internal/crm/dispatch"), {
			method: "POST",
			headers: { authorization: `Bearer ${agent.secret}` },   // (A) RAW SECRET as bearer
			signal: AbortSignal.timeout(POKE_TIMEOUT_MS),
		}).catch(missed);
	} catch (error) { missed(error); }
}
```

**The bridge helper** (`apps/api/src/agent/bridge.ts:13-20`) — "unset secret means there is no bridge, not an open one":
```ts
export function bridge(): Bridge | null {
	const secret = process.env.AGENT_BRIDGE_SECRET?.trim();
	if (!secret) return null;
	const base = process.env.AGENT_URL?.trim() || DEFAULT_AGENT_URL;   // http://127.0.0.1:2000
	return { url: (path) => new URL(path, base), secret };
}
```

**Task kinds & priorities** (`packages/db/src/agent-tasks.ts:1-32`):
```ts
export const TASK_KINDS = ["brand","portrait","meeting-prep","identify","profile",
	"recheck","company-profile","workspace-profile"] as const;
export const DIRECT_KINDS = ["brand","portrait"] as const;          // cheap, no LLM session
export const PRIORITY = {
	brand: 900, portrait: 800, workspace: 500, requested: 300,
	meeting: 200, identify: 100, sweep: 50, companyProfile: 40, recheck: 0,
} as const;
```
Budget is a per-task research spend cap (e.g. `meetingSoon` gets `budget: 10`, `brand` gets `2`).

**The agent side of the poke** (`apps/agent/agent/channels/crm.ts:33-48`) — verifies the **raw** secret and drains the queue:
```ts
POST("/internal/crm/dispatch", async (request, { send, waitUntil }) => {
	if (!authorised(request)) return new Response("Unauthorized", { status: 401 });
	waitUntil(
		drainAll((task) =>
			send(brief(task), {
				auth: taskAuth(task),
				continuationToken: taskToken(task.id),        // ties agent session → task row
			}),
		),
	);
	return new Response(null, { status: 202 });
}),
```
where `authorised` (`crm.ts:10-15`) is a plain equality check on the raw secret:
```ts
function authorised(request: Request): boolean {
	const secret = process.env.AGENT_BRIDGE_SECRET?.trim();
	if (!secret) return false;
	return request.headers.get("authorization") === `Bearer ${secret}`;
}
```

**The lease** (agent side, `apps/agent/agent/lib/tasks.ts:41-60`) — a Postgres `UPDATE ... FROM (SELECT ... FOR UPDATE SKIP LOCKED)` so two dispatchers never grab the same row, ordered by priority then dueAt, bounded by `MAX_ATTEMPTS = 3`:
```sql
UPDATE "agentTask" AS t
SET "leasedUntil" = ${until},
    "startedAt" = COALESCE(t."startedAt", ${now}),
    "attempts" = t."attempts" + 1
FROM (
	SELECT t2.id FROM "agentTask" AS t2
	WHERE t2."finishedAt" IS NULL
		AND t2."dueAt" <= ${now}
		AND (t2."leasedUntil" IS NULL OR t2."leasedUntil" < ${now})
		AND t2."attempts" < ${MAX_ATTEMPTS}
		AND ${match}                                       -- kind IN / NOT IN (…)
	ORDER BY t2."priority" DESC, t2."dueAt" ASC
	LIMIT ${limit}
	FOR UPDATE SKIP LOCKED
) AS due
WHERE t.id = due.id
RETURNING t.id, t."contactId", t."companyId", t.kind, t.reason, t.budget, t.attempts, t.priority, t."dueAt";
```
Two lanes drain in parallel (`apps/agent/agent/lib/dispatch.ts:132-137`): a **visible lane** (`DIRECT_KINDS` = brand/portrait, run inline, no LLM) and a **research lane** (everything else, starts a durable agent session). `drainAll` is wrapped in `collapsing(...)` so overlapping pokes coalesce.

`AgentTask` schema (`packages/db/prisma/schema.prisma:312-337`) — note **`contactId`/`companyId` are bare columns with NO foreign key** ("they outlive the records they name on purpose, so the queue survives a redeploy" — `docs/api.md:435-440`; a record delete must therefore `deleteMany` its tasks itself, see §4.3):
```prisma
model AgentTask {
  id         String  @id @default(cuid())
  contactId  String?
  companyId  String?
  kind       String
  reason     String
  priority   Int @default(0)
  budget     Int @default(4)
  attempts   Int @default(0)
  dueAt       DateTime
  leasedUntil DateTime?
  sessionId  String?
  startedAt  DateTime?
  finishedAt DateTime?
  outcome    String?
  createdAt  DateTime @default(now())
  @@index([dueAt, leasedUntil])
  @@index([contactId])
  @@map("agentTask")
}
```

> **[COLLECCT]** This maps almost 1:1 onto **Celery**, and it's cleaner than a naive `.delay()`:
> - The `AgentTask` table = your Celery task backlog, but **persisted and de-duped in your own DB (Mongo/Postgres)**, not just in Redis. Consider a `agent_tasks` collection with the same `(kind, subjectId, finishedAt)` de-dupe and `priority`/`budget`/`attempts`/`leasedUntil` fields.
> - The **poke** = `celery_app.send_task(...)` to wake a worker immediately; the **cron** = Celery beat sweeping "due" rows every N minutes. Keep both: the poke gives snappy UX, beat guarantees eventual pickup if the poke is lost.
> - `FOR UPDATE SKIP LOCKED` leasing = Celery's own visibility-timeout/ack semantics; if you keep your own table (recommended, for durability + de-dupe), replicate the `SKIP LOCKED` claim in Postgres, or use Mongo `findOneAndUpdate` with a `leasedUntil` guard.
> - `AGENT_BRIDGE_SECRET` raw-bearer = a shared secret header between FastAPI and the Agno worker HTTP endpoint (if you expose one). With Composio/Agno you more likely just enqueue a Celery task, so the "poke HTTP call" collapses into `send_task`.

### 2.2 The durable session bridge that streams the agent's steps/questions back to the UI

Two mechanisms, working together:

**(i) Persisted event log — `AgentConversation` + `AgentEvent`.** The agent writes `AgentEvent` rows (type, data JSON, emittedAt) keyed by `sessionId`; the API exposes them read-only over tRPC so a closed browser can reload the whole transcript.

Schema (`schema.prisma:339-380`):
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
  @@map("agentEvent")
}
model AgentConversation {
  id String @id @default(cuid())
  contactId String?  ; companyId String?  ; dealId String?    // which record the chat hangs off
  userId String
  sessionId         String  @unique
  continuationToken String?                                    // non-null ⇒ session resumable ("ready")
  streamIndex       Int     @default(0)                        // resume offset into the live stream
  title        String?  ; messageCount Int @default(0)
  createdAt     DateTime @default(now())
  lastMessageAt DateTime @default(now())
  @@index([contactId, lastMessageAt]) ... @@map("agentConversation")
}
```

`ConversationsService` (`apps/api/src/conversations/conversations.service.ts`) — `list` (cached 10 min per user+record, `:42-82`), `save` (upsert on `sessionId`, ownership-checked, `:84-121`), `events` (read the transcript, `:123-145`), `remove` (`:147-184`). The `events` read is the "replay a past agent run" path:
```ts
async events(input: ConversationEventsInput, userId: string) {
	const conversation = await this.db.agentConversation.findUnique({
		where: { id: input.id }, select: { sessionId: true, userId: true } });
	if (!conversation || conversation.userId !== userId)
		throw new NotFoundException(`No conversation with id ${input.id}.`);
	const events = await this.db.agentEvent.findMany({
		where: { sessionId: conversation.sessionId },
		orderBy: { emittedAt: "asc" }, take: input.limit,
		select: { id: true, type: true, data: true, emittedAt: true } });
	return events.map((event) => ({ type: event.type, data: event.data,
		meta: { id: event.id, at: event.emittedAt.toISOString() } }));
}
```
Router surface (`conversations.router.ts`): `conversations.list / events / save / remove`, all behind `AuthMiddleware`, all `ctx.user.id`-scoped.

**(ii) Live streaming — the Next.js proxy `/eve/v1/[...path]`.** The browser talks to the agent **through the Next app**, which mints a short-lived signed token and streams the response body straight back. `apps/app/lib/agent-session.ts` models a `Thread` as `new | ready | working | ended | offline`; `continuationToken` present ⇒ `"ready"` (resumable); events replay via `snapshot()` from the `eve/client` SDK (`agent-session.ts:36-49`).

### 2.3 How the signed tokens work (VERBATIM — this is what the task asked for)

The Next app mints an **HS256 JWT signed with `AGENT_BRIDGE_SECRET`**, 120-second TTL, carrying the signed-in user's identity and (optionally) the record the chat is about. `apps/app/lib/agent-bridge.ts:12-59`:
```ts
const ISSUER = "crm-app";
const AUDIENCE = "crm-agent";
const TTL_SECONDS = 120;

export async function mintBridgeToken(
	user: { id: string; email: string; name: string },
	record: { contactId?: string; companyId?: string; dealId?: string } = {},
): Promise<string> {
	const secret = process.env.AGENT_BRIDGE_SECRET;
	if (!secret) throw new Error("AGENT_BRIDGE_SECRET is not set.");
	const now = Math.floor(Date.now() / 1000);
	const header = { alg: "HS256", typ: "JWT" };
	const payload = {
		iss: ISSUER, aud: AUDIENCE, sub: user.id,
		email: user.email, name: user.name,
		...(record.contactId ? { contactId: record.contactId } : {}),
		...(record.companyId ? { companyId: record.companyId } : {}),
		...(record.dealId ? { dealId: record.dealId } : {}),
		iat: now, nbf: now - 5, exp: now + TTL_SECONDS,
	};
	const signingInput = `${base64url(JSON.stringify(header))}.${base64url(JSON.stringify(payload))}`;
	const key = await crypto.subtle.importKey("raw",
		new TextEncoder().encode(secret), { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
	const signature = await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(signingInput));
	return `${signingInput}.${base64url(new Uint8Array(signature))}`;
}
```

The **proxy route** (`apps/app/app/eve/v1/[...path]/route.ts:10-107`) — verifies the *user's* session cookie, strips hop-by-hop + cookie headers, pulls the record id from `x-crm-*` headers (validating it *looks like* a cuid so a caller can't smuggle arbitrary claims), mints the token, and pipes the streaming body through:
```ts
async function handler(request: Request): Promise<Response> {
	if (!bridgeConfigured())
		return Response.json({ error: "The research agent is not configured for this install." }, { status: 503 });
	const session = await getSession();
	if (!session) return Response.json({ error: "Not signed in." }, { status: 401 });

	const url = new URL(request.url);
	const target = `${AGENT_URL}${url.pathname}${url.search}`;
	const headers = new Headers(request.headers);
	for (const header of ["host","cookie","x-forwarded-host", ... "content-length","expect"]) headers.delete(header);

	const contactId = request.headers.get("x-crm-contact");   // record scoping from the UI
	const companyId = request.headers.get("x-crm-company");
	const dealId    = request.headers.get("x-crm-deal");
	headers.delete("x-crm-contact"); headers.delete("x-crm-company"); headers.delete("x-crm-deal");

	headers.set("authorization",
		`Bearer ${await mintBridgeToken(
			{ id: session.user.id, email: session.user.email, name: session.user.name },
			{ contactId: cuid(contactId), companyId: cuid(companyId), dealId: cuid(dealId) })}`);

	// ... fetch(target, { method, headers, body: request.body, duplex: "half", redirect: "manual" }) ...
	return new Response(upstream.body, { status: upstream.status, headers: responseHeaders });
}
// cuid(): only forwards /^[a-z0-9]{20,32}$/ — rejects anything that isn't a plausible id
```

The **agent verifies** the JWT (`apps/agent/agent/channels/eve.ts:11-48`) — issuer/audience pinned, HMAC checked, `sub` becomes the principal:
```ts
export const BRIDGE_ISSUER = "crm-app";
export const BRIDGE_AUDIENCE = "crm-agent";
export function repFromCrm(secret: string): AuthFn<Request> {
	return withAuthChallenges(async (request: Request) => {
		const result = await verifyJwtHmac(
			extractBearerToken(request.headers.get("authorization")),
			{ algorithm: "HS256", audiences: [BRIDGE_AUDIENCE], issuer: BRIDGE_ISSUER, secret });
		if (!result.ok) return null;
		const claims = result.sessionAuth;
		const userId = claims.subject;
		if (!userId) return null;
		return { attributes: claims.attributes ?? {}, authenticator: "crm-app",
			principalId: userId, principalType: "user" as const };
	}, [{ scheme: "Bearer" }]);
}
const secret = process.env.AGENT_BRIDGE_SECRET;
export default eveChannel({ auth: [...(secret ? [repFromCrm(secret)] : []), vercelOidc(), localDev()] });
```

**Summary of the token design:** short-lived (120s) HS256 JWT, `iss=crm-app`/`aud=crm-agent` pinned on both ends, `sub`=userId, optional record-scope claims, `nbf=now-5` for clock skew. The **same env secret** is (A) sent raw as a Bearer for machine-to-machine internal routes (`/internal/crm/*`), and (B) used as the HMAC key for per-user chat JWTs. Absence of the secret degrades gracefully everywhere ("not configured", poke skipped, key saved unverified).

> **[COLLECCT]** You need an equivalent short-lived signed handoff whenever the **Next.js UI streams from an Agno agent** rather than going through FastAPI. Options: (1) a Python-minted JWT (`pyjwt`, HS256, `iss/aud/sub/exp≈120s`, org + record claims) that the Agno serving process verifies; (2) if all agent runs go through Celery + a FastAPI SSE/WebSocket endpoint, you can lean on your existing session auth instead and skip the JWT. Whichever you pick, **add `organizationId` to the claims** (they don't, being single-tenant) and verify it agent-side so one org's chat can't target another's records. The durable `AgentConversation`/`AgentEvent` replay pattern is worth copying directly into Mongo (a `agent_conversations` + `agent_events` collection keyed by `session_id`) so Collecct's 3-pane console can reload an agent run after a refresh — this is your Analyst/Relation/Mail step stream.

---

## 3. Ingestion — Gmail threads + Calendar events → CRM data

All under `apps/api/src/google/`. Entry point is the cron; orchestrator fans out per connected mailbox; each source has a sync service; a shared matcher decides/creates contacts+companies; a participant gate throws away non-humans.

### 3.1 The cron entrypoint

`apps/api/src/google/sync.controller.ts:15-53` — the ONE non-tRPC, non-auth REST route besides `/health`. Guarded by `CRON_SECRET` with a **timing-safe** compare, **fails closed** when the secret is unset:
```ts
@Controller("internal/sync")
export class SyncController {
	@Get("google") @AllowAnonymous()  async googleViaGet(@Headers("authorization") a?) { return this.google(a); }
	@Post("google") @AllowAnonymous()  async googleViaPost(@Headers("authorization") a?) { return this.google(a); }
	private async google(authorization?: string) {
		if (!this.secret) { this.logger.error({ message: "CRON_SECRET is not set — refusing to run..." });
			throw new ServiceUnavailableException("Sync is not configured."); }
		if (!timingSafeEquals(authorization ?? "", `Bearer ${this.secret}`)) throw new ForbiddenException();
		return this.sync.runDue();
	}
}
```
Schedule (`docs/environment.md:346-347`): declared in `apps/api/vercel.json` at `*/5 * * * *` (every 5 min). **Note:** that `vercel.json` is *not committed in this tree* (referenced in docs only; `CRON_SECRET`/route are real). Minute schedules need Vercel Pro; on Hobby it silently degrades to daily.

### 3.2 Orchestrator — `GoogleSyncService.runDue()`

`apps/api/src/google/google-sync.service.ts:29-96` — reconciles connections, reads all "due" mailbox rows, runs each within a 60s tick budget, tallies a summary:
```ts
async runDue(): Promise<TickSummary> {
	await this.connections.reconcileAll();              // enrol MailboxSync rows for newly-granted scopes
	const due = await this.state.due(new Date());
	for (const row of due) {
		if (Date.now() - startedAt > TICK_BUDGET_MS) { /* stop, next tick continues */ break; }
		summary.attempted += 1;
		try {
			const outcome = await this.runOne(row.userId, row.source as SyncSource);
			/* tally skipped / failed / synced */
		} catch (error) { summary.failed += 1; await this.state.markFailed(row.id, ...); }
	}
	return summary;
}
async runOne(userId, source) {
	const row = await this.state.get(userId, source); if (!row) return null;
	return source === "calendar" ? this.calendar.sync(row) : this.gmail.sync(row);
}
```
Scheduling state lives in `MailboxSync` (`schema.prisma:475-494`): `status` (IDLE/RUNNING/NEEDS_RECONNECT/FAILED), `cursor` (Gmail historyId / Calendar syncToken), `lastSyncedAt`, `retryAfter`, `autoCreate`, unique on `(userId, source)`. `SyncStateService.due()` (`sync-state.service.ts:26-34`) = rows not `NEEDS_RECONNECT` and past `retryAfter`, oldest-synced first.

### 3.3 Gmail sync — incremental history → threads/messages/activities

`apps/api/src/google/gmail-sync.service.ts`. Flow: token → profile → if no cursor `start()` (records current `historyId`, does **not** backfill history — forward-only), else `incremental()`. Incremental (`:140-202`) lists `history.messagesAdded`, then `ingest()` (`:204-250`) de-dupes against stored `gmailMessageId`, batches `MAX_MESSAGES_PER_TICK = 120`, and for each calls `store()`.

`store()` (`:252-361`) is the thread/message projector. Key decisions VERBATIM:
```ts
const participants = [parsed.from, ...parsed.recipients];
const outbound = parsed.from.email === mailbox;
const thread = await this.db.emailThread.findUnique({
	where: { rootMessageId: parsed.rootId }, select: { id, companyId, contactId } });
let companyId = thread?.companyId ?? null;
let contactId = thread?.contactId ?? null;
if (!thread) {
	const repliedTo = outbound || (await this.hasOutboundInThread(parsed.rootId, mailbox));
	const match = await this.match.resolve(
		{ participants,
		  allowCreate: row.autoCreate && repliedTo,        // only auto-create if WE replied
		  source: RecordSource.EMAIL, ownerId: row.userId },
		context);
	companyId = match.companyId; contactId = match.contactId;
	if (!companyId && !contactId) return false;            // nothing to file it against → drop
}
// upsert EmailThread on rootMessageId, then create EmailMessage (rfcMessageId unique),
// then recompute thread stats (count/first/last), then project() an Activity.
```
Two rules worth noting:
- **`allowCreate` requires a reply from us** (`repliedTo`): inbound-only cold mail doesn't spawn records unless the mailbox owner has engaged the thread. (Calendar's rule differs — see §3.4.)
- `project()` (`:378-413`) upserts one rolled-up `Activity` (type `EMAIL`, `meta:{synced:true,source:"gmail"}`) per thread and calls `stamp.touch()` to bump `lastActivityAt` on the company/contact.

**MIME parsing** (`apps/api/src/google/mime.ts`) — plain-text body preferred, else HTML stripped; **quoted history removed** (`stripQuotedHistory:89-106` cuts at `On … wrote:`, `-----Original Message-----`, `From:` blocks, `>`-prefixed tails). **There is no signature extraction here** — that's the agent's job (`crm.signature-block` evidence). `rootMessageId()` derives the thread root from `References`/`In-Reply-To`/`Message-Id`.

Address parsing/gate (`apps/api/src/google/participants.ts`): `parseAddress`/`parseAddressList` (RFC-ish, quote/angle-aware), and the **three-layer external gate** `externalParticipants` (`:144-161`):
```ts
return participants.filter((participant) => {
	if (options.ourAddresses.has(participant.email)) return false;      // us (User table)
	if (options.suppressedEmails.has(participant.email)) return false;  // a rep's delete decision
	if (isMachineAddress(participant.email)) return false;              // no human ever read it
	const domain = workDomain(participant.email);
	if (!domain) return false;
	if (options.ourDomains.has(domain)) return false;                   // our own / allow-list domains
	if (options.suppressedDomains.has(domain)) return false;            // Settings→Connections suppression
	if (isAutomatedAddress(participant.email)) return false;            // sales@ noreply@ bookings@ …
	return true;
});
```
`isMachineAddress` catches machine domains (`.calendar.google.com`, `sendgrid.net`, opaque `c_f5ec…`/UUID local parts) — the famous `docs/api.md:333-376` "Interviews scheduled @ group.calendar.google.com became a contact" bug. `isAutomatedAddress` catches shared-inbox local parts. `splitName` derives a first/last from the display name or, failing that, the email local part.

### 3.4 Calendar sync

`apps/api/src/google/calendar-sync.service.ts`. Uses Google's `syncToken` cursor, paginates `MAX_PAGES_PER_TICK = 5`, window = now → `HORIZON_DAYS = 180`. Per event `apply()` (`:188-290`): cancelled → delete; else resolve participants (attendees + organizer), then:
```ts
const declinedByUs = event.attendees?.some(a => a.self && a.responseStatus === "declined");
const match = await this.match.resolve(
	{ participants,
	  allowCreate: row.autoCreate && !declinedByUs,       // create unless WE declined
	  source: RecordSource.CALENDAR, ownerId: row.userId }, context);
if (!match.companyId && !match.contactId) return "ignored";
// upsert CalendarEvent on (iCalUid, originalStartTime), syncAttendees(), prepareForMeeting(), project()
```
- **`syncAttendees`** (`:292-339`) upserts `CalendarAttendee` rows (dropping `attendee.resource` rooms and `isMachineAddress`), linking to existing contacts by email.
- **`prepareForMeeting`** (`:341-362`) — if the event is within 7 days and an attendee is a known contact with **no brief yet**, it queues a `meeting-prep` agent task via `agent.meetingSoon()`. This is the API "reporting a fact" (unknown attendee, meeting soon) and letting the agent decide.
- **`project`** upserts a `MEETING` activity + stamps `lastActivityAt`.

### 3.5 The identity matcher / auto-create (contacts + companies "created by the mailbox sync, not typed")

`apps/api/src/google/google-match.service.ts` — shared by both syncs. `resolve()` (`:86-152`): filter to externals → if a contact already exists by email, use it → else find known companies by domain → pick the `dominantDomain` → if the company exists, (optionally) create the contact under it → else `create()` a brand-new company+contact.

**Company auto-create** (`:154-201`) writes an enrichment-log activity and — crucially — the row-write is all it does; research is a *queued task*, not an inline call:
```ts
const companyId = await this.companies.companyForEmail(lead.email, { ownerId: request.ownerId });
await this.db.company.update({ where: { id: companyId }, data: { source: request.source } });
const contactId = await this.createContact(external, domain, companyId, request);
await this.log.record({ companyId, subject: "Company added from your inbox",
	body: `Created because you ${request.source === "CALENDAR" ? "met" : "emailed"} someone at ${domain}.`, ... });
```
`CompanyDirectoryService.companyForEmail` (`apps/api/src/companies/company-directory.service.ts:16-54`) upserts on `domain` with `enrichmentStatus: PENDING` and **fires `agent.companyCreated()`** (which queues `brand` + `company-profile` tasks). `domainFromEmail`/`isMachineDomain`/`FREE_EMAIL_DOMAINS` (`apps/api/src/companies/domain.ts`) ensure gmail.com etc. and machine hosts never become companies.

**Contact auto-create** (`google-match.service.ts:203-268`) upserts on email; if the sync only has an address and no real name (a "placeholder" derived name), it queues an `identify` task:
```ts
if (isPlaceholder && !hasRealName) {
	await this.agent.contactCreated(contact.id,
		"Created by the sync from an address, with no name on it");
}
```

> **[COLLECCT] — the biggest porting surface.** All of `apps/api/src/google/` is **Google-specific and needs a Microsoft/Composio equivalent:**
> - **Gmail history sync → Outlook mail via Composio.** Google uses `users.history.list` + `historyId` cursor; Microsoft Graph uses **delta queries** (`/me/messages/delta` with a `@odata.deltaLink` cursor). Composio's Outlook actions (per your `crm-graph-pipeline` memory) fetch messages; store the deltaLink where they store `cursor`. Forward-only-from-now (their `start()` records the cursor without backfilling) is a sane default to copy.
> - **Calendar `syncToken` → Graph `/me/calendarView/delta`** (or `/me/events/delta`), same deltaLink-as-cursor pattern; `iCalUID` exists in Graph too.
> - **Access tokens:** they use Better Auth's `auth.api.getAccessToken({ providerId:"google" })` (§3.6). You get the Outlook token from **Composio's connected-account** for that employee (your memory: per-employee Outlook by email). Their `GoogleTokenService.accessTokenFor` → your `ComposioTokenService.tokenFor(userEmail)`.
> - **The participant gate, `splitName`, `dominantDomain`, `FREE_EMAIL_DOMAINS`, machine-address/opaque-local-part filters are provider-agnostic** — port them nearly verbatim; they're pure functions and encode hard-won "don't make a contact out of a room-booking bot" lessons.
> - **Contacts auto-created from mailbox sync** is exactly your `contacts_tasks.py` / Composio-Outlook-contacts pipeline. Their model: sync writes the row + queues an `identify`/enrich task; **the agent (not the sync) enriches.** Match your Explorium enrichment to that boundary — the Celery contacts task should write the FalkorDB node and enqueue an Agno enrich task, not call Explorium inline in the request path.
> - **Signature-block extraction** is NOT in their sync; if Collecct wants it, put it in the Agno agent as an evidence source, not in the ingestion service.

### 3.6 Token & connection lifecycle

`GoogleTokenService` (`apps/api/src/google/google-token.service.ts`): `grantedScopes` (reads `account.scope`), `isConnected` (scope present for source), `accessTokenFor` (**delegates refresh to Better Auth** — `auth.api.getAccessToken({ providerId: GOOGLE_PROVIDER_ID, userId })`, `:70-72`), `revoke` (calls Google's revoke endpoint + nulls the stored tokens). Scopes are read-only Gmail + Calendar (`packages/auth/src/scopes.ts:5-9`): `gmail.readonly`, `calendar.readonly`.

`GoogleConnectionService` (`apps/api/src/google/google-connection.service.ts`): `status` (UI connection state), `onConnected`/`reconcileAll` (enrol `MailboxSync` rows when scopes appear; calendar defaults `autoCreate:true`, gmail `false`), `purgeSyncedData` (delete synced threads/events + `recomputeAll` stamps), `revoke`, `setAutoCreate`, `suppressDomain` (writes `SuppressedDomain`, optionally purges).

Google tRPC surface (`apps/api/src/google/google.router.ts`): `google.status / purgeSyncedData / revokeAccess / syncNow / setAutoCreate / suppressDomain / thread / event`. (`thread`/`event` are the read side — `ConversationService` in `google/conversation.service.ts` renders a thread's messages with participant avatars, and an event with attendees.)

---

## 4. Facts vs Suggestions — the write path

This is the core of "nothing about a person is guessed" (`README.md`). Evidence is priced; **strong evidence writes the record, weak evidence becomes a suggestion a human settles.** The *decision* lives in the agent; the API owns the *human-settles* mutation and the read.

### 4.1 The data model
`packages/db/prisma/schema.prisma:252-294`:
```prisma
enum FactBand   { VERIFIED  PROBABLE  POSSIBLE }
enum FactStatus { APPLIED  PROPOSED  DISMISSED  SUPERSEDED }

model ContactFact {
  id String @id @default(cuid())
  contactId String  ; contact Contact @relation(..., onDelete: Cascade)
  field String  ; value String
  score Float   ; band  FactBand
  evidence Json                         // the ledger: [{kind, detail, sourceUrl}]
  method String ; sourceUrl String?
  sessionId String?
  status      FactStatus @default(PROPOSED)
  decidedById String?  ; decidedBy User? @relation("FactDecider", ..., onDelete: SetNull)
  decidedAt   DateTime?
  observedAt   DateTime  @default(now())  ; supersededAt DateTime?
  @@index([contactId, field, status])  ; @@index([status, observedAt])  ; @@map("contactFact")
}
```

### 4.2 The write decision (AGENT side — the counterpart to the API mutation)

The evidence ledger (`apps/agent/agent/lib/evidence.ts`) prices each observation and never accepts a model-supplied confidence. Weights (`:22-78`) include `crm.signature-block: 0.8 (primary)`, `profile.email-match: 0.95`, `linkedin.employer-and-name: 0.85`, `web.cited-claim: 0.4 (not primary)`, `contradiction: 0`. Scoring is noisy-OR (`:99-126`):
```ts
const combined = evidence.reduce((remaining, item) => remaining * (1 - WEIGHTS[item.kind].weight), 1);
let score = Math.min(CEILING, 1 - combined);
if (contradicted) score = Math.min(score, CONTRADICTED);   // 0.45 ceiling if any source disagrees
return { score, band: bandFor(score, hasPrimary), ... };
// bandFor: VERIFIED if score≥0.85 AND hasPrimary; PROBABLE ≥0.55; POSSIBLE ≥0.30; else null (don't store)
```
`recordFact` (`apps/agent/agent/lib/facts.ts:41-191`) turns band → status:
```ts
const applies = scored.band === FactBand.VERIFIED;
// ... refuses if: empty value; band null (below floor); a DISMISSED fact has this exact value
//     ("A person has already dismissed this exact value. Do not offer it again.");
//     already APPLIED from same source; or humanOwns(field) — a rep's typed value outranks the web.
await db.$transaction(async (tx) => {
	if (applies && currentApplied)     // supersede the prior applied fact
		await tx.contactFact.update({ where: { id: currentApplied.id },
			data: { status: FactStatus.SUPERSEDED, supersededAt: new Date() } });
	await tx.contactFact.create({ data: { contactId, field, value: trimmed,
		score, band, evidence, method, sourceUrl, sessionId,
		status: applies ? FactStatus.APPLIED : FactStatus.PROPOSED } });
	if (!applies) return;                                  // PROPOSED: leave the denormalised column alone
	if (column) await tx.contact.update({ where: { id: contactId }, data: { [column]: trimmed } });
	// name → also split into firstName/lastName
});
```
So: **VERIFIED ⇒ `APPLIED` and the contact column is written; PROBABLE/POSSIBLE ⇒ `PROPOSED` (a suggestion) and the column is untouched.**

### 4.3 The human-settles path (API side)

`ContactsService.decideFact` (`apps/api/src/contacts/contacts.service.ts:511-588`) — this is what the `contacts.decideFact` mutation calls; a rep accepts/dismisses a `PROPOSED` fact:
```ts
if (fact.status !== FactStatus.PROPOSED) throw new ConflictException("That suggestion has already been settled.");
const accepted = input.decision === "accept";
const column = FACT_COLUMNS[fact.field];             // title / linkedinUrl / twitterUrl / githubUrl
await this.db.$transaction(async (tx) => {
	if (accepted)                                     // supersede any currently-applied fact for this field
		await tx.contactFact.updateMany({ where: { contactId, field, status: APPLIED },
			data: { status: SUPERSEDED, supersededAt: new Date() } });
	await tx.contactFact.update({ where: { id: fact.id },
		data: { status: accepted ? APPLIED : DISMISSED, decidedById: userId, decidedAt: new Date() } });
	if (accepted && column) await tx.contact.update({ where: { id: contactId }, data: { [column]: fact.value } });
	if (accepted && fact.field === "name") { /* split into firstName/lastName */ }
});
```
The `byId` read (`contacts.service.ts:157-260`) returns facts filtered to `APPLIED`/`PROPOSED` only, with the evidence ledger attached, so the UI can render "suggestions to settle" beside applied facts.

**Enrichment log** (`apps/api/src/crm/enrichment-log.service.ts`) — a separate, low-stakes write path: `record()` inserts a human-readable `Activity` of type `ENRICHMENT` ("Company added from your inbox", "Contact added from your inbox") attributed to the record's owner (or any user), and stamps `lastActivityAt`. This is timeline narration, not a fact.

**Cascade the DB doesn't do, the service does** (`docs/api.md:435-475`; `ContactsService.delete:312-362`): because `AgentTask`/`AgentEvent` have no FK, delete `deleteMany`s them; it also writes a `SuppressedContact` (keyed on lower-cased email) so the sync can't recreate the person, and recomputes `lastActivityAt` stamps for affected records **after commit** (logs on failure rather than throwing — the row is already gone).

> **[COLLECCT]** This facts/suggestions split is directly applicable to your Analyst "verdict" model (`backend/models/verdict.py`) and any enriched-fact writes into FalkorDB. Concretely:
> - Store enriched facts as **nodes/edges with a `status` (APPLIED/PROPOSED) + `evidence` + `score`/`band`**, not as blindly-overwritten properties. Explorium "match then fetch" outputs should land as PROPOSED unless the evidence is primary (email-match / signature), then a human accepts in the UI.
> - The **`decideFact` mutation → a FastAPI `POST /contacts/{id}/facts/{factId}/decide`** that flips status in a transaction and only then writes the denormalised property. Preserve "dismissed value is never re-proposed" and "human-typed value outranks the agent".
> - Keep the **decision (band→status) in the Agno agent**, not the FastAPI route — same boundary as §0. FastAPI persists what the agent decided and exposes the accept/dismiss.

### 4.4 API-side queue / cron (the "sweep")

`BackfillService` (`apps/api/src/backfill/backfill.service.ts`) is an **in-process periodic sweep**, not a Vercel cron — it self-throttles with a cache lock and is kicked on sign-in:
```ts
onModuleInit(): void { onSignedIn(() => { void this.auto(); }); }     // fires when anyone signs in
async auto(): Promise<{ started: boolean }> {
	if (await this.cache.get(AUTO_KEY)) return { started: false };       // AUTO_KEY="backfill:auto"
	await this.cache.set(AUTO_KEY, true, AUTO_EVERY_MS);                 // AUTO_EVERY_MS = 5 min lock
	void (async () => {
		await this.sweepWorkspace();                                     // queue workspace-profile if missing
		const companies = await this.runCompanies(false);               // queue brand/company-profile
		const contacts  = await this.runContacts();                     // queue portrait/identify
		const mirrored  = await this.images.sweep();
	})();
	return { started: true };
}
```
It finds records that "never succeeded" (`enrichmentStatus in [PENDING, FAILED]`) or that a `portrait`/`brand` task hasn't touched in 30 days (`RECHECK_*_AFTER_MS`), and enqueues via `AgentTriggerService.backfill()` (bulk de-duped insert). `SettingsService.setResearchKey` also triggers `backfill.run("companies")` so companies that were `PENDING` for lack of a key get picked up immediately (`settings.service.ts:107-144`).

> **[COLLECCT]** `BackfillService.auto()` = a **Celery beat task** (e.g. every 5 min) that sweeps records needing enrichment and enqueues Agno tasks, plus the cache-lock idempotency = a Redis lock / beat's own single-run guarantee. Your `samgov-ingestion` daily poll and any "re-enrich stale contacts" sweep fit this shape exactly. Their two triggers (sign-in event + explicit key-save) → your beat schedule + on-demand endpoints.

---

## 5. Auth — Better Auth + Google OAuth (high level)

### 5.1 Ownership & routes
The **API process owns authentication** (`apps/api/README.md:42-69`). It mounts `/api/auth/*` (Better Auth, via `@thallesp/nestjs-better-auth`) and is the only writer of session cookies; the Next app reads sessions straight from Postgres via `@crm/auth`. REST routes: `/api/auth/*` (anon), `/auth/me` (required), `/auth/session` (optional), `/health` (anon), `/internal/sync/google` (CRON_SECRET). `AuthModule.forRoot({ auth })` registers a **global `AuthGuard`** — every route is protected unless it opts out with `@AllowAnonymous()` / `@OptionalAuth()`.

### 5.2 The Better Auth config
`packages/auth/src/auth.ts:32-150` (VERBATIM highlights):
```ts
export const auth = betterAuth({
	appName: "CRM",
	database: prismaAdapter(db, { provider: "postgresql" }),
	emailAndPassword: { enabled: false },                 // Google/SSO only, no passwords
	socialProviders,                                       // google, built below, only if env.google set
	account: { accountLinking: { enabled: true, trustedProviders: ["google"] } },
	session: { expiresIn: 60*60*24*7, updateAge: 60*60*24, cookieCache: { enabled: true, maxAge: 5*60 } },
	rateLimit: { enabled: true, storage: "database" },     // rate limits in Postgres (README note)
	advanced: { cookiePrefix: AUTH_COOKIE_PREFIX, useSecureCookies: env.isProduction,
		...(env.cookieDomain && { crossSubDomainCookies: { enabled: true, domain: env.cookieDomain } }) },
	trustedOrigins: [...env.trustedOrigins], hooks: {},
	plugins: [
		organization({ allowUserToCreateOrganization: false, disableOrganizationDeletion: true,
			creatorRole: "owner", schema: { organization: { additionalFields: { website: { type:"string", required:false } } } } }),
		sso({ organizationProvisioning: { disabled: true } }),
	],
	databaseHooks: { user: { create: { before: /* allow-list gate */ } },
		session: { create: { before: /* ensureWorkspaceMembership */, after: /* notifySignedIn */ } } },
});
```
Google provider (`auth.ts:18-30`): scopes = `SYNC_SCOPES` (gmail.readonly + calendar.readonly, on top of openid/email/profile), `accessType: "offline"` (refresh token), optional `hd` domain hint. **Google is optional** — an SSO-only install omits `GOOGLE_CLIENT_ID/SECRET`.

### 5.3 The allow-list (the entire authZ model for "who can sign in")
`databaseHooks.user.create.before` (`auth.ts:106-125`) — the *only* gate deciding who gets an account, driven by one env var `ALLOWED_SIGN_IN`:
```ts
before: async (user) => {
	if (!hasSignInAllowList()) throw new APIError("FORBIDDEN", { message:
		'No one can sign in yet: set ALLOWED_SIGN_IN in .env ... ' });
	if (!isWorkspaceEmail(user.email)) { const domain = primaryWorkspaceDomain();
		throw new APIError("FORBIDDEN", { message: domain
			? `This CRM is private. Sign in with your @${domain} account.`
			: "This CRM is private. That address is not on the allow-list." }); }
	return { data: user };
},
```
`env.validation.ts:44-49` makes `ALLOWED_SIGN_IN` a **required boot var** (process refuses to start without it), alongside `DATABASE_URL` and `BETTER_AUTH_SECRET`.

### 5.4 Single-tenant "workspace" (their deliberate anti-pattern for you)
`docs/api.md:68-234` — **"There is exactly one organization, and it is not a tenancy boundary."** The Better Auth `organization` plugin holds ONE row whose id is the literal string `workspace` (`WORKSPACE_ID`). No `organizationId` on any CRM record; every read is `where: { id: WORKSPACE_ID }`, never a parameter. Signing in *is* the join — `ensureWorkspaceMembership` runs in `session.create.before` (`packages/auth/src/organization.ts:30-92`): upserts the singleton org, enrols every existing user (oldest = owner) on first creation, upserts the caller as `member`, **degrades (returns undefined, logs) rather than throwing** so a hiccup can't lock everyone out. Roles owner/admin/member; permissions read from one place (`canRenameWorkspace`/`canChangeRole`); the "last owner can't be demoted" invariant is enforced with `FOR UPDATE` row locks.

> **[COLLECCT] — invert this.** Their single-tenant stance is the direct opposite of your multi-tenant orgs/RBAC (`auth-connection-rbac` memory). Where they *delete* org threading, **you keep it**: every CRM record carries `organizationId`, every query filters by it, and the tRPC/FastAPI auth dependency must inject the org and scope reads. Do borrow: (a) the "sign-in is the join" hook to auto-provision membership, but keyed to your invite-only orgs; (b) the last-owner-lock transaction; (c) permissions defined in exactly one module that both the API enforces and the UI disables buttons on. Their SSO-is-a-row pattern (`docs/api.md:236-297`; `apps/api/src/sso`) — provider config stored as a Better Auth `ssoProvider` row, managed over tRPC, not an env var — is worth copying so a tenant admin can self-configure OIDC without a redeploy.

### 5.5 Session resolution & the cache pattern
tRPC context resolves the session per request (`auth.api.getSession`, §1.2). REST `/auth/me` returns a cached profile; `AuthService.getProfile` (`apps/api/src/auth/auth.service.ts:29-64`) is the **reference cache pattern** the whole codebase points at — read-through, write with explicit TTL, explicit invalidation:
```ts
async getProfile(userId: string): Promise<UserProfile> {
	const key = profileKey(userId);
	const cached = await this.cache.get<UserProfile>(key); if (cached) return cached;
	const user = await this.db.user.findUnique({ where: { id: userId }, select: {...} });
	if (!user) throw new NotFoundException(`No user with id ${userId}.`);
	const profile = { ...user, createdAt: user.createdAt.toISOString() };
	await this.cache.set(key, profile, PROFILE_TTL_MS);   // 5 min
	return profile;
}
async invalidateProfile(userId) { await this.cache.del(profileKey(userId)); }
```
`AuthHooksService` (`@AfterUpdate("user")`) invalidates this on any user-row change (README §"How auth is wired").

> **[COLLECCT]** Better Auth is TS-only, so it doesn't port. Your Python stack needs its own session/OAuth layer (e.g. Authlib/FastAPI-Users or your existing login/signout + invite-only orgs). Keep the *shapes*: global auth dependency with per-route opt-out; allow-list/invite gate at user-creation; org membership auto-provisioned on first sign-in; Microsoft/Composio as a **connection** (per-employee Outlook, admin-only SharePoint) that is separate from the identity provider — their `needsGoogleGrant` (`scopes.ts:30-34`) exactly models "you can sign in via SSO but still need to connect a mailbox", which is your Outlook-connect-by-email flow. The read-through/write-TTL/invalidate cache pattern maps to Redis get/setex/del.

---

## 6. Their backend conventions (the three named skills)

- **`nestjs-trpc` SKILL** (`.agents/skills/nestjs-trpc/SKILL.md`): "two systems that fail independently" (DI runtime vs Rust-CLI type-gen); routers/middlewares/context are ordinary `providers`; `@Ctx()` not `@Context()`; `MiddlewareOptions` not `TRPCMiddlewareOptions`; always `return next()` and pass ctx *through* `next({ ctx })`; keep routers thin (validate → delegate → map errors); prefer explicit `output` schemas on trust boundaries to strip fields; generated `server.ts` is a build step, gitignore-or-commit-consistently (this repo commits it).
- **`nestjs-best-practices` SKILL**: 40 rules, prioritized — Architecture & DI are CRITICAL (feature modules not technical layers; single-responsibility services; constructor injection; repository pattern), then Error Handling (exception filters + Nest HTTP exceptions), Security (validate all input via class-validator, guards, rate-limiting), Performance (caching, avoid N+1), DB (transactions, migrations). This repo visibly follows: feature modules, `ValidationPipe` global, `HttpException` families, `$transaction` for multi-write invariants, Prisma migrations.
- **`better-auth-best-practices` SKILL**: model-name-not-table-name; re-run the CLI after adding plugins; secondaryStorage moves sessions out of the DB (this repo keeps them in DB + `cookieCache` for 5-min optimistic reads); import plugins from dedicated paths for tree-shaking; `typeof auth.$Infer.Session` for types (used here as `Session`/`SessionUser`). Their own README flags the `rateLimit.storage:"database"` cost — every `/api/auth/*` call needs Postgres — and suggests Redis `secondaryStorage` to remove it.

---

## 7. Google-specific plumbing that needs a Microsoft/Composio equivalent (checklist)

| Google/Better-Auth thing (their code) | Collecct / Microsoft-Composio equivalent |
|---|---|
| `GoogleTokenService.accessTokenFor` → `auth.api.getAccessToken({providerId:"google"})` (`google-token.service.ts:70`) | Composio connected-account token for the employee's Outlook (per-email); admin-scoped token for SharePoint |
| Gmail `users.history.list` + `historyId` cursor (`gmail-sync.service.ts`) | Graph `/me/messages/delta` + `@odata.deltaLink` stored where `MailboxSync.cursor` is |
| Calendar `events.list` + `syncToken` (`calendar-sync.service.ts`) | Graph `/me/calendarView/delta` or `/me/events/delta`, deltaLink cursor; `iCalUID` exists in Graph |
| `mime.ts` (Gmail payload base64url parts, quoted-history strip) | Graph returns `body.content` (HTML/text) directly; keep `stripQuotedHistory`/`stripHtml` as-is; adapt only the part-walking |
| Scopes `gmail.readonly`/`calendar.readonly` (`scopes.ts`) | Graph `Mail.Read`, `Calendars.Read` (+ SharePoint `Sites.Read.All`) requested via Composio |
| `/internal/sync/google` Vercel cron (`sync.controller.ts`, `*/5`) | Celery beat task hitting `GoogleSyncService.runDue()` equivalent |
| Better Auth Google social provider + allow-list hook (`auth.ts`) | Your Python auth (Authlib/FastAPI-Users) with invite/allow-list gate; MS as a *connection*, not identity |
| `AGENT_BRIDGE_SECRET` raw-bearer poke + HS256 JWT chat (`bridge.ts`, `agent-bridge.ts`) | Celery `send_task` for dispatch; `pyjwt` HS256 (with `organizationId` claim) for UI→Agno streaming, or lean on session auth |
| Provider-agnostic (PORT VERBATIM): `participants.ts` gate, `domain.ts` (`FREE_EMAIL_DOMAINS`/`isMachineDomain`), `splitName`, `dominantDomain`, evidence ledger (`evidence.ts`), facts state machine (`facts.ts`/`decideFact`) | Same logic in Python; these encode hard-won correctness, not Google specifics |

---

## Key file:line index (for fast re-lookup)
- Rule: intelligence-not-in-API — `docs/api.md:42-67`; enforced by `apps/api/src/agent/{agent.module,agent-trigger.service,research-key.service}.ts`
- tRPC wiring — `apps/api/src/trpc/{trpc.module,trpc.context,context.types,list-input}.ts`, middlewares in `trpc/middlewares/`
- Representative router/service/contracts — `apps/api/src/contacts/{contacts.router:22,contacts.service,contacts.contracts}.ts`
- Agent dispatch (API) — `apps/api/src/agent/agent-trigger.service.ts:146,195` ; bridge helper `agent/bridge.ts`
- Agent dispatch (agent) — `apps/agent/agent/channels/crm.ts:33` ; lease `apps/agent/agent/lib/tasks.ts:41` ; lanes `lib/dispatch.ts:132`
- Signed tokens — `apps/app/lib/agent-bridge.ts:12` ; proxy `apps/app/app/eve/v1/[...path]/route.ts:10` ; verify `apps/agent/agent/channels/eve.ts:14`
- Durable session — `apps/api/src/conversations/conversations.service.ts` ; schema `schema.prisma:339-380`
- Gmail ingest — `apps/api/src/google/gmail-sync.service.ts:252` ; MIME `google/mime.ts:89` ; gate `google/participants.ts:144`
- Calendar ingest — `apps/api/src/google/calendar-sync.service.ts:188,341`
- Identity/auto-create — `apps/api/src/google/google-match.service.ts:86,154,203` ; company `companies/company-directory.service.ts:16`
- Cron — `apps/api/src/google/sync.controller.ts:15` ; orchestrator `google/google-sync.service.ts:29`
- Facts write (agent) — `apps/agent/agent/lib/{evidence.ts:99,facts.ts:41}` ; human-settles (API) — `contacts.service.ts:511`
- API sweep — `apps/api/src/backfill/backfill.service.ts:59`
- Auth — `packages/auth/src/{auth.ts:32,scopes.ts,organization.ts:30}` ; profile cache `apps/api/src/auth/auth.service.ts:29`
- Env boot validation — `apps/api/src/config/env.validation.ts`
