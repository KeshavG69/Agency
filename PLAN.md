# Nexagen AI Agency — Project Plan

**Strategy:** Build the CRM tool the boss asked for first (real data, real value), prove
it, then add the AI helpers on top. Each phase ends with something demo-able.
Total software cost: **$0** (only LLM usage + a server cost anything).

---

## Phase 0 — Set up the foundation
**Goal:** A government-shaped CRM + basic plumbing to fill it.
- Install **EspoCRM** (free) via Docker.
- Add government fields to "Opportunity": NAICS, set-aside type, agency, solicitation
  number, response deadline, recompete date, estimated value.
- Pipeline stages: Discover → Qualify → Capture → Pursue → Submitted → Won/Lost.
- Create an API user (login for our programs).
- Scaffold the Python "ingest service" (skeleton only).

**Need:** a server/laptop. **Effort:** ~1 day. **Demo:** govcon-tailored CRM (empty).

## Phase 1 — Fill the pipeline with real opportunities  ← the "wow"
**Goal:** Real federal opportunities flowing in automatically.
- **SAM.gov connector:** pull live opportunities by NAICS/set-aside → create CRM records
  on a schedule.
- **Excel importer:** load existing opportunity spreadsheets into the same pipeline.
- **De-duplication** via a small Postgres store.

**Need:** free **SAM.gov API key** tied to the company UEI (request now).
**Effort:** ~2–3 days. **Demo:** real opportunities in his pipeline — show the boss here.

## Phase 2 — Generate the Call Plan + Follow-ups  ← his exact ask
**Goal:** The output the boss named.
- First **Agno AI agent** reads the pipeline, ranks opportunities, writes a prioritized
  **Call Plan** (Call records) + **Follow-ups** (Tasks) back into the CRM.
- Trigger via button or schedule.

**Need:** Anthropic API key (~pennies/run). **Effort:** ~3–4 days.
**Demo:** one click → ranked call list + follow-up reminders.

## Phase 3 — Connect Outlook + recompete alerts
**Goal:** Two-way Outlook + renewal alerts.
- **Outlook (Microsoft Graph, free):** read emails/contacts; push call plan to his calendar.
- **Recompete detector:** pull contract end-dates from **USASpending.gov** (SAM.gov lacks this).

**BLOCKED until boss/IT answer:**
1. Which M365 cloud — regular / GCC / **GCC High**? (defense contractor often GCC High)
2. SAM.gov account tied to company UEI?

**Effort:** ~4–5 days once unblocked.
**Demo:** call plan on Outlook calendar + "contracts up for recompete soon."

## Phase 4 — Grow the AI team
**Goal:** One helper → the "AI agency." Three worker agents under the CEO orchestrator,
all reading/writing EspoCRM:
- **Analyst Agent** — pipeline/opportunities: bid/no-bid, prioritize, produce the call plan.
- **Relation Agent** — relationships: reads past email conversations, tracks the
  "last conversation" per organization/contact, flags who needs follow-up.
- **Mail Writing Agent** — drafts the email/reply to continue a thread (draft-only;
  human approves before send).
- **Shared memory:** start with shared session notes, upgrade to **Mem0 graph memory**.
- **CEO orchestrator** routes between them and combines answers.
- Email connector (Relation + Mail agents): Outlook (Microsoft Graph) or Gmail — TBD.

**Effort:** ongoing. **Demo:** one question → coordinated answer from the AI team.

## Phase 5 — Turn it into a product (optional, later)
- Custom branding, multi-tenant, EspoCRM **commercial license** (only needed for resale;
  internal use stays free).

---

## Order & dependencies
- **Do now (unblocked):** Phase 0 → 1 → 2 = the whole tool the boss asked for.
- **Chase in parallel:** the 2 IT questions (only block Phase 3).
- **Then:** Phase 3 → 4 → 5.

## Main risks
- SAM.gov ~1,000 pulls/day → pull incrementally, store locally.
- MS To-Do can't be created server-side → follow-ups go on the **calendar** instead.
- SAM.gov key tied to company UEI can take 1–4 weeks → start now.

## Connections layer
- **Composio** for reading/writing external apps (Outlook, CRM, Sheets) — has an Agno
  integration. Verify data-handling for GCC-High/DoD before routing live data through it.

## Free stack (what's free vs. paid)
| Component | Cost |
|---|---|
| EspoCRM core (CRM, pipeline, calls/tasks, REST API) | Free |
| SAM.gov API / USASpending.gov API | Free |
| Microsoft Graph / Outlook (already pay for M365) | Free |
| Python ingest + Agno agents | Free |
| Anthropic API (LLM) | ~pennies/run |
| Hosting | small VPS or existing infra |

> Paid EspoCRM extensions (Outlook add-on, Advanced Pack workflows) are **NOT needed** —
> our own Python/Agno layer replaces them.
