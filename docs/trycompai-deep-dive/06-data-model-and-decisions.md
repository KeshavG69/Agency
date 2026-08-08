# 06 — Data Model, Architecture Decisions & Roadmap

Source repo: `trycompai/crm` (cloned at `scratchpad/crm`), an **open-source, agentic-first CRM** by the Comp AI team. Single git commit visible (`a189eab`, a squashed merge). Stack: Turborepo + Bun monorepo, NestJS API, Next.js app, an **eve** durable-agent app, Prisma + **Postgres**, Better Auth (Google-only).

Its one-line thesis (README):
> **A durable research agent is the product. The database is just where it writes things down.**

This note captures (1) the full data model for the core + evidence + work-queue + agent-run tables, (2) the architectural rules verbatim, (3) the enrichment-agent plans, (4) the config/integration surface, (5) the security posture — and throughout, **Collecct-mapping notes** (their Postgres tables → our MongoDB collections / FalkorDB nodes) plus **single-tenant vs multi-tenant tension flags**.

> **TARGET (Collecct):** Python + Agno + Celery + FalkorDB (contact graph) + MongoDB (system-of-record) + Redis + Next.js. Microsoft (Outlook + SharePoint), NOT Google. **MULTI-tenant (orgs/RBAC)** — the *opposite* of their single-tenant rule. Daily SAM.gov ingestion; agents for bid/no-bid + contact ranking + mail.

---

## 0. The evolution story (migration timeline)

The migration folder names read as a design diary, and two entries matter enormously for Collecct:

```
20260731150000_init                      auth + Better Auth organization plugin
20260731160000_remove_organizations      → went SINGLE-TENANT on day one
20260731164536_add_crm_models            Company / Contact / Deal / Activity
20260731200000_add_google_sync           Gmail + Calendar tables
20260731210000_forward_only_sync
20260731220000_denormalise_last_activity  lastActivityAt cached columns
20260731230000_logo_dark_mode
20260801000000_contact_avatar
20260801120000_contact_summary_and_socials
20260801130000_contact_socials_checked
20260801140000_contact_intelligence       ← THE EVIDENCE MODEL: ContactFact/Brief/AgentTask/AgentEvent
20260801160000_agent_conversations
20260801170000_conversations_on_deals
20260801180000_task_timeline_position
20260801190000_agent_task_attempts
20260802120000_app_settings                AppSetting (agent model + Context key row)
20260803151440_add_workspace_organization  ← RE-ADDED the org plugin as a SINGLETON "workspace"
20260803160044_add_sso_provider
20260803162518_workspace_profile           "who we are" for the agent preamble
20260803190000_workspace_onboarded_metadata
20260803200000_blank_website_is_not_onboarded
20260803210000_suppressed_contact
20260803220000_context_dev_api_key
```

**The load-bearing arc:** they *deleted* organizations on day one (`remove_organizations`) to be deliberately single-tenant, then three days later *re-added* the Better Auth `organization` plugin (`add_workspace_organization`) — but only as **one singleton row whose id is the literal string `workspace`**, purely to answer "what is this company called, who works here, what do we sell." **It is emphatically NOT a tenancy boundary.** This is the exact axis Collecct inverts. See §2 (ADRs) for the full rationale and the tension analysis.

---

## 1. THE DATA MODEL (verbatim Prisma + Collecct mapping)

`datasource` is Postgres; Prisma client generated to `packages/db/src/generated/prisma`. Every model has an `@@map` to a camelCase table name. Below, the **key models verbatim**, grouped, each with a Collecct mapping note.

### 1.1 Core CRM: Company

```prisma title="packages/db/prisma/schema.prisma"
model Company {
  id          String  @id @default(cuid())
  name        String
  domain      String? @unique
  website     String?
  description String?

  logoUrl     String?
  logoDarkUrl String?
  iconUrl     String?
  iconDarkUrl String?
  iconTone    String?
  brandColor  String?

  industry    String?
  subIndustry String?
  city        String?
  stateCode   String?
  country     String?
  countryCode String?

  phone       String?
  email       String?
  linkedinUrl String?
  twitterUrl  String?
  githubUrl   String?
  pricingUrl  String?
  careersUrl  String?

  ownerId          String?
  owner            User?    @relation("CompanyOwner", fields: [ownerId], references: [id], onDelete: SetNull)
  primaryContactId String?  @unique
  primaryContact   Contact? @relation("PrimaryContact", fields: [primaryContactId], references: [id], onDelete: SetNull)

  enrichmentStatus EnrichmentStatus   @default(PENDING)
  enrichedAt       DateTime?
  enrichmentError  String?
  enrichment       CompanyEnrichment?

  source RecordSource @default(MANUAL)

  lastActivityAt DateTime?

  contacts       Contact[]           @relation("CompanyContacts")
  conversations  AgentConversation[]
  deals          Deal[]
  activities     Activity[]
  emailThreads   EmailThread[]
  calendarEvents CalendarEvent[]
  createdAt      DateTime            @default(now())
  updatedAt      DateTime            @updatedAt

  @@index([ownerId])
  @@index([name])
  @@index([lastActivityAt])
  @@map("company")
}
```

- `domain` is **unique** — the enrichment key. "Two records for the same domain is the #1 way a CRM rots, so we block it at the database" (crm-plan §1).
- `enrichmentStatus` + `enrichedAt` + `enrichmentError` = the poll-able bookkeeping the UI watches (background writes are polled, not invalidated).
- `lastActivityAt` is a **denormalised cached maximum** (sortable column), recomputed by the service on delete.

> **Collecct → MongoDB `companies` collection.** Add `org_id` (tenant scope) to the doc + a compound index `{org_id, domain}` unique-per-tenant (their global-unique `domain` becomes per-tenant-unique). For govcon, replace `industry/subIndustry` firmographics with **UEI, CAGE code, NAICS[], set-aside flags** (already in your SAM.gov ingestion). Brand columns (`logoUrl…iconTone`) are Context.dev-specific — drop or repoint to your own enrichment. **FalkorDB:** a `Company` node keyed on UEI/domain; edges to `Contact` and `Opportunity` nodes.

### 1.2 Core CRM: Contact (with the enrichment status fields)

```prisma title="packages/db/prisma/schema.prisma"
model Contact {
  id          String  @id @default(cuid())
  firstName   String
  lastName    String?
  email       String? @unique
  phone       String?
  title       String?
  linkedinUrl String?
  twitterUrl  String?
  githubUrl   String?
  imageUrl    String?

  socialsCheckedAt DateTime?

  enrichmentStatus EnrichmentStatus @default(PENDING)
  enrichedAt       DateTime?
  enrichmentError  String?

  brief         ContactBrief?
  facts         ContactFact[]
  conversations AgentConversation[]

  companyId String?
  company   Company? @relation("CompanyContacts", fields: [companyId], references: [id], onDelete: SetNull)
  ownerId   String?
  owner     User?    @relation("ContactOwner", fields: [ownerId], references: [id], onDelete: SetNull)
  primaryOf Company? @relation("PrimaryContact")

  source RecordSource @default(MANUAL)

  lastActivityAt DateTime?

  deals           DealContact[]
  activities      Activity[]
  emailThreads    EmailThread[]
  calendarEvents  CalendarEvent[]
  eventAttendance CalendarAttendee[]
  createdAt       DateTime           @default(now())
  updatedAt       DateTime           @updatedAt

  @@index([companyId])
  @@index([ownerId])
  @@index([lastActivityAt])
  @@map("contact")
}
```

- `email` is **unique** (matches HubSpot; makes CSV import idempotent). Cost: "two people behind one `info@` address need separate records."
- `socialsCheckedAt` = "looked, found nothing, on this date" — the generalisation that stops every run re-litigating the same absence.
- Note `firstName/lastName/title/linkedinUrl/…` are the **denormalised current values** kept on the row so lists stay one query; the *reasoning* behind them lives in `ContactFact` (see 1.5).

> **Collecct → MongoDB `contacts` collection** + add `org_id`. Contacts arrive from **Outlook** (Composio) not Gmail — your contact-review work/personal modal already gates ingestion. **FalkorDB `Contact` node** is the heart of your contact graph (Mem0/Explorium-enriched). Their `Contact.email @unique` → per-tenant unique. Keep the `enrichmentStatus/enrichedAt/enrichmentError` triplet — it maps cleanly to a Celery-driven enrichment poll.

### 1.3 Core CRM: Deal + DealContact + Activity

