# The Relationship Engine

**Status:** proposed (Phase 1)
**Owner:** BD/capture rep, in the Today view
**Depends on:** the connected Outlook mailbox, the contact graph (FalkorDB), the facts
store, the action plan.

---

## Why this exists

Today the CRM layer only wakes up when an **opportunity** does. A solicitation lands, the
Analyst judges it, the Relation agent finds contacts for *that opportunity*. Nothing in the
system ever says "you have not spoken to Dave in seven weeks and he is one of your warmest
contacts — reach out." Relationships decay silently between deals, and the rep is the only
thing holding the thread.

The Relationship Engine is the inverse of the opportunity flow. It is **relationship-first,
not deal-first**. It runs on a clock, sweeps the whole network, and answers one question per
person: *does this relationship need a touch right now, and if so, what would I say?* When
the answer is yes, it hands the rep a written message and the evidence behind it. The rep
glances and taps once.

It covers two kinds of touch, from the same mailbox and the same machinery:

1. **Going-cold** — a warm relationship that has gone quiet past its natural rhythm.
2. **Personal hook** — a human thread worth pulling ("you talked golf on Aug 3", "his kid
   started at Purdue"), independent of any business reason.

## Scope (Phase 1)

- **Source: the Outlook work mailbox only.** No personal mail, no calendar, no Teams/chat.
  Everything is read from correspondence we already ingest.
- **Send policy: one-tap approve.** The agent drafts everything. Approving a card creates
  the Outlook **draft** and (on the rep's tap) sends it. Nothing sends autonomously. This
  keeps the product's one hard rule intact: *the AI never sends on its own; the human holds
  the pen.*
- **Surface: the Today view.** Relationship nudges are action cards alongside the existing
  deadline-driven ones. No new top-level section.
- **Every card shows its evidence.** A nudge that cannot say *why* it fired is a defect.

Explicitly **out** of Phase 1: Teams/chat ingestion, personal mail/calendar, autonomous
send, a standalone Relationships page. Each is an additive Phase 2+ piece and changes
nothing below.

---

## The four pieces

### 1. Personal facts lane (facts store)

The facts store already does propose → human approves/dismisses → evidence attached, but its
vocabulary is strictly professional:

```
FACT_FIELDS = {title, company, industry, phone, seniority, function, linkedin, website}
```

We add a **relationship lane** — same store, same lifecycle, new fields:

| field             | example value                     | drives                          |
|-------------------|-----------------------------------|---------------------------------|
| `interests`       | "golf", "cycling", "BBQ"          | personal-hook nudges            |
| `shared_activity` | "played golf 2026-08-03"          | personal-hook nudges (dated)    |
| `family`          | "daughter at Purdue"              | personal-hook nudges            |
| `key_date`        | "work anniversary 2026-10-01"     | date-triggered nudges           |
| `personal_note`   | free text the rep or agent jots   | context on the card             |

Two ways a personal fact is born, both reusing the existing propose/approve flow:

- **Extracted from mail.** A mail-scan step reads correspondence for the contact and
  proposes personal facts with the message as evidence. Same weak/verified banding as
  professional facts — a throwaway line is too weak to store; a clear, repeated signal is
  proposed for the rep to confirm.
- **Manual note.** The rep types "played golf 8/3" on the contact. This is the *only*
  reliable source for things email never states outright, and it is nearly free to build.

A human's decision on a personal fact is final, exactly as today (`DISMISSED` is never
re-offered; a human-set value is not overruled by a machine one).

### 2. Cadence & staleness (arithmetic, no model)

We already store, per relationship, per employee:

- `corr_count` — how many times this employee has corresponded with the person (warmth).
- `last_contact` — the most recent touch.

From these we derive, with **no LLM** (this is arithmetic, like the action planner):

- **Expected cadence** — a target rhythm from warmth. A very warm contact (high
  `corr_count`) has a tighter expected cadence than a light acquaintance. Concretely: bucket
  `corr_count` into a target interval (e.g. warm → ~30 days, moderate → ~60, light → skip).
- **Staleness** — `days_since(last_contact) − expected_cadence`. Positive means overdue.
- **Nudge-worthy** — overdue **and** warm enough to matter. Cold, one-off contacts never
  generate noise.

Why no model: same reasoning as the action planner. Given unchanged data it must give the
same answer every day, cost nothing per contact, and be inspectable. An LLM here would be
slow, expensive per-contact across a big network, and non-deterministic.

### 3. The Relationship Agent (scheduled)

A **nightly Celery task** (sits next to `action_plan.daily`), fanning out lightly across the
network:

1. **Select candidates** — cadence/staleness picks the going-cold set; open personal facts
   with a live hook (a recent `shared_activity`, an upcoming `key_date`) add the rest. This
   is cheap arithmetic + a graph query, not a model, so it scales to thousands of contacts.
2. **Draft, per selected contact** — *here* an LLM writes the actual message, grounded in:
   the relationship history summary, the personal facts, and the rep's own voice. Going-cold
   gets a light re-engagement ("been a while — how's things at [company]?"); a personal hook
   gets the specific thread ("up for golf this weekend?"). Short, honest, in the rep's tone.
3. **Emit an action** — one `Action` per nudge, with the drafted message on it and the
   evidence line filled in. Written as an **upsert on a dedupe key** so the nightly run and
   any event-driven run cannot duplicate, resurrect, or re-nudge something the rep already
   settled — identical safety guarantee to the action planner.

The draft is attached but **unsent**. The agent's job ends at "written and explained."

### 4. The Today card (surface)

Two new `ActionKind`s join the existing set (`analyze`, `call`, `reply_mail`, …):

```
relationship_touch     # a warm contact has gone quiet
relationship_personal  # a live personal hook worth pulling
```

The card renders like any other Today action — a verb, a subject, a reason — plus the
drafted message and a one-tap **Approve & send** (creates the Outlook draft and sends on the
tap). Standard action lifecycle applies: **snooze** ("not this week") and **dismiss** ("not
doing this") already exist and work unchanged.

Evidence is mandatory and lives in the `reason` line, e.g.:

> **Reach out to Dave Chen at Lockheed** — warm (42 emails), quiet 47 days; you talked golf
> Aug 3.

An unexplained nudge does not ship.

---

## Implementation approach — playbook + deterministic code + a proof gate

This codebase **already uses the "playbook" pattern** the team has discussed: `bd_skills/`
is a folder of `SKILL.md` files loaded by Agno's `LocalSkills` with progressive disclosure
(name + description in the prompt, full text pulled on demand) — versioned markdown that is
corrected without a code deploy. See `agent/skills_registry.py`. The Relationship Engine
reuses this, but a production multi-tenant backend is not a single-user chat window, so the
three "delegation loop" layers map onto three different places:

- **Process → a new skill.** `bd_skills/relationship-outreach/SKILL.md` holds the judgement:
  how to read cadence, choose a personal hook over a generic re-touch, and write short and
  honest in the rep's voice. Correctable as markdown, no deploy.
- **Toolbox → existing tools + one helper.** The graph search, the facts store, and a new
  deterministic cadence/staleness helper. Reused, not rebuilt per run.
- **Proof → a new verification gate (this system does not have one yet).** Before a draft
  becomes a card it must pass **checks that can fail** — if a check cannot fail it is not a
  check:
  1. recipient email **matches the contact** in the graph (no wrong-recipient send),
  2. every personal detail the message cites (golf, "Purdue") **exists as a stored fact**
     (no hallucinated hook),
  3. no business/opportunity claim without a source,
  4. message within a length bound.
  A failed check drops the card; it never reaches the rep. This gate is what makes one-tap
  approve safe.

**What is NOT a playbook, on purpose.** Cadence/staleness and dedupe are plain arithmetic,
no LLM — identical reasoning to the action planner: same answer every day, no per-contact
cost, fully inspectable. Only the *judgement* (which hook, what to say) runs through the
agent + skill. The engine is a **hybrid**: deterministic selection → agent drafting under a
skill → proof gate → one-tap card.

**The loop.** When a rep edits or dismisses a draft, that is signal, not noise: a dismissed
personal fact is never re-offered (facts store already does this), and repeated edits of the
same kind are a cue to correct `relationship-outreach/SKILL.md` — fix the playbook, not the
one chat.

## How the two hard constraints are honored

1. **The AI never sends.** Every card is a draft. Approve is a human tap; there is no code
   path where the agent sends. One-tap is *effort* removed, not the *human* removed.
2. **Judgement is shown.** Staleness math and the personal fact that fired the nudge are on
   the card. The rep can see exactly why this person, why today.

## What "fully agentic" means here (and what it does not)

Your boss's ask — "he doesn't have to worry about the CRM" — is satisfied by the agent doing
**all the analysis and all the writing**. The rep never assembles who-to-contact or
what-to-say; it arrives done. The single retained human act is one glance and one tap, which
is what protects the relationship from an unrecallable wrong-recipient or wrong-tone message.
That is the deliberate line, and it matches the product's stated rule.

## Build order

- **1.1 — DONE.** Personal facts lane: `PERSONAL_FACT_FIELDS` (multi-valued), the
  `human.manual-entry` evidence kind, `record_manual_fact` + `personal_facts`, and the
  `POST /api/intelligence/contacts/{email}/facts` manual-note endpoint.
- **1.2 — DONE.** `utils/cadence.py` (warmth→cadence, staleness, `select_going_cold`) over a
  new `graph_store.list_relationship_signals` read. Pure arithmetic, unit-tested.
- **1.3 — DONE.** `utils/personal_llm.py` + a second per-sender read folded into the mail
  sweep, proposing personal facts (`llm.mail-personal-extraction`, non-primary → suggestions).
- **1.4 — DONE (backend).** `agent/relationship_agent.py`, the `utils/nudge_proof.py` proof
  gate, `client/nudge_store.py`, the nightly `relationship.daily` task, the two new action
  kinds, planner integration (`_plan_relationship_nudges`), and the
  `/api/relationships/nudges` read + decide endpoints.
- **1.5 — DONE (frontend).** Personal-notes UI (`components/agent/PersonalFacts.tsx`) wired
  into the contact review panel (`SuggestionsReview.tsx`): grouped multi-valued facts with
  add/remove, plus mail-scanned personal suggestions to confirm. Today cards for the two
  relationship kinds (`ActionCard.tsx`) open `NudgeDialog.tsx` — the drafted message + its
  evidence, editable, with **Approve & send** (posts the existing human-send path
  `/api/mail/send`, then records the decision) or **Dismiss**. `tsc --noEmit` clean.

Tests: `scripts/test_facts_store.py`, `test_personal_facts.py`, `test_cadence.py`,
`test_nudge_proof.py` — all green.

## Hardening pass (advisor review, 2026-09-18)

An adversarial review of the Phase 1 build found defects that would have made the first real
run either fire nothing or embarrass the user. All are fixed and covered by tests.

**Showstoppers, verified against live data and fixed**

1. **The nightly sweeps dispatched nobody.** `sweep_all_relationships` *and* the pre-existing
   `mail_sweep.daily` selected users by a top-level `users.organization_id` — a field that does
   not exist (membership lives in `organizations[]`; `organization_id` is derived per request).
   Measured on the real database: **0 of 5 users matched.** Both now use one shared
   `utils.organizations.iter_active_memberships()`.
2. **One tap would have mailed `Best, [Your Name]` to a customer.** The engine always knows the
   sender (the mailbox owner), so the rep's real name is now substituted at draft time, and a
   `no_placeholders` gate check fails any draft still carrying a `[...]` blank.
3. **A decided contact was silenced forever, and its record was overwritten.** The dedupe key
   was static per (owner, contact, kind), so after one approve/dismiss the engine went quiet
   for that contact permanently — after one pass over the network, quiet entirely. The key now
   carries a **cycle token** (the contact's `last_contact` day), and `upsert` refreshes a draft
   **only while the nudge is open**, so a decided row stays the record of what the rep actually
   approved.
4. **Ranking surfaced the deadest contacts first.** Sorting by "most overdue" put year-silent
   contacts at the top of the list. Now: anything past `LAPSED_CEILING_DAYS` (240) is dropped
   as lapsed, and the rest rank by **warmth first, then most-recently-lapsed**.
5. **Personal hooks could reach government contacts.** "Up for golf?" to a `.gov`/`.mil`
   contracting officer is a procurement-integrity problem. Those contacts are now forced to the
   professional `relationship_touch` variant, whatever personal facts we hold.
6. **Cadence was tuned for consumer sales, not govcon.** 30/60 days became **90/180** — monthly
   "just checking in" to a program manager reads as spam on a 6-18 month deal cycle.

**Quality improvements in the same pass**

- **The last exchange is on the card.** The mail sweep now stores `last_subject` alongside
  `last_contact` (written only when the date advances, so subject and date can never disagree).
  The card reads *"last spoke about "GITSS-A recompete" on 2026-08-03"* — a reason, not a metric
  — and the draft is told about the thread so it can pick up where they left off.
- **Sending records the touch.** Approving a nudge writes `record_touch` to the graph
  immediately, so tomorrow's card does not repeat a staleness the rep just fixed, and the
  contact correctly re-enters the cycle later. This also makes the engine correct regardless of
  whether the mail listing covers Sent Items.
- **`corr_count` means something again.** Automated senders (`noreply@`) no longer earn warmth,
  and a message addressed to more than `MASS_RECIPIENT_THRESHOLD` people is treated as an
  announcement, not a relationship.
- **Drafted once, not nightly.** An existing open nudge has its cadence numbers refreshed but
  its message left alone — one frontier-model call per nudge instead of per nudge per night,
  and the rep never sees a draft change under them.
- **The gate no longer trusts the model's own account.** `grounded` could only check what the
  draft *admitted* to using, so a body inventing "the Aspen trip" while declaring
  `drew_on=["golf"]` passed. `no_invented_specifics` now fails any proper noun in the body that
  cannot be accounted for by the contact, their employer, the sender, our company, the last
  thread, or a stored fact — with the allowlist seeded from the recipient's own address so a
  sparsely-known contact is not gated out for being named.

**Tests:** `test_cadence`, `test_nudge_proof`, `test_nudge_store`, `test_relationship_sweep`
(end-to-end, model stubbed), `test_facts_store`, `test_personal_facts`.

## Second hardening pass (advisor re-review, 2026-09-18)

A second adversarial review checked whether the first fix pass was correct. It found one
defect the *fix pass itself introduced* — plus several places where a fix was right in
isolation but the surrounding wiring undid it.

**The crash the first pass introduced (would have killed the demo)**

`facts_store.personal_facts()` returns rows (`{id, value, decided_by, …}`) because the contact
UI needs the id to remove a note. The sweep passed those rows straight through as if they were
strings, so the proof gate called `.strip()` on a dict: **`AttributeError`, on the first contact
that had a personal fact — i.e. exactly the golf scenario.** It sat outside the per-contact
guard, so the whole employee's sweep died with it.

Worse, the end-to-end test *hid* it: the `FakeFacts` stub flattened the rows to strings, which
production does not. **A stub easier to consume than the real interface proves nothing.** Both
are fixed — `_fact_values()` converts at one boundary, the stub now returns the production
shape, and the gate moved inside the per-contact `try`.

**Fixes that were right but undone by their surroundings**

- **The network starved after one pass.** The cap was applied *during* selection, so the same
  8 warmest contacts were chosen nightly; once the rep decided all 8, every later sweep
  re-picked them, skipped them as decided, and drafted nothing for contacts 9…N. The cap now
  applies **after** dedupe, so a day's budget is spent on contacts not yet dealt with.
- **Stale cards outlived the silence they described.** Nothing ever retired an open nudge whose
  cycle had ended, so after a contact replied the card stayed on Today saying "quiet 95 days" —
  and one tap would send "it's been a while" to someone who wrote yesterday. Open nudges not
  re-selected by a run are now expired, and their Today cards closed with them. (This also
  fixes the two-open-cards case when a contact's facts change and the nudge kind flips.)
- **Cards were visible to the wrong rep, and would send from the wrong mailbox.**
  `_users_by_email` queried `organizations.organization_id` with a **string** against an
  **ObjectId** field, matching nobody — so every off-chain action was written unassigned, and
  an unassigned action is visible to the whole org. One rep's "Message Dave" appeared on a
  colleague's Today, and whoever tapped it sent from *their* Outlook with someone else's name
  in the sign-off. Now matches either representation. (Pre-existing; it also affected
  `reply_mail` cards.)
- **"Last exchange" would have been blank on every card.** `last_subject` was written only when
  `last_contact` strictly advanced — but the contacts the engine selects are silent ones whose
  last message predates the field, so it would never fill. Now backfills when no subject is
  stored.
- **A rep with no resolvable name got silent 100% gate-out.** The lookup was case-sensitive
  while email signup does not lowercase, so the agent fell back to `[Your Name]` and the gate
  rejected every draft, logged at INFO. Now case-insensitive, and a missing name **skips the
  sweep with a warning** instead of manufacturing drafts that cannot pass.
- **The refresh path could contradict the draft.** Day-2's reason was rebuilt from "the first
  fact we hold" rather than the one the message was written around, so a card could say
  "you noted: cycling" above a message about golf. The hook is now stored on the row and reused.
- **Model text starting with `$` would corrupt the write.** Inside an aggregation pipeline a
  string beginning with `$` is read as a field path, so a subject like "$5M recompete" would
  null the field or fail. Values are now wrapped in `$literal` (verified with a `$5M` subject).
- **Gate-outs are now visible.** A dropped draft means the rep silently gets nothing, so it logs
  at WARNING with the failing check names, and the sweep returns `gate_failures`.

**What the empirical dry-run showed (more important than any of the above)**

Running the real selection path against live data: **3,198 of 3,219 contacts have
`corr_count = 0`**, only 21 carry any date, and **zero** nudges would fire. The `mailbox_sync`
bookmark shows the mail sweep last ran **2026-08-05** and never since — the dispatcher bug.
All correspondence signal has been frozen for six weeks. Unblocking the dispatcher does not
retroactively create history: the sweep has to actually run, and with only ~46 days of mail in
the graph, a 90/180-day cadence cannot fire at all. **Per-org cadence settings and a deeper
backfill are prerequisites for a live demo, not nice-to-haves.**

## Still open (ranked)

- **Event-driven triggers — the highest-value remaining work.** Cadence is a fallback; the real
  reason to reach out is an event we already ingest: a new SAM.gov notice in their agency/NAICS,
  an award to their company, a title change (we already store `SUPERSEDED` rows), a pursuit
  milestone. "Dave's office posted a sources-sought in 541512 yesterday" is what makes a rep
  send rather than dismiss.
- **Pursuit-aware suppression** — never nudge a contact attached to a pursuit in source
  selection. Needs contact→pursuit linkage on the nudge.
- **Per-org cadence settings and per-contact overrides** (key-account flag).
- **Teammate collision check on the card** — `/api/mail/collisions` already exists, unused here.
- **Rep voice sample** — feed a few of the rep's own sent notes into the drafting prompt.
- **Mail-mined personal facts are PROPOSED only**, so they reach the engine only after a human
  confirms them in the review panel. Surfacing them on the nudge card for one-click confirm is
  the missing link.
- **`bd_skills/relationship-outreach/SKILL.md`** referenced above does not exist yet.
- **Data policy** — personal facts are org-scoped and mail bodies go to OpenRouter; both want an
  org-level toggle before a DFARS/CMMC-conscious customer asks.

## Open questions

- **Expected-cadence buckets** — the exact `corr_count` → interval mapping wants one round
  of tuning against real mailbox data, not a guess. Start with 30/60/skip and adjust.
- **Volume cap** — a sensible daily ceiling on relationship nudges so Today stays a work
  plan, not a wall. Suggest a small per-day cap, newest hooks and most-overdue first.
- **Reply-detected suppression** — if the rep has already replied recently, the nudge should
  stand down. Covered by `last_contact`, but worth verifying against the mail-sync bookmark.
