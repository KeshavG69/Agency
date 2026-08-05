# trycompai/crm — Deep Dive & Implementation Playbook for Collecct

**What this is.** An exhaustive teardown of the open-source [`trycompai/crm`](https://github.com/trycompai/crm)
("Comp") repo, turned into a copy-ready plan for *our* system (Collecct). It was produced by cloning
the repo and running six parallel investigators over it (agent runtime, prompts, backend, frontend,
design system, data model). This file is the **curated, ranked, actionable layer**; the exhaustive
per-area notes live in the appendix files next to it.

**The one-line thesis of their whole product:**
> "The agent is not a feature of the CRM; the CRM is where the agent keeps its notes."

Everything worth stealing flows from three ideas: **evidence, not confidence** · **the queue is the
schedule** · **the API is dumb (all intelligence lives in the agent)**.

> ⚠️ **The one thing NOT to copy:** Comp is *deliberately single-tenant*. Collecct is multi-tenant
> (orgs + RBAC). Every "the agent may read everything" must become "…everything **within one
> `organization_id`**." See [§13](#13-the-multi-tenant-inversion-the-one-thing-not-to-copy).

---

## How to read this bundle

| File | What's in it |
|---|---|
| **`README.md`** (this) | The ranked playbook + phased roadmap. Start here. |
| `01-agent-brain.md` | The agent runtime: the lease/queue loop, all ~20 tools, the sandbox, `schedule_recheck`, the noisy-OR scorer. |
| `02-prompts-and-skills.md` | **Every prompt & skill, verbatim.** The runtime-assembled instruction layers, all 4 domain skills, all 20 tools' prompt text. |
| `03-backend-plumbing.md` | NestJS + tRPC, the agent↔UI bridge, Gmail/Calendar sync, the fact write-path. |
| `04-frontend-look.md` | The shell/layout, theming, the `DataTable`+nuqs pattern, and the **Agent tab** internals. |
| `05-design-system.md` | The palette (verbatim), the design laws, `no-useEffect`, shadcn rules — the "pro look" recipes. |
| `06-data-model-and-decisions.md` | Full Prisma schema (facts/queue/events), the architecture rules & rationale, the enrichment-agent plans. |
| `reference/` | **Verbatim source files** copied from their repo — the 4 skills, `agent-instructions.md`, `lib-evidence.ts`, `lib-facts.ts`, `ui-globals.css`. Drop-in reading. |

---

## The 12 things to steal, ranked

Ranked by (value to Collecct × how cleanly it ports). "Effort" is rough dev-days.

| # | Steal this | Why it matters for us | Effort | Detail |
|---|---|---|---|---|
| 1 | **Evidence, not confidence** (the ledger + scorer) | Kills hallucinated facts across enrichment *and* bid/no-bid. Fixes our undifferentiated "guessed vs verified" company data. | 2–3d | [§1](#1-evidence-not-confidence--the-crown-jewel) |
| 2 | **The queue *is* the schedule** (durable lease loop) | Turns our dead `company_needs_research` flag into real autonomous work. One mechanism powers all recurring agent work. | 2d | [§2](#2-the-queue-is-the-schedule) |
| 3 | **`schedule_recheck`** (self-scheduling + a reason) | Our Analyst runs once and forgets. This makes a "Watch" opp auto-revisit when the RFP drops. | 1d | [§3](#3-schedule_recheck--the-agent-sets-its-own-follow-ups) |
| 4 | **Fact write-path state machine** (facts vs suggestions) | "Never overwrite a human / never re-offer a dismissal / never write without a source" — enforced in code, not prose. | 2d | [§4](#4-the-fact-write-path--a-state-machine-not-a-prompt) |
| 5 | **"Intelligence never in the API"** boundary | Clean seam: routers just drop a task row; agents own all vendor calls + writes. We already have this shape (routers → Celery). | design | [§5](#5-intelligence-never-lives-in-the-api) |
| 6 | **Identity-matching skill** ("guess where to look, never what you'll find") | Directly upgrades our contact resolution; ports 1:1 to Outlook+SAM. | 1d | [§6](#6-identity-matching-the-skill) |
| 7 | **The sandbox** (deny-all egress, no DB creds) | We handle CUI/FOUO gov data — this is the right posture for any shell/REPL tool. | 1d | [§7](#7-the-sandbox--a-shell-with-neither-creds-nor-network-is-a-text-processor) |
| 8 | **The Agent tab** ("show your work") | Serves the backlog item "better analyst bid/no-bid decisions" by making reasoning inspectable; presentation layer isolates cleanly onto Agno. | 3–4d | [§8](#8-the-agent-tab--show-your-work) |
| 9 | **Runtime-assembled layered prompt** (not one megaprompt) | Our agent prompts are giant inline f-strings. Layering (charter + per-record + who-we-are + capabilities) is cleaner and shareable. | 1–2d | [§9](#9-prompts-are-assembled-at-runtime-in-layers) |
| 10 | **The design system** (palette + laws) | The literal "professional look." Adopt token architecture + density + the two genuine bug-fixes; swap their green for our brand. | 2–3d | [§10](#10-the-professional-look--design-system) |
| 11 | **nuqs URL-state** for lists | Makes our Pipeline filters *shareable links* instead of localStorage-only. | 1d | [§11](#11-nuqs-url-state-for-the-pipeline) |
| 12 | **Enrichment-as-argument + signature-block** | Free person data (title/phone) from email signatures — replaces the paid Explorium person lookup we're dropping. | 1–2d | [§12](#12-enrichment-as-an-argument-not-a-purchase) |

---

## 1. Evidence, not confidence — the crown jewel

**The rule:** the model **never emits a confidence score.** It reports *what kind of thing it saw*
from a fixed vocabulary, and **deterministic code prices it.** Their stated reason: models grade
their own certainty badly and inflate it to look useful. "A confidently wrong fact about a customer
is worse than a blank field, because nobody can tell it is wrong."

This is two files. First, the skill that teaches the model to pick a `kind` (verbatim, from
`reference/skill-evidence.md`):

> You never set a confidence. You report what you saw, and the ledger prices it. Getting the `kind`
> right is therefore the whole job — it is the difference between a fact landing on a record and a
> rep being asked a question.

Their evidence kinds (primary = "can carry a fact alone", supporting = "true but not enough"):

| Kind | Weight | Primary? |
|---|---|---|
| `profile.email-match` | 0.95 | ✓ |
| `linkedin.employer-and-name` | 0.85 | ✓ |
| `crm.thread-reply` | 0.85 | ✓ |
| `crm.signature-block` | 0.80 | ✓ |
| `github.account-identity` | 0.80 | ✓ |
| `crm.meeting-attendance` | 0.70 | ✓ |
| `web.cited-claim` | 0.40 | ✗ |
| `handle.name-form` | 0.35 | ✗ |
| `search.cites-profile` | 0.35 | ✗ |
| `employer-only` | 0.20 | ✗ |
| `contradiction` | 0.00 | ✗ (holds the fact) |

Second, the scorer (verbatim, from `reference/lib-evidence.ts`) — **noisy-OR** combination, a hard
primary-source gate, and contradiction that *holds* rather than averages:

```ts
const CEILING = 0.99;
const CONTRADICTED = 0.45;
export const BAND_FLOOR = { VERIFIED: 0.85, PROBABLE: 0.55, POSSIBLE: 0.3 };

export function scoreEvidence(evidence: Evidence[]): Scored {
  if (evidence.length === 0) return { score: 0, band: null, hasPrimary: false, rationale: "No evidence." };

  const contradicted = evidence.some((i) => i.kind === "contradiction");
  const hasPrimary   = evidence.some((i) => WEIGHTS[i.kind].primary);

  // noisy-OR: independent sources each chip away at the "still unknown" mass
  const combined = evidence.reduce((remaining, i) => remaining * (1 - WEIGHTS[i.kind].weight), 1);

  let score = Math.min(CEILING, 1 - combined);
  if (contradicted) score = Math.min(score, CONTRADICTED);   // a clash caps the score, doesn't average it
  return { score, band: bandFor(score, hasPrimary), hasPrimary, rationale: rationaleFor(...) };
}

export function bandFor(score: number, hasPrimary: boolean): FactBand | null {
  if (score >= BAND_FLOOR.VERIFIED && hasPrimary) return FactBand.VERIFIED; // ← needs a PRIMARY source
  if (score >= BAND_FLOOR.PROBABLE) return FactBand.PROBABLE;
  if (score >= BAND_FLOOR.POSSIBLE) return FactBand.POSSIBLE;
  return null;                                                              // ← weak → dropped, not stored
}
```

Three subtle things worth copying exactly:
- **Noisy-OR, not sum/average.** Two weak sources combine to *more* than either, but never to certainty.
- **VERIFIED requires `hasPrimary`.** No pile of web citations can write a record; only a source that
  *identifies this person* can. A high score without a primary source stays a suggestion.
- **`contradiction` caps at 0.45.** Disagreement isn't "60% true" — it's unresolved, and a human should see it.

### → How we implement it in Collecct

This is the principled version of the exact gap we already found: our `company_enrich.py` writes a
*dataset-verified* company name and a *domain-derived guess* into the same field, indistinguishable
downstream. Port the model, with **gov-specific evidence kinds**:

```python
# backend/models/evidence.py  (new)
WEIGHTS = {
  # --- primary (identifies THIS person/company) ---
  "sam.poc-listed":          (0.90, True),   # named POC on a SAM.gov notice
  "outlook.thread-reply":    (0.85, True),   # they replied from that address on a synced thread
  "linkedin.employer_name":  (0.85, True),
  "outlook.signature-block": (0.80, True),   # their own signature (see §12)
  "sharepoint.authored-doc": (0.80, True),   # they authored/own a doc in our tenant
  "pdl.domain-company":      (0.80, True),   # company from the PDL dataset hit (company facts)
  "outlook.meeting-attend":  (0.70, True),
  # --- supporting ---
  "web.cited-claim":         (0.40, False),  # Exa/Perplexity result WITH url
  "handle.name-form":        (0.35, False),
  "domain-derived-name":     (0.30, False),  # ← our current "guess the company from the domain"
  "employer-only":           (0.20, False),
  "contradiction":           (0.00, False),
}
```

Then a `score_evidence()` that mirrors `scoreEvidence` (noisy-OR + primary gate + contradiction cap),
and store the band on the FalkorDB node / Mongo doc. Consequences:
- The **domain-derived company name becomes a `POSSIBLE` suggestion**, not an asserted fact — it renders
  in the UI as *"we think it's Raytheon — confirm?"* instead of pretending to be true.
- The **Analyst** stops implicitly trusting weak company data; it can read the band.
- Our existing prompt ethos ("mark unverified / gates to confirm" in `analyst_agent.py`) now has a
  data-layer backbone instead of living only in prose.

Full port table in `01-agent-brain.md` §15 and `06-data-model-and-decisions.md` §2–3.

---

## 2. The queue *is* the schedule

**Their pattern:** there is no separate scheduler. A single `* * * * *` cron leases whatever is *due*
from one `AgentTask` table using Postgres row-locking, so many dispatchers can grab disjoint work and
a crashed run self-heals when its lease expires:

```sql
-- lib/tasks.ts (paraphrased): lease due rows atomically
UPDATE agent_task SET lease_until = now() + interval '5 min', attempts = attempts + 1
WHERE id IN (
  SELECT id FROM agent_task
  WHERE due_at <= now() AND (lease_until IS NULL OR lease_until < now())
  ORDER BY priority DESC, due_at ASC
  FOR UPDATE SKIP LOCKED         -- ← many workers, no double-processing
  LIMIT 12
) RETURNING *;
```

All recurring work is just "a task with a future `due_at`." Nothing is a real cron except the lease tick.

### → How we implement it in Collecct

We're on Celery + Mongo + Redis, so:
- **`agent_tasks` Mongo collection**: `{organization_id, kind, ref_id, due_at, lease_until, attempts, priority, reason}`.
- The **`FOR UPDATE SKIP LOCKED` analogue in Mongo** is `find_one_and_update` with a filter on
  `due_at <= now AND (lease_until == null OR lease_until < now)`, setting a new `lease_until` — atomic per doc.
- A **Celery-beat tick** (we already have beat in `app/worker.py`) leases a batch and fans out the
  matching task (`research_company_task`, `reanalyze_opportunity_task`, …).
- **Two lanes** like theirs: mechanical work (SAM.gov ingest, domain→company) runs with no LLM; only
  research/judgement gets an Agno session. Mechanical work never queues behind an LLM run.

This is the missing engine that makes #1, #3, and #12 actually *run*.

---

## 3. `schedule_recheck` — the agent sets its own follow-ups

Their agent can write a **future** task for itself and **must attach a rep-readable reason**; fruitless
lookups trigger multi-day "stand-downs" so daily sweeps don't re-burn budget on the same dead end.

### → In Collecct
- Add `recheck_after_days: int | None` + `recheck_reason: str` to `AnalystVerdict`
  (`backend/models/verdict.py`). A "Watch" (Sources Sought / RFI) sets e.g. 14 days + *"re-judge when the
  RFP is expected to drop."*
- `analyze_opportunity_task` enqueues an `agent_tasks` row (from #2) instead of only dropping the passive
  `Revisit:` card at `analyst_tasks.py:51`.
- Add a stand-down: if research finds nothing, push `due_at` out and record why, so we don't re-spend the
  LLM budget nightly.

---

## 4. The fact write-path — a state machine, not a prompt

`reference/lib-facts.ts` enforces in **code** what a prompt can only ask for:
- **Never overwrite a human-entered value.** A `PROPOSED` fact yields to anything a rep typed.
- **Never re-offer a dismissed value.** Dismiss is permanent for that value.
- **Never write without a primary source.** The band gate from #1.
- `isDerivedName` distinguishes a *typed* name from an *email-derived placeholder* — the technical heart
  of "never write a fact you haven't read."

The lifecycle (from their `ContactFact` model): evidence → band `VERIFIED` writes `APPLIED` + the column;
`PROBABLE`/`POSSIBLE` → `PROPOSED` (a suggestion under an empty field); a human `accept`/`dismiss` moves
it to `APPLIED`/`DISMISSED`; a newer verified value marks the old one `SUPERSEDED` (which gives you **free
job-change detection**).

### → In Collecct
A `contact_facts` Mongo collection keyed `(organization_id, contact_email, field, value)` with
`{score, band, evidence[], status, decided_by}`. The only *API/router*-owned mutation is the human
"accept/dismiss" (mirrors their `contacts.decideFact`). Everything else is written by the Celery/agent
layer. This is what lets enrichment be safe to run repeatedly.

---

## 5. "Intelligence never lives in the API"

Their hard rule (from `docs/api.md`): NestJS serves HTTP/auth/tRPC/sync and, when something happens,
just **writes a task row**. The separate agent app owns every vendor client, the scoring model, and all
writes. They even *deleted* `apps/api/src/enrichment/` to enforce it — a key-existence check is delegated
over the bridge rather than done in the API.

### → In Collecct
We already have this shape (FastAPI routers → Celery tasks → Agno agents). The lesson is to **hold the
line**: routers validate + enqueue; they never call Exa/Explorium/LLMs inline or write enriched facts.
Audit `routers/*` for any enrichment that leaked in.

---

## 6. Identity matching, the skill

The single sharpest idea, verbatim from `reference/skill-identity-matching.md`:

> `pmarchetti@fernhill.com` is not a name. … Asking a model what it stands for produces "Paula
> Marchetti" — which happens to be right, and would have been just as confident had it been wrong. …
> searching *that* [surname] alongside the company returns `linkedin.com/in/paulamarchetti`. The guess
> went into the **query**, and the answer came from the profile. That is the shape of every match:
> **guess where to look, never what you will find.**

Plus **fail-closed** ("both `employerMatches` and `nameMatches`, or it is not them"), **read internal
history first** (a reply you already have is the strongest evidence anywhere), and a great "things that
look like evidence and are not" list (a search result, a matching first name, a plausible expansion).

### → In Collecct
Rewrite our contact-resolution guidance in `crm_agent.py` / enrichment around this. It maps 1:1 to
Outlook + SAM: `read_crm_history` → *read our synced Outlook threads first*; the LinkedIn verdict step →
same; add gov kinds (`sam.poc-listed`, `sharepoint.authored-doc`). Copy the "guess the query, not the
answer" line as a literal instruction.

---

## 7. The sandbox — "a shell with neither creds nor network is a text processor"

Their agent's shell sandbox has **no network and no `DATABASE_URL`**, deny-all egress on all three
backends. All 20 tools + `web_fetch` run *outside* it (in the app runtime); `web_search` runs at the
provider. Mailbox text may never enter the scratch FS or a third-party query. Rationale: "a shell with
credentials and network is exfiltration-shaped even in an internal tool."

### → In Collecct
We handle CUI/FOUO. Our `python_repl_tool` / any bash-like tool should follow this exactly: no secrets,
no DB handle, no egress in the sandbox; keep network/DB in curated tools. **And with multi-tenancy it's
*more* important** — a sandboxed step in an org-A run must never see org-B data or secrets.

---

## 8. The Agent tab — "show your work"

The crown jewel of their UI. On each record, a tab shows the agent's steps live, marks **rejected leads
with their reason**, and surfaces the agent's **unanswered questions inline** with option buttons a human
clicks to answer. It's durable: it reconciles **DB-archived events + a live session snapshot + 3s polling
while "working"**, so a browser refresh replays the whole reasoning trail. A "rejected" lead is simply a
tool output with `stored:false` → rendered as a warning-toned step *"{verb} — {reason}"*.

### → In Collecct
- Persist an **append-only `agent_events` collection** (`{organization_id, ref_id, step, tool, note, ts}`)
  — we already generate the `rationale`/`reason` text, we just don't store the trail.
- A read-only endpoint replays it; a React "Agent activity" tab renders steps.
- **A bid/no-bid `No-Bid` becomes a warning marker with its reason** — this directly serves the backlog
  item *"better analyst bid/no-bid decisions"* by making them inspectable and contestable.
- The eve-specific streaming bits isolate cleanly; the presentation layer drops onto Agno. Details +
  verbatim components in `04-frontend-look.md`.

---

## 9. Prompts are assembled at runtime, in layers

There is **no monolithic system prompt.** Each session's instructions are layered: a static charter
(`instructions.md`) + a per-record "## This session" block + a "## Who we are" (workspace) block + a
"## What you can use here" (capabilities) block, assembled on `session.started`. Capabilities are
**optional-by-default** — a missing API key is announced, never thrown, so the agent plans around what
the install actually has.

### → In Collecct
Our agent prompts are big inline f-strings that repeat the "never fabricate" ethos across the Analyst,
CRM, and Mail agents. Refactor to layers:
- A shared **`charter.md` / `grounding.md`** loaded into every agent (the anti-hallucination rules).
- A per-run **context block** (the opportunity, the org profile — we already build `company_context`).
- A **capabilities block** built from which integrations the org has connected (Outlook? SharePoint?
  which keys?), mirroring their "told at session start what exists."
- Store the stable guidance as **versioned markdown** (their skills model) so it's editable without a
  code deploy. Full verbatim prompt inventory in `02-prompts-and-skills.md`.

---

## 10. The professional look — design system

The "pro feel" is **density + restraint + one source of truth**, not decoration. From
`reference/ui-globals.css` and `05-design-system.md`:

- **One CSS file owns all tokens** via `:root` + `.dark` + a Tailwind `@theme inline` map. Never override
  component styles at the call site.
- **Only two things are ever filled**: `--primary` (go) and `--destructive` (stop). Everything else is a
  neutral chip. Their tokens:
  - `--primary: #006b4f` (brand green), `--destructive: #ae2e24` — **identical in light & dark** ("a brand
    colour that changes per theme is not one colour, it is two").
  - Flat backgrounds `#ffffff` / `#0f0f0f`; foreground `#171717` / `#f5f5f5`; **untinted** neutral greys;
    borders `#e2e2e2` / `#2a2a2a`.
  - `--ring` is the *only* intentionally per-theme token (`#006b4f` → `#40be96` in dark — "a ring only has
    to be seen").
- **One radius across themes** (`--radius: 5px`; `sm/md/lg` = 4/5/8px; literal radii banned).
- **Dense data-app scale**: 32px (`h-8`) controls, `text-xs` body, `text-sm` titles, `tabular-nums`,
  near-flat shadows (opacity ≤ 0.12), thin custom scrollbars, hover states via `color-mix(… black 12%)`.
- Engineering laws that keep it un-templated: **`no-useEffect`** (lint-enforced; server-first data via
  prefetch→hydrate), composition-over-boolean-props, semantic tokens only, `gap-*` not `space-*`.

### → In Collecct
- Adopt the **token architecture** and the **density scale** into our existing Tailwind app verbatim —
  but **swap `--primary`/`--ring` for our own brand color** (their green is *Comp's* brand, not ours).
- Take the two genuine bug-fixes they call out: **radius parity across themes** and a **visible dark-mode
  modal scrim**.
- Adopt `no-useEffect` + server-first fetching as a house rule. Full checklist in `05-design-system.md` §11.

---

## 11. nuqs URL-state for the Pipeline

Their lists put **all** state — query, sort, page, tab, facets, hidden columns — in the **URL** via nuqs
(a shared parser map + a `createListSearchParams` factory), with `placeholderData: keepPrevious` and
row-hover prefetch. Result: every filtered view is a **shareable link**, and back/forward just works.

### → In Collecct
Migrate our Pipeline filters from **localStorage → nuqs URL state**. A teammate can then paste "all
SDVOSB IT opps in NAICS 541512 closing this month" as a link. Low effort, high daily value. Pattern +
verbatim code in `04-frontend-look.md`.

---

## 12. Enrichment as an argument, not a purchase

Their plan docs rank **internal data first** (thread-reply 0.85, **signature-block 0.8**) *above* any
paid API, and treat LinkedIn as "an enricher, not a finder." The signature block at the bottom of an
email gives you a person's title/phone/company **for free** — exactly the person data we were paying
Explorium for.

### → In Collecct
- Add a **signature-parse step** to our Outlook pipeline. Note: today `fetch_outlook_message` only pulls
  the 280-char `bodyPreview` (the *top*); signatures are at the *bottom*, so pull the full body, parse
  server-side, store only extracted fields (`outlook.signature-block` evidence, weight 0.8).
- This is the same move we just made for *companies* (domain→PDL) applied to *people*, and it lets us
  **drop paid per-person enrichment**. Feeds directly into the evidence model (#1).

---

## 13. The multi-tenant inversion (the one thing NOT to copy)

Comp removed orgs on day one and re-added only a **singleton `workspace`** row to answer "who are we."
Their `where: { id: WORKSPACE_ID }` is a *constant*, and their SECURITY.md accepts "signed-in ⇒ see
everything." **For us that is a bug, not a feature.** Concretely:
- Every one of their `WORKSPACE_ID` reads → **`organization_id` scoping** for us. `organization_id` must
  be load-bearing on every collection, every graph node, every agent-task row, every agent-event.
- A sandboxed or research step in an org-A run must **never** touch org-B Outlook/SharePoint/CRM data.
- Their accepted "read everything" is our **must-fix list** — it lines up with our known hardening gaps
  (ungated data endpoints, the contact-graph clobber). See `06-data-model-and-decisions.md` §5–6.

---

## Suggested roadmap for Collecct

**Phase 0 — Evidence foundation (highest leverage, touches files we're already editing).**
1. `backend/models/evidence.py`: the gov `WEIGHTS` enum + `score_evidence()` (noisy-OR + primary gate + contradiction cap). *(#1)*
2. Rework `company_enrich.py` to emit **evidence kinds** instead of a bare name: dataset hit → `pdl.domain-company` (primary); the domain guess → `domain-derived-name` (supporting, → `POSSIBLE` suggestion). *(#1, #12)*
3. `contact_facts` collection + the write-path invariants (never overwrite a human / never re-offer a dismissal / band gate). *(#4)*

**Phase 1 — Turn on the autonomous loop.**
4. `agent_tasks` collection + a Celery-beat lease tick (`find_one_and_update`, two lanes). *(#2)*
5. `research_company_task`: consume the (now principled) `company_needs_research`, run Exa, write back `web.cited-claim` evidence + re-embed. Trigger it **demand-driven** from the Relation agent. *(#2, #6)*
6. Signature-block parse in the Outlook pipeline → `outlook.signature-block` facts; retire paid person enrichment. *(#12)*

**Phase 2 — Self-scheduling + transparency.**
7. `recheck_after_days`/`recheck_reason` on `AnalystVerdict`; enqueue rechecks instead of the passive card. *(#3)*
8. `agent_events` append-only log + the **Agent-activity tab** (No-Bid = warning marker with reason). *(#8)*

**Phase 3 — Polish + prompt hygiene.**
9. Layer the prompts (shared `grounding.md` charter + per-run context + capabilities); move the identity-matching + evidence skills into versioned markdown. *(#6, #9)*
10. Design-system pass: token architecture + density + brand-swapped palette + the two bug-fixes; Pipeline filters → nuqs URL. *(#10, #11)*

Everything in Phase 0–1 lands on files already open in this branch (`company_enrich.py`,
`contacts_tasks.py`, `verdict.py`, `graph_store.py`, `analyst_tasks.py`), which is why it's the natural
place to start.

---

*Sources: the six appendix files in this folder, and the verbatim files under `reference/`, all derived
from a full clone of `github.com/trycompai/crm`.*