```prisma title="packages/db/prisma/schema.prisma"
model Deal {
  id            String              @id @default(cuid())
  conversations AgentConversation[]
  name          String
  companyId     String
  company       Company             @relation(fields: [companyId], references: [id], onDelete: Cascade)
  ownerId       String
  owner         User                @relation("DealOwner", fields: [ownerId], references: [id])

  stage             DealStage @default(DEMO_BOOKED)
  stageChangedAt    DateTime  @default(now())
  amount            Decimal?  @db.Decimal(14, 2)
  currency          String    @default("USD")
  expectedCloseDate DateTime?
  closedAt          DateTime?
  closedReason      String?

  lastActivityAt DateTime?

  contacts   DealContact[]
  activities Activity[]
  createdAt  DateTime      @default(now())
  updatedAt  DateTime      @updatedAt

  @@index([companyId]) @@index([ownerId]) @@index([stage])
  @@index([expectedCloseDate]) @@index([lastActivityAt])
  @@map("deal")
}

model DealContact {
  dealId    String
  deal      Deal    @relation(fields: [dealId], references: [id], onDelete: Cascade)
  contactId String
  contact   Contact @relation(fields: [contactId], references: [id], onDelete: Cascade)
  role      String?
  @@id([dealId, contactId])
  @@index([contactId])
  @@map("dealContact")
}

model Activity {
  id      String       @id @default(cuid())
  type    ActivityType
  subject String?
  body    String?

  occurredAt  DateTime?
  dueAt       DateTime?
  completedAt DateTime?

  companyId String?
  company   Company? @relation(fields: [companyId], references: [id], onDelete: Cascade)
  contactId String?
  contact   Contact? @relation(fields: [contactId], references: [id], onDelete: Cascade)
  dealId    String?
  deal      Deal?    @relation(fields: [dealId], references: [id], onDelete: Cascade)

  createdById String
  createdBy   User   @relation("ActivityAuthor", fields: [createdById], references: [id])
  meta        Json?

  emailThreadId   String?        @unique
  emailThread     EmailThread?   @relation(fields: [emailThreadId], references: [id], onDelete: Cascade)
  calendarEventId String?        @unique
  calendarEvent   CalendarEvent? @relation(fields: [calendarEventId], references: [id], onDelete: Cascade)

  createdAt DateTime @default(now())
  updatedAt DateTime @updatedAt

  @@index([companyId, createdAt]) @@index([dealId, createdAt])
  @@index([contactId, createdAt]) @@index([dueAt]) @@index([createdById])
  @@map("activity")
}
```

Key design (crm-plan §3): **one `Activity` table with a denormalised `companyId`**. "A company timeline must show its deals' and contacts' events. One indexed query beats three joins." `companyId` is *always stamped when resolvable*, even for deal/contact activity, so a company timeline is a single indexed range scan (`WHERE companyId = ?`). `meta` Json holds `{from,to}` for `STAGE_CHANGE`, credit cost for `ENRICHMENT`. Email/calendar items **project into Activity** via the unique FKs (`emailThreadId`/`calendarEventId`) rather than unioning three tables.

- Deal owner is **required** ("every deal has a name against it"); company/contact owners optional.
- `DealContact.role` is the many-to-one join (contacts on a deal). NOTE: the top-level `docs/crm-plan.md` proposes a *far larger* many-to-many model (`CompanyContact`/`CompanyDeal`/`DealContact` with primary flags, `AssociationLabel`, custom-field JSONB, `PropertyDefinition` registry, RBAC) — that is an **aspirational plan doc, not the shipped schema.** The shipped schema is the leaner `docs/plan/crm-plan.md` version. Flag for Collecct: the shipped one is what actually runs.

> **Collecct → MongoDB `opportunities` collection** (your "Deal" = govcon Opportunity/Bid). `DealStage` enum → your bid pipeline stages. `amount/currency/expectedCloseDate/closedReason` all map. Add `org_id`, plus govcon fields you already have: solicitation #, NAICS, set-aside, agency, SAM.gov linkage, bid/no-bid verdict. `Activity` → an `activities` collection or embedded timeline; stamp `org_id`. **FalkorDB:** `Opportunity` node, edges to `Company` (agency + primes/subs) and `Contact` (POCs).

### 1.4 Enums (the vocabulary)

```prisma title="packages/db/prisma/schema.prisma"
enum DealStage {
  DEMO_BOOKED  QUALIFIED_TO_BUY  UNQUALIFIED_TO_BUY
  DECISION_MAKER_BOUGHT_IN  CONTRACT_SENT  CLOSED_WON  CLOSED_LOST
}
enum ActivityType { NOTE CALL EMAIL MEETING TASK STAGE_CHANGE ENRICHMENT }
enum EnrichmentStatus { PENDING RUNNING COMPLETE FAILED SKIPPED }
enum RecordSource { MANUAL IMPORT EMAIL CALENDAR }
enum FactBand { VERIFIED PROBABLE POSSIBLE }
enum FactStatus { APPLIED PROPOSED DISMISSED SUPERSEDED }
enum GoogleSyncStatus { IDLE RUNNING NEEDS_RECONNECT FAILED }
enum EmailDirection { INBOUND OUTBOUND }
```

- `UNQUALIFIED_TO_BUY` is **terminal, not linear** — treated like `CLOSED_LOST` (excluded from pipeline value/forecast). Pipeline math: open = `DEMO_BOOKED, QUALIFIED_TO_BUY, DECISION_MAKER_BOUGHT_IN, CONTRACT_SENT`; closed = `CLOSED_WON, CLOSED_LOST, UNQUALIFIED_TO_BUY`.
- `EnrichmentStatus` — note `SKIPPED` is distinct from `FAILED` (a "not found"/free-email lookup is skipped and *not billed*, and does not look like an error).

> **Collecct:** `RecordSource` becomes `{MANUAL, IMPORT, OUTLOOK, SHAREPOINT, SAMGOV}`. `DealStage` → your govcon capture stages. Keep `FactBand`/`FactStatus` **verbatim** — they are the evidence vocabulary (§1.5) and the single most portable thing in this repo.

### 1.5 ★ THE EVIDENCE MODEL: ContactFact (the centerpiece)

This is the model to copy. **One row = one claim about one field of one contact, with the evidence that backs it and its human-review lifecycle.**

```prisma title="packages/db/prisma/schema.prisma"
model ContactFact {
  id        String  @id @default(cuid())
  contactId String
  contact   Contact @relation(fields: [contactId], references: [id], onDelete: Cascade)

  field String        // "title" | "linkedinUrl" | "employer" | "seniority" | "function" | "location" | "tenure" | "name"
  value String

  score Float          // 0–1 from the evidence ledger
  band  FactBand       // VERIFIED | PROBABLE | POSSIBLE (derived from score + hasPrimary)

  evidence Json        // every evidence item, so the score can be explained AND recomputed

  method    String     // "linkedin.profile" | "github.api" | "crm.thread" | "web"
  sourceUrl String?

  sessionId String?    // joins to AgentEvent for "show your work"

  status      FactStatus @default(PROPOSED)   // APPLIED | PROPOSED | DISMISSED | SUPERSEDED
  decidedById String?
  decidedBy   User?      @relation("FactDecider", fields: [decidedById], references: [id], onDelete: SetNull)
  decidedAt   DateTime?

  observedAt   DateTime  @default(now())
  supersededAt DateTime?

  @@index([contactId, field, status])
  @@index([status, observedAt])
  @@map("contactFact")
}
```

Field-by-field meaning:
- **`field` / `value`** — the atomic claim (e.g. field=`title`, value=`Head of Security`).
- **`score` Float (0–1)** + **`band`** — evidence strength. Band is stored *alongside* the raw score "so a re-calibration re-derives bands without re-running the research."
- **`evidence` Json** — the full list of evidence items, kept so the score is explainable and recomputable. (Same philosophy as keeping `CompanyEnrichment.raw`.)
- **`method` / `sourceUrl`** — provenance: which tool/endpoint, which URL.
- **`sessionId`** — which agent session produced it → joins to the `AgentEvent` log.
- **`status` lifecycle** — `PROPOSED` (a suggestion a human settles) → `APPLIED` (written to the record) / `DISMISSED` (human rejected; never re-offered) / `SUPERSEDED` (a newer fact replaced it — **this is how job-change detection falls out for free**).
- **`decidedById` / `decidedAt`** — who accepted/dismissed and when. This is **labelled training data** produced by the people best placed to judge.
- **`observedAt` / `supersededAt`** — temporal bounds; a superseded `employer` fact *is* the job-change event.

**Job-change detection is free:** "a new `employer` fact superseding an applied one *is* the event. No diffing machinery."

> **Collecct → MongoDB `contact_facts` collection** (or `facts`, one doc per claim), + `org_id`. This directly maps to your Explorium/Mem0 enrichment and **contact-ranking agent**: every ranking signal becomes an evidence-backed fact rather than an opaque score. `sessionId` → your Agno session / Celery task id. `decidedBy` → the reviewing user. **FalkorDB:** you can also model facts as edges (`Contact -[:HAS_FACT {field,value,band,status}]-> Value`) so the graph carries provenance — but the relational/document form is simpler for the review UI. **Keep the FactStatus lifecycle exactly** — PROPOSED-vs-APPLIED is what lets a human outrank the agent.

### 1.6 ContactBrief (the narrative panel)

```prisma title="packages/db/prisma/schema.prisma"
model ContactBrief {
  contactId String  @id
  contact   Contact @relation(fields: [contactId], references: [id], onDelete: Cascade)

  narrative String
  sections  Json     // { currentRole, tenure, previousRoles[], seniority, function, location }

  score     Float
  sourceUrl String?
  sessionId String?

  refreshedAt DateTime @default(now())
  @@map("contactBrief")
}
```

One-per-contact narrative + structured `sections` Json. Written only when evidence clears the band floor.

> **Collecct → embed on the contact doc, or a `contact_briefs` collection.** This is the "5-minutes-before-a-call" briefing — for govcon, becomes the POC/teaming-partner brief your Mail/Relation agent writes. `WorkspaceProfile` (§1.11) is the company-level analogue.

### 1.7 ★ THE WORK QUEUE: AgentTask (due dates + leasing)

