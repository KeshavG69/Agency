# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

Collecct is one shared console serving three roles inside a small-to-mid government
contractor's business-development team. Role changes permissions, not the experience:

- **BD / capture rep** — the daily inhabitant. Reviews newly ingested SAM.gov
  opportunities, works the generated call plan, chases contacts and follow-ups,
  approves drafted email.
- **Capture manager / BD lead** — assigns bids, watches deadlines and gates across the
  whole team's pursuits, reviews what the agents produced before it goes anywhere.
- **Owner / executive** — occasional, decision-oriented. Pipeline health, bid/no-bid
  calls, what is due.

Admin-only sections today are **Library** (documents) and **Organisation** (settings).
Membership is invite-only per organisation.

## Product Purpose

Government contractors lose winnable work because opportunity discovery, relationship
history, and response deadlines live in three disconnected places: SAM.gov, an Outlook
mailbox, and a spreadsheet. Collecct pulls all three into one pipeline and then puts an
agent team on top of it that reads every solicitation, decides bid / no-bid / watch,
ranks what to pursue, and produces the prioritised **call plan** with drafted outreach.

Success is a rep opening Collecct in the morning and knowing — without assembling it
themselves — which pursuits matter today, who to contact, and what to say.

## Positioning

Not a general CRM with government fields bolted on, and not an opportunity-alert feed.
The differentiator is that the **full solicitation document is parsed and carried on the
record**, so every agent judgement is grounded in the actual PWS rather than the notice
summary — and each judgement is emitted as structured, inspectable evidence (named risk
factors with severity and reasoning, a priority score, a rationale), not prose.

The org's own SAM.gov/UEI profile drives the fit read, so the same opportunity scores
differently for different tenants.

## Operating Context

- **Ingestion:** daily NAICS-filtered SAM.gov poll (public search API; the bulk CSV is
  unreliable), plus Excel import of existing opportunity spreadsheets. Deduped on
  SAM.gov notice ID.
- **Outlook (per employee, by email):** contact ingestion goes through a work/personal
  review step before anything lands. Mail history is what the relationship and call-brief
  work reads.
- **SharePoint (admin-connected):** document library, folder structure, and per-employee
  access are crawled into their own graph. A Bid → SharePoint folder taxonomy
  (Solicitation / Capture Docs / Resources / Response) and two-way document sync are
  planned; when built, SharePoint is intended as the authoritative document store.
- **Pipeline stages:** Discover → Qualify → Capture → Pursue → Submitted → Won / Lost.
- **Sections:** Dashboard, Pipeline, Call Plan, Contacts, Library (admin),
  Organisation (admin). Left rail carries active Bid pursuits.
- **Rhythm:** deadline-driven. Response due dates, recompete dates, and gate reviews are
  the clock the whole product runs on.

## Capabilities and Constraints

- Agent roster: Analyst (bid/no-bid, priority, risk factors, call actions), Relation,
  Mail Writing, Capture, Company Research, Brief — under a CEO orchestrator.
- Structured agent output is a product fact, not an implementation detail: `bid_decision`
  (Bid / No-Bid / Watch), `risk_level`, a list of named `risk_factors` (capability,
  eligibility, competition, past performance, scope clarity, schedule, contract type,
  teaming) each with severity and a one-line note, `priority_score` 0–100, and a rationale.
- Org keywords are a **ranking** signal only — they never drive a bid decision and never
  filter ingestion.
- Multi-tenant: sold to many govcon firms. Per-org UEI → SAM.gov profile, invite-only
  membership, self-serve Outlook and SharePoint connect flows. The org onboarding path is
  a first-class surface, not an afterthought.
- Stack in place: Next.js 16 / React 19 / Tailwind 4 frontend (TanStack Query for server
  state, Zustand for client state, nuqs for URL state); FastAPI + Agno backend; FalkorDB
  for the contact and SharePoint graphs; Redis/Celery for scheduled work.
- Out of scope (frozen): pricing, resume, proposal, and solution generation. Pricing
  intelligence lives in a sibling product (PriceIQ); the long-term join is a shared
  Contract entity, not a merged UI.

### Hard constraints — never violate

1. **The AI never sends. It drafts.** Every outbound email, reply, and follow-up is a
   draft a human approves. No agent-initiated send, in any surface, ever.
2. **Agent output must show its evidence.** A bid/no-bid call, a priority score, or a
   brief must expose what it was based on. An unexplained verdict is a defect, not a
   terse design.

### Explicitly undecided

- GCC High / restricted-M365 support is not a committed constraint today.
- SharePoint-as-source-of-truth is the plan for documents, but the two-way sync that
  would make it true is not built.

## Brand Commitments

Product name: **Collecct**, rendered in the top bar as `Collecct.` with a terminal dot.
No other binding identity constraints have been established.

## Evidence on Hand

- Real data throughout: live SAM.gov opportunity records, real Outlook contacts and mail
  history, real SharePoint document structure. Design should assume populated, messy,
  long-tail data — not tidy samples.
- Parsed solicitation documents (`document_text`) on opportunity records.
- **No** customer testimonials, case studies, logos, benchmarks, pricing tiers, press, or
  usage statistics exist. Future work must not fabricate them.

## Product Principles

1. **Deadline is the organising axis.** Anything that hides how much time is left on a
   pursuit is working against the user.
2. **Judgement is shown, never asserted.** Every score, verdict, and ranking carries the
   reasoning and the named factors that produced it, close enough to act on.
3. **The human holds the pen.** Agents prepare; people approve, edit, and send.
4. **One console, three altitudes.** The same screens must serve a rep grinding a pursuit,
   a lead scanning a portfolio, and an owner checking health — without three products.
5. **Real data or nothing.** Absent facts stay visibly absent. Empty and partial states
   are designed, not fabricated around.

## Accessibility & Inclusion

No product-specific standard has been established.