```prisma title="packages/db/prisma/schema.prisma"
model AgentTask {
  id        String  @id @default(cuid())
  contactId String?          // plain column, NO foreign key — outlives the record on purpose
  companyId String?

  kind   String              // "brand" | "portrait" | "identify" | "profile" | "recheck" | "meeting-prep" | "company-profile" | "workspace-profile"
  reason String              // the agent's own words, shown to the rep

  priority Int @default(0)
  budget   Int @default(4)   // research units this task may spend
  attempts Int @default(0)

  dueAt       DateTime        // "every N minutes, the oldest ten" belongs HERE, not in cron
  leasedUntil DateTime?       // the lease; a dead run frees its row when this expires

  sessionId  String?
  startedAt  DateTime?
  finishedAt DateTime?
  outcome    String?

  createdAt DateTime @default(now())

  @@index([dueAt, leasedUntil])
  @@index([contactId])
  @@map("agentTask")
}
```

This is the "work queue and audit trail in one." Mechanics (from `docs/agent.md` + `lib/tasks.ts`):
- **`claimDue` leases rows with `FOR UPDATE SKIP LOCKED`** — "two dispatchers take disjoint work and a run that dies frees its row when the lease expires."
- **`dueAt` replaces cron cadence.** The single schedule "decides nothing: it leases what is due and starts a session per row."
- **`budget`** — a research-unit allowance the agent spends but cannot raise (a VP on a live deal earns a deep pass; a newsletter signup earns one lookup).
- **`reason`** is the agent's own words, shown to the rep — "an agent that cannot say why it will be back in fourteen days does not have a reason, it has a default."
- **No FK on `contactId`/`companyId`** — deliberately plain columns "so the queue survives a redeploy"; the service clears them on delete (the DB won't cascade).

**Priorities** (`packages/db/src/agent-tasks.ts`, verbatim — this is the "what a rep sees first" ordering):

```ts title="packages/db/src/agent-tasks.ts"
export const TASK_KINDS = ["brand","portrait","meeting-prep","identify",
  "profile","recheck","company-profile","workspace-profile"] as const;

export const DIRECT_KINDS = ["brand", "portrait"] as const;   // the "visible lane"

export const PRIORITY = {
  brand: 900,          // logo + real name, on every list row
  portrait: 800,       // the face
  workspace: 500,      // who *we* are — opens every later session
  requested: 300,      // a rep pressed Research
  meeting: 200,        // a meeting is coming
  identify: 100,       // a new contact
  sweep: 50,           // the sign-in backfill
  companyProfile: 40,  // the written brief
  recheck: 0,          // come back in ninety days
} as const;
```

**Two lanes** (`schedules/dispatch.ts`): a **visible lane** (`brand`, `portrait` — no session, no model, run directly, 60/tick six at a time) and a **research lane** (everything else — one eve session per row, 12/tick). A logo must never queue behind LLM research.

> **Collecct → MongoDB `agent_tasks` collection** driven by **Celery**. Their `FOR UPDATE SKIP LOCKED` + `leasedUntil` lease maps to Celery's visibility-timeout / a Mongo `findOneAndUpdate` atomic claim with a `leased_until` field (or Redis as the broker with a lease). **`dueAt` = your daily SAM.gov cadence** and `schedule_recheck` follow-ups. Keep the **two-lane split**: cheap deterministic enrichment (company/UEI lookup) vs. LLM agent runs (bid/no-bid, contact ranking, mail). `priority` + `reason` map 1:1. Add `org_id` and scope `claimDue` per tenant (or interleave fairly across tenants). `budget` → your per-task LLM/token budget.

### 1.8 ★ AGENT RUNS: AgentEvent (the "show your work" substrate)

```prisma title="packages/db/prisma/schema.prisma"
model AgentEvent {
  id        String  @id           // eve's meta.id ULID — dedupes replays
  sessionId String
  contactId String?
  type      String
  data      Json
  emittedAt DateTime
  @@index([sessionId, emittedAt])
  @@index([contactId, emittedAt])
  @@map("agentEvent")
}
```

"Every runtime event, written by a hook. The 'show your work' substrate." Populated by a single `defineHook` on `*` doing `insert … on conflict (id) do nothing` (ULID keeps it append-ordered, id dedupes replays). This is the **audit trail** the Agent tab reads back — "which tool ran, what it returned, what it decided" — without inventing an audit format. `hooks/audit.ts` writes it; it is *not* the same as the human-readable stderr narration (`hooks/activity.ts`).

> **Collecct → MongoDB `agent_events` collection** (append-only, capped or TTL-indexed), + `org_id`. Every Agno tool call / step emits one doc keyed by `session_id`. This powers your "Agent tab" transcript. Use a ULID/ObjectId + unique index for replay-dedupe. This is how you make agent reasoning auditable for bid/no-bid decisions — critical for govcon defensibility.

### 1.9 AGENT SESSIONS: AgentConversation (the durable chat handle)

```prisma title="packages/db/prisma/schema.prisma"
model AgentConversation {
  id String @id @default(cuid())

  contactId String?  contact Contact? @relation(...)
  companyId String?  company Company? @relation(...)
  dealId    String?  deal    Deal?    @relation(...)

  userId String
  user   User   @relation("ConversationOwner", fields: [userId], references: [id], onDelete: Cascade)

  sessionId         String  @unique      // the durable eve session id
  continuationToken String?              // present only when eve will accept another turn
  streamIndex       Int     @default(0)

  title        String?
  messageCount Int     @default(0)

  createdAt     DateTime @default(now())
  lastMessageAt DateTime @default(now())

  @@index([contactId, lastMessageAt])
  @@index([companyId, lastMessageAt])
  @@index([dealId, lastMessageAt])
  @@map("agentConversation")
}
```

Holds only the **handle** (durable session id + cursor); the transcript itself lives in `AgentEvent` — "Nothing is stored twice." Scoped to the rep (`userId`) — "Two people asking about the same contact are having two conversations." Resuming passes the saved cursor; replay uses `streamIndex: 0`.

> **Collecct → MongoDB `agent_conversations`**, one per (record, user), + `org_id`. `sessionId` → Agno session id. This is what lets a rep re-open last week's thread on an opportunity. Attach to Contact / Company / Opportunity just as they attach to contact/company/deal.

### 1.10 CompanyEnrichment (raw payload retention)

```prisma title="packages/db/prisma/schema.prisma"
model CompanyEnrichment {
  companyId String   @id
  company   Company  @relation(fields: [companyId], references: [id], onDelete: Cascade)
  source    String   @default("context.dev")
  raw       Json
  fetchedAt DateTime @default(now())
  @@map("companyEnrichment")
}
```

**Keep the raw vendor payload** so re-deriving a field later costs no credits. This pattern "already paid for itself once — `iconTone` and `iconDarkUrl` were backfilled for every company from stored payloads without spending a credit." (The people-enrichment plan proposed an identical `ContactEnrichment` model; the shipped design folded that into `ContactFact.evidence` + facts instead.)

> **Collecct → MongoDB `company_enrichment`** (or embed `raw` on the company doc). For govcon, `raw` = the full SAM.gov entity record / Explorium payload; keeping it means re-deriving NAICS/PoP/set-aside without re-hitting the API. + `org_id`.

### 1.11 WorkspaceProfile + AppSetting (the singleton config rows)

```prisma title="packages/db/prisma/schema.prisma"
model WorkspaceProfile {
  id String @id                     // always WORKSPACE_ID = "workspace"
  website   String
  narrative String
  sections  Json
  sourceUrl String?
  sessionId String?
  refreshedAt DateTime @default(now())
  @@map("workspaceProfile")
}

model AppSetting {
  id String @id                     // always SETTINGS_ID = "app"
  agentModelId            String?
  agentModelContextWindow Int?
  contextDevApiKey        String?   // ← the Context.dev key lives in a ROW, not an env var
  updatedAt DateTime @updatedAt
  @@map("appSetting")
}
```

- **`WorkspaceProfile`** = "who we are" (what we sell / who we sell to / what we're picked over), rendered into a **Who-we-are block in front of every agent session preamble** (`lib/workspace.ts`). Deliberately tiny — `MAX_NARRATIVE=320`, `MAX_LINE=140` (enforced in the write path, not by asking the prompt nicely). Dies with the website: `readWorkspaceIdentity` returns the profile only when its `website` still matches.
- **`AppSetting`** — one row holding the agent model choice + the Context.dev key. "The choice is a row, not an env var" so a self-hoster's admin who cannot redeploy can change it. Default model `zai/glm-5.2-fast` (context window 1,000,000) lives in `packages/db/src/settings.ts` as `DEFAULT_AGENT_MODEL`.

> **Collecct — CRITICAL TENSION.** `WorkspaceProfile` and `AppSetting` are **hard-coded singletons** (`id="workspace"`, `id="app"`). In Collecct these must become **per-org** documents: `workspace_profiles` keyed by `org_id`, `app_settings` keyed by `org_id`. Your memory already notes "agents' company profile now built per-org from UEI→SAM.gov (Redis-cached, busted on Save)" — that is exactly this table made multi-tenant. The `WORKSPACE_ID` constant pattern (one id, never a parameter) is precisely what you must NOT do; every read that says `where: {id: WORKSPACE_ID}` becomes `where: {org_id}`.

### 1.12 Microsoft-sync analogues: MailboxSync / EmailThread / EmailMessage / CalendarEvent / CalendarAttendee

Verbatim (condensed to the load-bearing fields):

```prisma title="packages/db/prisma/schema.prisma"
model MailboxSync {
  id String @id @default(cuid())
  userId String   user User @relation(...)
  source String                                   // "gmail" | "calendar"
  status GoogleSyncStatus @default(IDLE)
  cursor String?                                  // Gmail historyId / Calendar nextSyncToken
  lastSyncedAt DateTime?  lastError String?  retryAfter DateTime?
  autoCreate Boolean @default(false)
  @@unique([userId, source]) @@index([status]) @@map("mailboxSync")
}

model EmailThread {
  id String @id @default(cuid())
  rootMessageId String @unique                    // RFC 822 Message-ID — global, not Gmail's per-mailbox threadId
  subject String?
  companyId String?  contactId String?            // onDelete: SetNull
  firstMessageAt DateTime  lastMessageAt DateTime  messageCount Int @default(0)
  messages EmailMessage[]  activity Activity?
  @@index([companyId, lastMessageAt]) @@index([contactId, lastMessageAt]) @@map("emailThread")
}

model EmailMessage {
  id String @id @default(cuid())
  threadId String  thread EmailThread @relation(...)
  rfcMessageId String @unique                     // global identity; account B skips what account A ingested
  syncedByUserId String?  gmailMessageId String?
  direction EmailDirection  fromEmail String  fromName String?
  recipients Json  subject String?  snippet String?  body String?  sentAt DateTime
  @@index([threadId, sentAt]) @@map("emailMessage")
}

model CalendarEvent {
  id String @id @default(cuid())
  iCalUid String  originalStartTime DateTime  recurringEventId String?
  // key is (iCalUid, originalStartTime) — shared across attendees AND recurrence instances
  title String?  description String?  location String?  conferenceUrl String?
  startsAt DateTime  endsAt DateTime  isAllDay Boolean  status String  organizerEmail String?
  companyId String?  contactId String?
  syncedByUserId String?  googleEventId String?
  attendees CalendarAttendee[]  activity Activity?
  @@unique([iCalUid, originalStartTime]) @@index([companyId, startsAt]) @@index([contactId, startsAt])
  @@map("calendarEvent")
}

model CalendarAttendee {
  id String @id @default(cuid())
  eventId String  event CalendarEvent @relation(...)
  email String  name String?  responseStatus String?  isOrganizer Boolean
  contactId String?
  @@unique([eventId, email]) @@index([contactId]) @@map("calendarAttendee")
}
```

Design rules that survive the platform swap (gmail-calendar-plan §4.2):
- **Google's per-mailbox ids are NOT identities.** Dedup on the *global* `Message-ID` (email) and `(iCalUID, originalStartTime)` (calendar), so two reps on one thread produce one CRM thread.
- **Email/calendar get real tables; the timeline gets a projection** (one `Activity` per thread/event via the unique FK).
- **Forward-only sync, no backfill** — cursor stamped "now" on first sight.
- **`MailboxSync` cursor lives in Postgres** because the API is serverless (no worker to hold state).

> **Collecct → Outlook (Microsoft Graph via Composio), not Gmail.** `MailboxSync` → an `outlook_sync` collection per user, `cursor` = Graph delta token. The **global-id dedup rule is the portable insight**: use Graph's `internetMessageId` (RFC 822) as `rootMessageId`/`rfcMessageId`, and iCalUID for events. Your SharePoint graph is a *separate* store (FalkorDB) — this email/calendar model is the Outlook half. Add `org_id` everywhere. Your daily contact-ingestion cron is their forward-only sync.

### 1.13 Suppression + Auth/Tenancy models (the multi-tenant tension surface)

```prisma title="packages/db/prisma/schema.prisma"
model SuppressedDomain  { domain String @id  reason String?  createdAt DateTime @default(now()) @@map("suppressedDomain") }
model SuppressedContact { email  String @id  reason String?  createdAt DateTime @default(now()) @@map("suppressedContact") }
```

`SuppressedContact` (keyed on lowercased email) is written on contact delete so the Gmail/Calendar sync doesn't recreate a person the rep deleted; `SuppressedDomain` is the "not a customer" control. Both are **global** (no tenant scope).

Better Auth models (the re-added, deliberately-singleton org plugin):

```prisma title="packages/db/prisma/schema.prisma"
model User { id String @id  name String  email String  emailVerified Boolean  image String?
  ownedCompanies Company[] @relation("CompanyOwner")  ownedContacts Contact[] @relation("ContactOwner")
  ownedDeals Deal[] @relation("DealOwner")  activities Activity[] @relation("ActivityAuthor")
  mailboxSyncs MailboxSync[]  factDecisions ContactFact[] @relation("FactDecider")
  conversations AgentConversation[] @relation("ConversationOwner")
  members Member[]  invitations Invitation[]  ssoproviders SsoProvider[]
  @@unique([email]) @@map("user") }

model Session { id String @id  expiresAt DateTime  token String  userId String
  activeOrganizationId String?    // present again after the re-add, but there is only ever one org
  @@unique([token]) @@index([userId]) @@map("session") }

model Account { /* Better Auth per-provider tokens: accessToken/refreshToken/idToken/scope/password … */ }
model Verification { … } model RateLimit { … }

model Organization { id String @id  name String  slug String  logo String?  createdAt DateTime
  metadata String?         // onboardedAt lives INSIDE this blob, not a column
  website String?
  members Member[]  invitations Invitation[]  @@unique([slug]) @@map("organization") }

model Member { id String @id  organizationId String  userId String  role String @default("member")  createdAt DateTime
  @@unique([organizationId, userId]) @@index([organizationId]) @@index([userId]) @@map("member") }

model Invitation { id String @id  organizationId String  email String  role String?  status String @default("pending")
  expiresAt DateTime  inviterId String  @@index([organizationId]) @@index([email]) @@map("invitation") }

model SsoProvider { id String @id  issuer String  oidcConfig String?  samlConfig String?  userId String?
  providerId String  organizationId String?  domain String  @@unique([providerId]) @@map("ssoProvider") }
```

> **Collecct — THIS IS THE INVERSION.** Their `Organization`/`Member`/`Invitation` tables **exist but are inert**: exactly one org (id=`"workspace"`), `Invitation` "table is created because the plugin owns its own schema — it is unused, and nothing in this repo writes to it." Collecct is the **opposite**: `org_id` is a real tenant discriminator on *every* collection, `Member.role` drives real RBAC, and the invite flow is live (your `auth-connection-rbac` memory: invite-only orgs, per-employee Outlook, admin-only SharePoint). So: **keep their table shapes, delete their singleton assumption.** Every `where: {id: WORKSPACE_ID}` → `where: {org_id}`; `ensureWorkspaceMembership` (auto-enrol every signer as one team) → your real invite-gated membership; `activeOrganizationId` on the session becomes meaningful (which tenant am I acting in). Suppression tables → per-tenant.

---

## 2. ARCHITECTURAL RULES (verbatim + rationale)

There is no formal `adrs/*.md` decision-record set — `adrs/` holds only a `README.md` (explaining ADRs are freeform proposals) and one styling proposal (`comp-palette.md`). **The real architectural decisions are written up inline "where the work happens"** — in `README.md`, `docs/api.md`, `docs/environment.md`, and `docs/agent.md`. The README states this explicitly: "Three rules the codebase holds to — Written up where the work happens, not in a style guide." Below, each decision **verbatim** with its rationale.

### RULE 1 — Intelligence never lives in the API

README, verbatim:
> The API deliberately has no intelligence in it at all. NestJS reports that *something happened* — a thread was ingested, a company was created, an attendee is unknown — by writing a row to a queue. The agent leases that row and decides what it means. A Nest service that calls an enrichment API is treated as a bug, and [`docs/api.md`](./docs/api.md) explains the outage that made that a rule.

README "Three rules" section, verbatim:
> **Intelligence never lives in the API** ([docs/api.md](./docs/api.md)). Nest reports that something happened; the agent decides what it means. Two copies of an identity matcher once drifted until one matched every employer on earth.

`docs/api.md`, verbatim (the full rule):
> ## Intelligence never lives in the API
>
> This is an **agentic-first platform**. The API serves HTTP, auth, tRPC and the Google sync. It does not research, enrich, score, summarise, match identities or decide anything about a person or a company — not as a fallback, not "just the cheap bit", not behind a flag. That work belongs to the eve agent in `apps/agent`, which owns the vendor clients, the confidence model and the writes.
>
> Nest's half of the contract is to **report that something happened** — a thread was ingested, a company was created, an attendee is unknown — and let the agent decide what it means. A Nest service that calls an enrichment API is a bug, and the reason is in the tree: two identity matchers were copied across `apps/api` and `apps/agent`, and the copies silently drifted until one of them matched every employer on earth.

The mechanism (api.md): `apps/api/src/enrichment/` was deleted; replaced by `apps/api/src/agent/agent-trigger.service.ts` — "one service with one verb, which writes an `AgentTask` row saying *this happened* and why it might matter. A row rather than an HTTP call: the agent leases work from that table already, so the row *is* the message, and it survives the agent being down, redeployed, or slower than the request that produced it."

AGENTS.md reinforces: "Every piece of intelligence in this repo lives there, not in the API."

> **Collecct mapping:** This is the cleanest architectural gift in the repo and it maps directly onto **Celery + Agno**. Your FastAPI/Next API layer should *only* write task rows (`agent_tasks` docs) and serve data; **all** bid/no-bid scoring, contact ranking, and mail drafting lives in Agno agents run by Celery workers. "The row is the message" = enqueue a Mongo/Redis task doc, not an in-request call — this is exactly how you survive a worker being down. The drift war-story (two identity matchers) is the argument against duplicating any scoring logic between API and worker.

### RULE 2 — No confidence scores: observation or human-verified suggestion

README, verbatim:
> The rule the agent itself never breaks: **nothing about a person is guessed.** No tool accepts a confidence score, because a model asked to grade its own certainty will, and it will be wrong in the direction that makes it look useful. Tools report what they *observed* — `crm.signature-block`, `github.account-identity` — and a ledger prices the evidence. Strong evidence writes to the record. Weak evidence becomes a suggestion a human settles. A confidently wrong fact about a customer is worse than a blank field, because nobody can tell it is wrong.

`docs/agent.md`, verbatim:
> ## Evidence, not confidence
>
> **No tool accepts a confidence, a score, or a `sourceUrl` offered as proof.** A tool reports what it *observed* — `crm.signature-block`, `github.account-identity` — and `lib/evidence.ts` prices it. This is the rule the whole design rests on: a model asked to grade its own certainty will, and it will be wrong in the direction that makes it look useful.
>
> - `lib/evidence.ts` — the weights, the combination rule, the bands.
> - `lib/facts.ts` — the only write path to a contact's fields. Applies at `VERIFIED`, stores a proposal below it, and enforces three things a prompt cannot: never overwrite a human, never re-offer a dismissal, never write without a primary source.
> - The bands are behaviour, not labels. `PROBABLE` means *a rep decides*, and that is a correct outcome — four Marchettis work at Fernhill.

The three code-enforced invariants (from `lib/facts.ts`, see §3.4): **never overwrite a human**, **never re-offer a dismissal**, **never write without a primary source** — enforced *in the fact store*, not in a prompt. The contact-intelligence plan's phrasing of the same decision:
> **Confidence is data, not a gate.** Every fact is stored with evidence, score, method and source — including the ones too weak to apply.
> **Humans outrank the agent, permanently.** A field a person typed is never overwritten; a dismissed proposal is never re-proposed. Enforced in the fact store, not in a prompt.

> **Collecct mapping:** For a govcon **bid/no-bid** and **contact-ranking** agent this is the single most important pattern to steal. Never let the LLM emit "confidence 0.82"; instead have tools emit *observed* evidence kinds and price them in deterministic code. A bid recommendation becomes an evidence-backed argument (past-performance match, NAICS match, incumbent presence, teaming fit) with a VERIFIED/PROBABLE/POSSIBLE band, and anything below VERIFIED is a **suggestion a capture manager settles**, not an auto-decision. Enforce "human outranks agent" in your `contact_facts` write path (Mongo transaction), not in the prompt.

### RULE 3 — No organizations / single-tenant (the one Collecct INVERTS)

README, verbatim:
> It is single-tenant and internal by design. Sign-in is Google, the allow-list is one environment variable, and everyone who gets in can see everything. That is the whole authorisation model — see [SECURITY.md](./SECURITY.md) before you point it at real customer data.

README "Three rules", verbatim:
> **There are no organizations.** Single tenant, deliberately. An `organizationId` that is always the same value is a column, an index and a permissions check that buys nothing and reads like a real one at review time.

`docs/api.md` — the full rule, verbatim (this is the one to read closely because it documents the *singleton workspace* compromise):
> ## There is exactly one organization, and it is not a tenancy boundary
>
> This is an internal tool behind Google sign-in, and it is **single tenant**. There is no `x-organization-slug` header, no org context interceptor, no org-scoped cache keys, and **no `organizationId` on any CRM record**. A company, a contact, a deal and an activity are scoped by nothing, because there is nothing to scope them to.
>
> What does exist is a **singleton workspace**: the Better Auth `organization` plugin, holding one row whose id is the literal string `workspace` (`WORKSPACE_ID` … ). It is there to answer three questions a CRM has to answer about *itself* — what is this company called, who works here, and what do we sell — and for nothing else.
>
> - **The id is a constant, never a parameter.** Every read says `where: { id: WORKSPACE_ID }`. The moment a function takes an `organizationId`, the plugin has become tenancy plumbing and the rule above is broken. If you are porting something from the Comp AI MVP, delete the org threading rather than stubbing it — an `organizationId` that is always the same value is a column, an index and a `where` clause that buy nothing.
> - **Signing in is the join, and there is no invite flow.** `ensureWorkspaceMembership` runs in `databaseHooks.session.create.before` … `ALLOWED_SIGN_IN` already decides who may sign in; an invitation would be a second, quieter answer to the same question. The plugin's `invitation` table is created because the plugin owns its own schema — it is unused, and nothing in this repo writes to it.
> - **The first account is the owner; everyone after is a member.**
> - **`ensureWorkspaceMembership` degrades, it does not throw.** A failure there would … lock everyone out of the CRM to protect a settings page. It logs, returns `undefined`, and the next sign-in retries.
> - **Permissions are read from one place.** `canRenameWorkspace` and `canChangeRole` … match the plugin's own default statements — owner and admin … `WorkspaceService` adds the one invariant the plugin has no opinion about: **the last owner cannot be demoted.**

The migration record confirms the arc: `20260731160000_remove_organizations` ("this is a single-tenant internal app, so memberships and invitations no longer exist") → `20260803151440_add_workspace_organization` (re-creates `organization`/`member`/`invitation` + `session.activeOrganizationId`, purely for the singleton).

> **★ COLLECCT TENSION — this is the central conflict.** Collecct is **multi-tenant with real RBAC**, the exact opposite of Rule 3. Concretely, everywhere their code says "an organizationId that is always the same value buys nothing," Collecct's org_id is *load-bearing*:
> - **Every CRM collection needs `org_id`** (companies, contacts, opportunities, activities, contact_facts, agent_tasks, agent_events, conversations, sync state, suppression). Their rule "no organizationId on any CRM record" is the one instruction to consciously reverse.
> - **`where: {id: WORKSPACE_ID}` → `where: {org_id}`** in every read. The `WorkspaceProfile`/`AppSetting` singletons (§1.11) become per-org documents — which you have already started ("company profile now built per-org from UEI→SAM.gov").
> - **`ensureWorkspaceMembership` (auto-enrol every signer) → invite-gated membership.** Their "signing in is the join, no invite flow" is exactly what your `auth-connection-rbac` work replaces: invite-only orgs, per-employee Outlook by email, admin-only SharePoint.
> - **`session.activeOrganizationId`** stops being decorative and becomes "which tenant is this request acting in" — every query must filter on it. This is also the #1 security-audit item: an ungated data endpoint that forgets the org_id filter is a cross-tenant leak (their SECURITY.md's "everyone sees everything" is *acceptable* single-tenant but *catastrophic* multi-tenant).
> - **Redis cache keys must be org-scoped.** Their api.md explicitly removed org-scoped cache keys; you must *add them back* (`crm:{org_id}:…`) or one tenant reads another's cached aggregates. The MVP-heritage `docs/crm-plan.md` even shows the pattern: `crm:v{gen}:{entity}:{hash}` — reintroduce the org segment.
> - **RBAC:** their `docs/crm-plan.md` (aspirational) actually sketches a Better Auth `admin`-plugin RBAC model (`owner`/`manager`/`rep`/`readonly`, three enforcement layers: tRPC middleware → service ownership check → UI). That sketch is closer to what Collecct needs than the shipped single-tenant reality — worth mining even though it never shipped here.

### RULE 4 — `packages/ui` is the only source of UI

README "Three rules", verbatim:
> **`packages/ui` is the only source of UI** ([docs/design.md](./docs/design.md)). No overriding styles at the call site.

Rationale (design.md, referenced throughout the plans): shared components only, no `className` overrides on shared components, no bespoke radii/colours; new variants land in the package. (`comp-palette.md` ADR is a concrete instance: repoint both themes onto flat white + brand green `#006B4F`, one radius, fills reserved for exactly two things — the action you want and the one you can't undo.)

> **Collecct mapping:** minor for a backend-focused port, but the principle (one component source of truth, no call-site overrides) is sound for your custom 3-pane console. Not a data/architecture concern.

### RULE 5 — SSO is a row, not a deployment

`docs/api.md`, verbatim (opening):
> ## SSO is a row, not a deployment
>
> Google is the sign-in method a clone starts with. An install that has its own identity provider adds one on **Settings → SSO**, and the whole of that configuration is an `ssoProvider` row written by Better Auth's [`sso` plugin](…) — not an environment variable, because a self-hoster's admin cannot redeploy.
>
> - **OpenID Connect only.** … SAML … deliberately no UI for it …
> - **The provider belongs to the workspace, and the id is still a constant.** `SsoService` passes `WORKSPACE_ID`; it is never an input.

Same "asked-for not configured" philosophy governs the **Context.dev key** (lives in `AppSetting`, not env) and the **agent model** (a row, not a deploy) — the recurring principle: *anything a non-redeploying admin must change lives in a DB row, not an env var.*

> **Collecct mapping:** For Microsoft, the analogue is per-org Azure AD / Entra SSO config + Composio Outlook/SharePoint connections stored as **per-org rows**, not env vars — which is what your connection-RBAC model already does. The generalizable rule: **tenant-configurable integration settings are DB rows keyed by org_id**, env vars are only for install-wide infra (DB URL, secrets).

### RULE 6 — tRPC is the data surface; REST is for auth and health

`docs/api.md`, verbatim (opening + key bullets):
> ## tRPC is the data surface; REST is for auth and health
>
> Everything the app reads or writes goes through `nestjs-trpc` routers under `/api/trpc` … The remaining REST controllers are `/api/auth/*` (Better Auth) and `/health`.
>
> - **One router per module** … carrying `@Router(...)` and `@UseMiddlewares(AuthMiddleware)`. A router with no `AuthMiddleware` is public — there is no other guard.
> - **Routers are thin.** They validate input with zod and call a service; the Prisma work lives in `*.service.ts` …
> - **Filtering, sorting and pagination happen in Prisma.** … Never return a whole table and filter in the browser …

> **Collecct mapping:** You use Next.js + a Python backend, so "tRPC" isn't literal, but the principle holds: **a single typed data surface, thin route handlers, all filtering/sorting/pagination in the datastore (Mongo aggregation), never in the browser.** "A router with no auth middleware is public — there is no other guard" is a sharp reminder for your ungated-endpoint audit item.

### Other decisions worth stealing (from api.md)

- **"Not every address on a thread is a person"** — `isMachineAddress`/`isMachineDomain`/`isAutomatedAddress` gate what the sync turns into a record (the `group.calendar.google.com` "Interviews scheduled" bug). → Collecct: same gating on Outlook contact ingestion; your work/personal review modal is the human half of this.
- **"Deleting a record is a decision the sync has to respect"** — delete writes a `SuppressedContact` (by lowercased email) so the sync can't recreate it; `AgentTask`/`AgentEvent` carry ids as **plain columns with no FK** so the queue survives, and the *service* clears them on delete. `lastActivityAt` is recomputed on the affected records only, after commit, logging rather than throwing.
- **"Freshness: invalidate the query, don't disable the cache"** — TanStack Query invalidation via a `useCrmCache()` fan-out; background writes (enrichment finishing) are **polled** (`refetchInterval` while status is PENDING/RUNNING), not invalidated. `cache-manager` (Redis when `REDIS_URL` set) is used deliberately per-value, not as a global interceptor.

---

## 3. THE ENRICHMENT-AGENT PLANS (the design thinking)

Three plan docs, in build order: **people-enrichment-agent** (v1) → **contact-intelligence-agent** (v2, the one that shipped the fact store) → **gmail-calendar-plan** (the sync that feeds contacts in). Below: strategy, evidence standards, source hierarchy, follow-up logic — with the sharpest lines verbatim.

### 3.1 Strategy — "enrichment as an argument, not a purchase"

The core competitive thesis (contact-intelligence §1), verbatim table:
> | They optimise for | We do instead |
> | **Enrichment as a purchase.** Credits buy a record; the unit is the record and the vendor's confidence is not your business. | **Enrichment as an argument.** Every claim carries evidence, method and date, and the UI shows them. A rep can disagree with one field for one reason. |
> | **Per-seat, per-credit economics** … so re-reading every contact monthly is a line item they must charge for. | **One tenant, our Postgres, our keys.** Re-reading costs cents. That turns enrichment from a snapshot into a *feed*, which is the only way job-change detection exists at all. |
> | **Generic B2B data.** | **Our own conversation history as evidence** — in full. … a signature block settles a job title outright, and a reply on a thread is proof of identity no data vendor can sell us. |
> | **A chat panel bolted onto a record.** | **A resident agent with durable sessions.** … same session, same memory, next week. |

The test it must pass (verbatim):
> a rep opens a contact five minutes before a call and knows who they are, what they do, how long they have done it, what we have already said to each other, and which parts a machine inferred. Then they type "is he still at Fleetio?" into the same panel and watch it check.

The eight decisions that carry the design (contact-intelligence §2), verbatim highlights:
> 1. **Never at the API level.** … The API's enrichment directory is deleted, not deprecated …
> 2. **The agent runs in a sandbox and uses the harness.** … Authored tools are for the three things the harness cannot do: vendor APIs that need keys and verification, CRM reads and writes, and decisions.
> 3. **Confidence is data, not a gate.** Every fact is stored with evidence, score, method and source — including the ones too weak to apply.
> 4. **Background runs propose; people approve.** A markdown schedule … **cannot park for a person** — so an approval gate in a cron run is not a pause, it is a failure. Background work below the bar writes a *proposal*.
> 5. **The record is the interface to the agent.**
> 6. **The agent may read the whole CRM, including bodies.** … The boundary is *egress* …
> 7. **Budget is an input the agent spends,** held in `defineState` and enforced in the tool — a VP on a live deal earns a deep pass, a newsletter signup earns one lookup.
> 8. **Humans outrank the agent, permanently.** … Enforced in the fact store, not in a prompt.

> **Collecct mapping:** "one tenant, our keys, re-reading costs cents → a feed not a snapshot" is *the* argument for your daily SAM.gov + Explorium re-reads. Multi-tenant changes the economics (you pay per org) but the "feed → change detection" logic is what surfaces new opportunities and contact job-changes. "Enrichment as an argument" = your bid/no-bid and contact-ranking outputs should be *inspectable arguments*, defensible in a govcon review.

### 3.2 Evidence standards — the source hierarchy (internal data first, external optional)

The **weighted evidence ledger** — verbatim from `apps/agent/agent/lib/evidence.ts` (this is the shipped implementation, the concrete "no confidence score" mechanism):

```ts title="apps/agent/agent/lib/evidence.ts"
export const WEIGHTS: Record<EvidenceKind, Weighting> = {
  "profile.email-match":        { weight: 0.95, primary: true,  label: "their email address is on the profile" },
  "linkedin.employer-and-name": { weight: 0.85, primary: true,  label: "LinkedIn: employer and name both match" },
  "crm.thread-reply":           { weight: 0.85, primary: true,  label: "they replied on a thread we have" },
  "crm.signature-block":        { weight: 0.8,  primary: true,  label: "their own email signature says so" },
  "github.account-identity":    { weight: 0.8,  primary: true,  label: "the GitHub account names them or their employer" },
  "crm.meeting-attendance":     { weight: 0.7,  primary: true,  label: "they attended a meeting on our calendar" },
  "web.cited-claim":            { weight: 0.4,  primary: false, label: "a cited web source states it" },
  "handle.name-form":           { weight: 0.35, primary: false, label: "the handle is a form of their name" },
  "search.cites-profile":       { weight: 0.35, primary: false, label: "a search for them cites this profile" },
  "employer-only":              { weight: 0.2,  primary: false, label: "the employer matches, the name does not" },
  contradiction:                { weight: 0,    primary: false, label: "another source disagrees" },
};

const CEILING = 0.99;
const CONTRADICTED = 0.45;
export const BAND_FLOOR = { VERIFIED: 0.85, PROBABLE: 0.55, POSSIBLE: 0.3 };
```

Combination rule (verbatim from the same file): `score = 1 − Π(1 − wᵢ)` over evidence items, capped at `CEILING` (0.99); if any `contradiction`, `score = min(score, 0.45)`. Bands: `VERIFIED` requires `score ≥ 0.85 AND hasPrimary`; `PROBABLE` ≥ 0.55; `POSSIBLE` ≥ 0.3; below 0.3 → `null` (not stored).

**The source hierarchy is explicit and deliberate — internal CRM data ranks at the very top, near-tied with LinkedIn:** the two rows unique to them (`crm.thread-reply` 0.85, `crm.signature-block` 0.8) are near the top. contact-intelligence §5: "The two bolded rows are ours alone, and they are near the top. That is the argument in §1 turned into arithmetic."

Two rules a score cannot express (verbatim):
> 1. **No auto-apply without a primary source.** Three weak signals multiply to a confident-looking number and remain three weak signals.
> 2. **Contradiction floors the score.** A profile saying one employer and a mail header saying another is not 0.6, it is unresolved, and the fact is held.

The bands as **behaviour** (verbatim table):
> | `VERIFIED` | ≥ 0.85 + primary | Applied automatically | The value, plain. Dotted underline reveals the source. |
> | `PROBABLE` | 0.55–0.85 | Stored as a proposal; field stays empty | A line under the field: *"LinkedIn suggests Head of Security · accept · dismiss"* |
> | `POSSIBLE` | 0.3–0.55 | Stored, never shown as a value | Only in the provenance panel |
> | below | < 0.3 | Not stored | — |

The agent's own `evidence.md` skill (verbatim opening — the prose the model reads):
> You never set a confidence. You report what you saw, and the ledger prices it. Getting the `kind` right is therefore the whole job — it is the difference between a fact landing on a record and a rep being asked a question.

And on independence (verbatim):
> One entry per **independent** source. Two things on the same page are one observation, not two: a GitHub profile whose name and company both match is one `github.account-identity`, not a name match plus a company match. Splitting it would double-count a single page into false certainty …

`skills/identity-matching.md` sets the evidence hierarchy for the hardest problem (people-enrichment §5, verbatim):
> 1. **Exact email on the profile** — decisive.
> 2. **Local-part decomposition against a real name.** `pmarchetti` is consistent with `a` + `marchetti` … Checked *against candidates*, never used to generate a name — the direction matters, and it is the whole difference between this and guessing.
> 3. **Company match**, current employer equals the contact's company.
> 4. **Corroboration** — the meeting we already synced, the thread they replied on, the title they signed off with in an email footer.

> **Collecct mapping — steal this wholesale for contact-ranking and bid/no-bid.** Define your own `WEIGHTS` table of *observed* govcon evidence kinds, e.g.: `samgov.exact-uei-match` (primary, ~0.95), `pastperf.same-naics-and-agency` (primary, ~0.85), `outlook.thread-reply` (primary, 0.85 — your internal data, rank it high), `outlook.signature-title` (0.8), `sharepoint.doc-authored-by` (primary), `explorium.cited-claim` (supporting, 0.4), `name-handle-form` (0.35), `agency-only-match` (0.2), `contradiction` (0 → floors to 0.45). Reuse the `1 − Π(1−wᵢ)` combination, the CEILING, the contradiction floor, and the `VERIFIED/PROBABLE/POSSIBLE` band floors **verbatim**. Internal data (Outlook + SharePoint) should rank at the top exactly as their CRM history does — "no data vendor can sell you a reply from the person's own address." Store each item in `contact_facts.evidence` (Mongo array) so a capture manager can audit *why* a POC was ranked #1.

### 3.3 The write path — three invariants enforced in code (not prompt)

From `apps/agent/agent/lib/facts.ts` (`recordFact`) — the *only* write path to a contact's fields. The logic, in order:
1. Score the evidence; if `band === null` → **not stored** ("Below the floor for keeping").
2. If a **DISMISSED** fact with the same value exists → refuse ("A person has already dismissed this exact value. Do not offer it again.").
3. If an APPLIED fact from the same source with the same value exists → no-op.
4. **`humanOwns(...)`** check → if a person filled the field (for `name`: `isDerivedName` is false; for a column-backed field: the column is set and there is no prior agent fact) → refuse ("A person already filled in {field}. That outranks anything found on the web.").
5. Apply only when `band === VERIFIED`: in a transaction, mark any current APPLIED fact `SUPERSEDED` (+`supersededAt`), insert the new fact as `APPLIED`, and write the denormalised column on `Contact`. Otherwise insert as `PROPOSED` (field stays empty).

Field registry (verbatim): `name, title, linkedinUrl, twitterUrl, githubUrl, employer, seniority, function, location, tenure` — only `title/linkedinUrl/twitterUrl/githubUrl` have a backing column; the rest live purely as facts. `lastEmployerChange()` reads the SUPERSEDED→APPLIED `employer` transition = **job-change detection, for free**.

Cross-cutting note (agent.md): "Adding a fact field means adding it to `FIELDS` in `lib/facts.ts` **and** to `FACT_COLUMNS` in `apps/api/src/contacts/contacts.service.ts`, which is where an accepted proposal writes through."

> **Collecct mapping:** Implement `record_fact` as a **single Mongo transaction** enforcing the same three invariants (never overwrite a human, never re-offer a dismissal, never write without a primary source). This is what makes the contact-ranking agent trustworthy in a multi-user org: one rep's manual correction outranks the agent permanently, and a dismissed suggestion never comes back. The SUPERSEDED lifecycle gives you free job-change / re-compete detection.

### 3.4 Follow-up logic — the agent schedules its own next look

`docs/agent.md` + contact-intelligence §7. The agent books its own recheck via `tools/schedule_recheck.ts`, which **takes a date and a reason**, and the reason is shown to the rep. The cadence heuristics (contact-intelligence §7, verbatim):
> - champion on an open deal → 14 days ("job change here moves a live deal")
> - named contact, no open deal → 90 days
> - nothing found twice → 365 days, low priority
> - `support@`, `no-reply@` → never

Verbatim principle:
> An agent that cannot say why it will be back in fourteen days does not have a reason, it has a default.

Dispatch mechanics (agent.md): one `defineSchedule` (`schedules/dispatch.ts`) "decides nothing: it leases what is due and starts a session per row. Anything that looks like 'every N minutes, the oldest ten contacts' belongs in a task's `dueAt`, not in a cron expression." Events jump the queue: a calendar event tomorrow with an unknown external attendee inserts a high-priority `meeting-prep` task the moment the sync sees it. `AgentTriggerService.poke()` (fire-and-forget, never awaited) drains both lanes on demand after any `AgentTask` write, so "add a company and its logo appears" without waiting for cron.

> **Collecct mapping:** `dueAt`-driven follow-ups map onto **Celery** (`apply_async(eta=...)` or a beat-scanned `agent_tasks` collection). Your daily SAM.gov poll is the recurring dispatch; `schedule_recheck` is how a bid/no-bid or relation agent books "re-evaluate this opportunity when the amendment drops" or "re-rank this POC in 30 days," each with a human-readable reason. Keep the rule: **cadence lives in the task's `dueAt`, not in a cron expression** — it makes the schedule dumb and the reasons auditable.

### 3.5 People-enrichment §0 — the measured findings that reshaped the design

The v1 plan's Phase-0 spike (people-enrichment §0) is a model of "measure, don't assume," and its findings are portable warnings:
- **LinkDAPI (unofficial LinkedIn API over RapidAPI) people-search is broken** — returns HTTP 200 with confident, unrelated people. "Do not build on it." It is an **enricher, not a finder**: given a slug it's excellent, given a name it's useless.
- So the resolver became a **hybrid**: `email + company → Context.dev web search (site:linkedin.com/in) → slug → LinkDAPI profile/overview → deep profile`. "Context.dev is a search engine and cannot read LinkedIn; LinkDAPI can read LinkedIn and cannot search. Neither alone solves this."
- **Hit rate is partial, and that is the honest headline** — an unusual local-part at an 8,000-person company resolves to nothing. "A miss must stay a miss" — the temptation to accept a near-match "is exactly how Dario Fontana ends up filed as Paula Marchetti."

The confidence bands from v1 (people-enrichment §5) — simpler `high/medium/low` that v2 replaced with the ledger:
> | `high` | Email match, or unique local-part decomposition at the right company | Merged automatically. |
> | `medium` | Consistent decomposition but more than one plausible candidate | Written as a **suggestion**; a rep accepts or rejects. |
> | `low` | Company match only | Discarded. |

GDPR / legal posture (people-enrichment §9, verbatim highlights) — worth copying for govcon PII handling:
> - **This is personal data under UK/EU GDPR** even though it is professional and public. What that actually requires … a lawful basis … a retention period, and the ability to answer an access or erasure request. `ContactEnrichment` with `sourceUrl` and `fetchedAt` is most of the answer …
> - **Keep it to business context.** Name, title, employer, tenure, public profile. Nothing about a person outside their work, and none of the special categories …
> - **Single-provider risk.** … `lib/linkdapi.ts` being the only file that knows the vendor is what makes swapping it a day rather than a rewrite.

> **Collecct mapping:** Your enrichment stack is **Explorium + Composio Outlook + SharePoint**, not LinkDAPI/Context.dev, but the lessons transfer exactly: (1) isolate each vendor behind one client module so swapping is a day not a rewrite; (2) a miss must stay a miss — never let the ranking agent accept a near-match POC; (3) keep `sourceUrl`+`fetchedAt` on every enrichment doc so a govcon erasure/audit request is a query. For govcon specifically, provenance is not a nicety — it is the defensibility of a bid decision.

### 3.6 The egress boundary (read everything internal; constrain what leaves)

`skills/data-boundaries.md`, verbatim:
> ## You may read everything
> This is a single-tenant internal CRM. Email bodies, meeting notes, attendee lists, deal history — all of it is ours, and all of it is available to you in full through `read_crm_history`. There is no redaction to work around and no approval to seek.
>
> ## The boundary is egress
> **1. No customer text in a third-party query.** … Ask them derived questions — "what did Acme announce in 2026?" — never a pasted thread …
> **2. Nothing from a mailbox goes into `/workspace`.** The sandbox has a different lifetime …
> **3. Nothing sensitive gets logged.** … Reading is not logging.
>
> ## What belongs on a record
> Business context only … none of the special categories — health, politics, religion, sexuality, ethnicity, union membership — regardless of what a source volunteers …

The sandbox is `deny-all` egress, and **never given `DATABASE_URL`** ("A shell with credentials and network is exfiltration-shaped even in an internal tool; a shell with neither is a text processor").

> **★ COLLECCT TENSION:** "read everything" is safe *because* it's single-tenant. In a multi-tenant Collecct, "the agent may read everything" must mean **everything within one org_id** — an agent run for org A must never read org B's Outlook/SharePoint/CRM data. The egress rules (no customer text to third parties, no secrets in logs, no DB creds in any sandboxed tool) carry over unchanged and are, if anything, *more* important with multiple customers' data present.

---

## 4. INTEGRATION / CONFIG SURFACE (`.env.example`)

**One `.env` at the repo root**, read by all three processes (loader in `packages/env` walks up to the workspace root — the marker is a `package.json` with a `workspaces` key; real env vars always win over the file). "If you add a variable, add it to `.env.example`… and if the API reads it, declare it in `apps/api/src/config/env.validation.ts` too."

### Required (API refuses to boot without the first three)

| Variable | Purpose / integration implied |
| --- | --- |
| `DATABASE_URL` | Postgres. Matches `docker compose` default `postgresql://postgres:postgres@localhost:5432/crm`. |
| `BETTER_AUTH_SECRET` | Signs session cookies (`openssl rand -base64 32`). API + app must share it or sign-in loops. |
| `ALLOWED_SIGN_IN` | **The entire authorisation model.** Comma-separated domains/addresses. Empty = nobody signs in (fails closed). |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | The 4th value, a **pair** (both or neither). Sign-in button **and** Gmail/Calendar sync (same OAuth client, restricted `gmail.readonly` + `calendar.readonly` scopes). Optional only if using SSO. |

### Optional — "what the agent can do" (each key opens one more place to look; app runs with none)

| Variable | Integration | What it adds |
| --- | --- | --- |
| `PERPLEXITY_API_KEY` | **Perplexity** | Open-web research with citations; the search that finds a LinkedIn slug. |
| `RAPIDAPI_KEY` | **LinkedIn via LinkDAPI** (RapidAPI) | LinkedIn profiles: name, title, employer, tenure. |
| `GITHUB_TOKEN` | **GitHub** | Raises rate limit (from 60/hr) when matching contacts to GitHub profiles. |
| `BLOB_READ_WRITE_TOKEN` | **Vercel Blob** | Mirrors every logo/profile picture in-house (source URLs expire). Read by API + agent + seed. |
| `AI_GATEWAY_API_KEY` | **Vercel AI Gateway** (the model) | Not needed on Vercel (OIDC handles it). |
| `AGENT_BRIDGE_SECRET` | agent↔app bridge | Lets a rep talk to the agent from the record's Agent tab; also authorises the dispatch poke + Context-key verification. Unset = tab reports "not configured," agent still runs its schedule. |

**Not an env var (deliberately):**
- `CONTEXT_DEV_API_KEY` — **Context.dev** (company brand: logo, colours, industry, real name behind a domain). Lives in the `AppSetting` row, asked for at `/onboarding/research`, changed on Settings → General — because "a self-hoster's admin cannot redeploy to set an environment variable." Read only by `readContextDevKey` in `@crm/db/settings`.
- **Agent model** — a row in `AppSetting`, not an env var (default `zai/glm-5.2-fast`).
- **SSO** — an `ssoProvider` row, not a variable.

### Optional — operations / deploy

| Variable | Purpose |
| --- | --- |
| `REDIS_URL` (+ `CACHE_TTL_MS`) | **Upstash/Redis** shared cache. Without it: per-instance in-memory (fine local, wrong for multi-instance). |
| `CRON_SECRET` | Bearer guard on `POST /internal/sync/google` (the Gmail/Calendar cron). Fails closed if unset. Min 16 chars. |
| `API_URL` / `APP_URL` / `AUTH_COOKIE_DOMAIN` / `AGENT_URL` | Origins; only needed off localhost. `APP_URL` doubles as the trusted-origin allow-list. `AGENT_URL` is the agent's own deployment. |
| `DIRECT_DATABASE_URL` / `POSTGRES_URL_NON_POOLING` | Unpooled connection for `prisma migrate deploy` behind a pooler. |
| `PRISMA_LOG_QUERIES` | Debug-level SQL logging, off by default. |

> **Collecct mapping:** Replace the integration set: **Perplexity/Context.dev/LinkDAPI/GitHub → Explorium + Composio (Outlook + SharePoint) + SAM.gov API**; **Vercel Blob → your file store**; **Vercel AI Gateway → your Agno LLM provider**; **Google OAuth → Microsoft Entra**. **The design principles port exactly:** (1) required-vs-optional split (a missing key removes a capability, never throws — see `lib/capabilities.ts`, which prints on/off at boot and tells the agent what it has *before* it plans); (2) tenant-configurable integration secrets live in **per-org DB rows** (Composio connection ids, Context-key-analogues), not global env; (3) `CRON_SECRET`-style bearer guard on your daily-poll internal routes. The `capabilities()` "told at startup what's on" pattern is worth copying so an Agno agent plans around what a given org actually connected (some orgs SharePoint-only, some Outlook-only).

---

## 5. SECURITY POSTURE (`SECURITY.md`, worth copying)

Verbatim, the load-bearing statements:
> This CRM is built for **one organisation of authenticated internal users**. It is not a hardened public or multi-tenant service boundary, and the design says so out loud in a few places.

> **Sign-in is the entire authorisation model.** `ALLOWED_SIGN_IN` decides who gets in; after that, every signed-in person can read and write every record. There are no roles, no per-record permissions and no organizations — deliberately, because a permissions check that always returns `true` reads like a real one at review time. If you need someone to see only part of the pipeline, this is the wrong tool today.

> An unset `ALLOWED_SIGN_IN` fails closed: nobody can sign in. A list that names a consumer domain (`gmail.com`) is an open door, which is why single addresses are supported.

> **Operators can read everything.** Whoever runs the deployment has the database, the environment and the logs.

> **The agent reads your mail.** … It is deliberately unrestricted on the *read* side and constrained on the *write* and *egress* sides … If you deploy this, you are the data controller for those mailboxes.

> **Outbound calls send data to third parties.** Each optional key … turns on a vendor … a query carries whatever it needs to ask the question — typically a name, an email domain and an employer. With no keys set, nothing leaves your infrastructure except Google's own APIs.

> **The sync route is guarded by a shared secret.** `CRON_SECRET` is the whole guard and the route refuses to run without it.

> **Session cookies depend on one shared value.** … Rotating [`BETTER_AUTH_SECRET`] signs everyone out, which is the intended way to revoke every session at once.

Deploy-safely checklist (verbatim): set `ALLOWED_SIGN_IN` to a domain you control (never a public provider); generate your own `BETTER_AUTH_SECRET`; HTTPS both processes; set `CRON_SECRET`; keep the DB off the public internet; **start with no optional API keys and add them one at a time, so you know what is leaving.**

> **★ COLLECCT TENSION — the single biggest gap to close.** Their entire authZ model is "signed in ⇒ read/write everything," and they say plainly this is "**not a hardened … multi-tenant service boundary**." Collecct MUST NOT inherit this. Every one of their "deliberately absent" protections is **mandatory** for you:
> - **Per-record / per-org authorization** on every read and write (org_id filter enforced server-side, not just in the UI — their own aspirational `crm-plan.md` names the rule "The UI hides buttons; it never guards anything").
> - **Roles** — your invite-only orgs + admin-only-SharePoint + per-employee-Outlook RBAC is exactly the "roles" they skipped.
> - **The "ungated data endpoints" and "contact-graph clobber" items in your `auth-connection-rbac` memory are the concrete instances** of what breaks if you port their model unchanged: an endpoint that forgets the org_id filter is a cross-tenant data leak, not a cosmetic bug.
> - Keep the good habits: `ALLOWED_SIGN_IN`-style fail-closed defaults, `CRON_SECRET`-style bearer guards on internal poll routes, secret-rotation-signs-everyone-out, "add integrations one at a time so you know what's leaving."

---

## 6. MONOREPO / SERVICE WIRING

- **Turborepo + Bun workspaces** (`package.json`: `workspaces: ["apps/*","packages/*"]`, `packageManager: bun@1.3.12`, node ≥22). Root scripts proxy to `turbo run` (`dev/build/lint/test/check-types/db:*`).
- **Three deployable apps:** `apps/app` (Next.js front end, :3000) · `apps/api` (NestJS, :3001 — HTTP/auth/tRPC/Google sync) · `apps/agent` (the **eve** durable research agent, its own deployment, :2000). Shared packages: `packages/db` (Prisma schema + client + the `agent-tasks`/`workspace`/`settings`/`blob`/`images` helpers), `packages/auth` (Better Auth + allow-list), `packages/ui`, `packages/env`, `packages/typescript-config`.
- **`turbo.json`:** `globalPassThroughEnv` lists every runtime secret (DB, auth, all vendor keys, `AGENT_URL`, `AGENT_BRIDGE_SECRET`) so Turbo doesn't silently strip them (documented war-story: `API_URL` stripped from the Next build shipped the localhost fallback to prod; `DATABASE_URL` stripped broke `eve build`). Only `API_URL`/`APP_URL`/`NEXT_PUBLIC_API_URL` are real cache keys (inlined into the browser bundle). `dev` is `cache:false, persistent:true`.
- **`docker-compose.yml`:** just Postgres 17-alpine on :5432 (user/pw/db = postgres/postgres/crm) with a healthcheck — the only local infra dependency. Redis is optional and external (Upstash).
- **Deploy shape:** three independent Vercel deployments + a Postgres (Neon). "The only thing they must agree on is `DATABASE_URL` and `BETTER_AUTH_SECRET`." Agent schedules become Vercel Cron Jobs; the model routes through Vercel AI Gateway (OIDC, no key).

> **Collecct mapping:** Their **3-app split (front end / API / agent)** maps onto your **Next.js + Python API + Celery-agent-workers**. Their key insight — **the agent is its own deployment with its own work queue, not an in-request feature** — is your Agno-agents-run-by-Celery topology. `docker-compose` (Postgres only) → your OrbStack stack (MongoDB + FalkorDB + Redis). The `globalPassThroughEnv` discipline (declare every secret or it's silently stripped) is a real gotcha worth remembering for any Turbo/monorepo front end you keep.

---

## 7. CLOSING — the portable core for Collecct (priority order)

1. **The evidence model (`ContactFact` + `lib/evidence.ts` + `lib/facts.ts`)** — copy verbatim, re-weight for govcon. It is the mechanism behind "no confidence scores," "human outranks agent," and free change-detection. Highest-value steal.
2. **The `AgentTask` work queue** (dueAt + leasing + budget + reason + two lanes) → Celery-backed `agent_tasks`. "The row is the message"; cadence in `dueAt` not cron.
3. **`AgentEvent` audit substrate** — one append-only doc per agent step → the auditable "show your work" trail your bid decisions need for defensibility.
4. **"Intelligence never in the API"** — all scoring/ranking/drafting in Agno workers; the API only writes task rows and serves data.
5. **Config discipline** — required-vs-optional capabilities (missing key = disabled capability, never a throw), tenant secrets in per-org rows, told-at-startup-what's-on.
6. **Provenance everywhere** (`method`, `sourceUrl`, `fetchedAt`, `evidence`, raw payload retention) — GDPR/govcon-audit answer as a query.

**The one thing to consciously invert:** every single-tenant assumption (Rules 3, the `WORKSPACE_ID` singleton, "signed-in ⇒ see everything," ungated cache keys, "no organizationId on any record"). Collecct's `org_id` is load-bearing on **every** collection, RBAC is real, and their SECURITY.md's accepted risks are your must-fix list. Keep their table *shapes*; delete their singleton *reads*.

