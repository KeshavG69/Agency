# Daily Action Plan — implementation spec

**Status:** built · **Branch:** `keshav` · **Written:** 2026-08-11

> **How to use this doc.** Self-contained working spec. If context is lost, read this top
> to bottom and continue without re-deriving anything. Update the **Status board** (§10)
> as items land.

---

## 1. The problem, in the boss's words

> "When he logs into Collecct, the page should tell him: *these are your tasks for today.
> You have to talk to these guys, you have to analyse these bids.* Right now there's too
> much data for him to see."

Clarified over two rounds:

**It is not a volume problem.** Forty items is fine. Forty items is a good day's work.

**It is a unit problem.** The Pipeline renders **objects** — "here are 40 opportunities,
the Analyst thinks these are Bids, now go figure out what to do about each one." The human
has to *derive* the action from the record. That derivation is the work he doesn't want to
do.

He wants **verbs**. The same data, already converted into tasks:

- *Call Donna Scandaliato at DLA about the UHF receiver overhaul*
- *Analyse this bid — it came in overnight and hasn't been read*
- *Approve capture on GITSS-A*
- *Decide bid / no-bid — Analyst says P74, High risk, named incumbent*
- *Reply to Annie Kazi, she asked about submission format yesterday*

**And the deadline sets the pace.** Closes in 3 days → the whole chain compresses into
today, everything is urgent. Closes in a month → the chain spreads out, and only its first
step is on today's page. The same opportunity produces the same tasks either way; the
deadline decides **which day each one lands on and how loud it is**.

So: **the action becomes the row, and the response deadline becomes the scheduler.**

---

## 2. Decisions locked (do not re-litigate)

| Decision | Rationale |
|---|---|
| **A new `actions` Mongo collection.** Not derived on the fly from opportunities. | Actions need their own lifecycle — done / snoozed / dismissed / rolled over. Derived-on-read cannot remember "he pushed this to Thursday". |
| **The planner is plain Python. No LLM.** | The judgement (bid/no-bid, priority, risk, who to call) has *already* been made by the Analyst and CRM agents. The planner only asks "what is the next step, and when is it due" — that is a state machine, not a reasoning task. Adding an LLM here would make the list slow, expensive and non-deterministic for zero gain. |
| **Backward-scheduled from `response_deadline`.** | This is the whole point (§1). Forward-scheduling ("do it when it arrives") is what the Pipeline already does badly. |
| **Actions never auto-execute anything.** | Every action is a *prompt to a human*. The agents already run on their own schedule; this list is the human half. |
| **No new agent, no new model call.** | See above. The one exception is that a `call` action *opens* the existing per-contact brief, which is already an LLM task on its own queue. |
| **Not built on `agent_tasks`.** | `agent_tasks` is the queue for **machine** work — leases, retries, budgets, stand-downs. An action is human work: it has no lease, cannot be retried by a worker, and needs snooze/dismiss. Different lifecycle, different table. They coexist. |
| **"Today" becomes the landing view**, replacing Dashboard as `INITIAL.view`. | Otherwise he still lands in the data, which is the complaint. Dashboard stays reachable in the top bar. |
| **Per-user by default.** Admins get an "everyone" toggle. | "*Your* tasks for today." An admin seeing the whole org's list by default recreates the firehose. |
| **Overdue rolls over, it does not accumulate silently.** | An untouched action stays `open` with a past `due_on`; the query picks it up as Overdue. No separate rollover job. |

---

## 3. Existing building blocks (nothing here is new)

| Signal | Where it lives | Feeds which action |
|---|---|---|
| `bid_decision` / `priority_score` / `analyst_rationale` | `opportunities` doc, written by `crm_store.apply_verdict()` | `analyze`, `decide` |
| `risk_level` / `risk_factors` | same, `models/verdict.py` | urgency + the "why now" line |
| `response_deadline` | `models/opportunity.py:24`, ISO `YYYY-MM-DD` | **the scheduler** |
| `capture_approved` / `captured_at` / `capture_failed_at` | `crm_store.py:192-240` | `approve_capture`, `retry_capture`, `review_docs` |
| Call rows + `status` (`Planned`/`Done`/`Dismissed`) | `crm_store.create_call()` / `set_call_status()` | `call` |
| Per-pursuit contacts (POC + Relation agent picks) | `crm_store.call_contacts()` | `call` (which contact) |
| Per-contact brief | `routers/calls.py` `POST /api/calls/brief` | the `call` action's primary button |
| Mail triage cards | `routers/mail_triage.py`, `crm_store.list_mail_triage()` | `reply_mail` |
| `assigned_to` / `_visibility_query` | `crm_store.py:410-435` | who the action belongs to |
| Beat scheduler | `app/worker.py:65` | the nightly planner run |
| Index bootstrap | `utils/db_indexes.py` | the `actions` indexes |

---

## 4. Data model

New collection **`actions`**. One document = one thing one human must do.

```python
{
  "_id":              ObjectId,
  "organization_id":  str,
  "assigned_to":      str | None,   # user id; None = unassigned (visible to all, per RBAC)

  "kind":             str,          # see §5 — analyze | decide | approve_capture |
                                    # retry_capture | call | review_docs | submit | reply_mail
  "opportunity_id":   str | None,
  "contact_email":    str | None,   # call / reply_mail
  "ref_id":           str | None,   # call_id, triage card_id — whatever the action acts on

  "title":            str,          # imperative, already rendered: "Call Donna Scandaliato at DLA"
  "reason":           str,          # why now: "Capture is done — the customer call is the next move"

  "due_on":           "YYYY-MM-DD", # THE DAY IT LANDS ON THE LIST (backward-scheduled)
  "hard_deadline":    "YYYY-MM-DD" | None,   # the opportunity's response_deadline
  "urgency":          str,          # critical | high | normal
  "infeasible":       bool,         # not enough runway left to finish the chain

  "status":           str,          # open | done | snoozed | dismissed | expired
  "snoozed_to":       "YYYY-MM-DD" | None,
  "completed_at":     datetime | None,
  "auto_completed":   bool,         # closed by the planner because the state satisfied it

  "dedupe_key":       str,          # f"{org}:{kind}:{opportunity_id}:{contact_email or ''}"
  "created_at":       datetime,
  "updated_at":       datetime,
}
```

**`dedupe_key` is the idempotency contract.** The planner runs daily and on demand; it must
never create a second "Approve capture on GITSS-A". Every write is an upsert on
`dedupe_key`, and the planner only ever touches `due_on` / `urgency` / `reason` /
`infeasible` on an existing open row — never `status`, so a snooze survives a re-plan.

**Indexes** (add to `utils/db_indexes.py`):

```python
actions.create_index([("dedupe_key", 1)], unique=True, name="uq_action_dedupe")
actions.create_index([("organization_id", 1), ("assigned_to", 1),
                      ("status", 1), ("due_on", 1)], name="ix_action_worklist")
actions.create_index([("organization_id", 1), ("opportunity_id", 1)], name="ix_action_opp")
```

---

## 5. The step ladder — the state machine

This is the heart of it. Each opportunity walks one chain. At any moment **exactly one
step is its next step**; that step becomes an action.

| # | `kind` | Fires when the opportunity… | Satisfied when… | `lead_days` |
|---|---|---|---|---|
| 1 | `analyze` | has no `bid_decision` and `ingesting != True` | `bid_decision` is set | 21 |
| 2 | `decide` | `bid_decision == "Watch"` | decision becomes `Bid` or `No-Bid` | 18 |
| 3 | `approve_capture` | `bid_decision == "Bid"` and not `capture_approved` | `capture_approved == True` | 14 |
| 4 | `retry_capture` | `capture_failed_at` is set | failure cleared / `captured_at` set | 12 |
| 5 | `call` | a call row exists with `status == "Planned"`, **or** `captured_at` is set and no call is Done | call status is `Done`/`Dismissed` | 10 |
| 6 | `review_docs` | `captured_at` set and documents exist and no `capture_reviewed_at` | `capture_reviewed_at` set | 7 |
| 7 | `submit` | `bid_decision == "Bid"`, captured, `stage != "Submitted"` | `stage == "Submitted"` | 3 |

`lead_days` = *how many days before the deadline this step should be finished.* It is the
runway the step needs, not its duration. `analyze` at 21 means "an unread bid closing in
three weeks is due today"; `submit` at 3 means "the submit reminder appears three days out."

**`reply_mail` is off-chain.** It comes from mail triage, not from the pursuit's lifecycle.
`due_on = today` always (a customer question waits for nobody), `urgency = high` when the
card is tied to a Bid whose deadline is inside 14 days, else `normal`.

**Terminal opportunities produce nothing**: `bid_decision == "No-Bid"`, `stage` in
`("Won", "Lost", "Submitted")`, or `response_deadline` in the past.

### 5.1 Backward scheduling

```python
days_left = (hard_deadline - today).days   # None when there is no deadline

if days_left is None:
    due_on = today                          # no clock on it; do the cheap step now
elif days_left <= lead_days:
    due_on = today                          # we are inside the window — it is due NOW
else:
    due_on = hard_deadline - lead_days      # it can wait; it will surface on its day
```

### 5.2 Urgency

```python
slack = days_left - lead_days               # negative = behind schedule

slack is None (no deadline) -> "normal"
slack < 0                   -> "critical"
slack <= 2                  -> "high"
else                        -> "normal"
```

### 5.3 Infeasible

`remaining_lead` = the `lead_days` of the *earliest* step still outstanding (step 1's 21
days already accounts for everything after it — the ladder is cumulative, not additive).

```python
infeasible = days_left is not None and days_left < remaining_lead * 0.25
```

An infeasible action is not hidden and not silently dropped. It renders with a distinct
treatment and the reason reads: *"Closes in 2 days and capture hasn't started — realistically
this can't be delivered. Drop it or escalate."* Dismiss is the primary button, not Done.

### 5.4 Assignment

`assigned_to` is copied from the opportunity's `assigned_to`. For `call` and `reply_mail`
it is the rep whose mailbox the brief/card belongs to. Unassigned opportunities produce
unassigned actions, which every member of the org sees (matching `_visibility_query`).

---

## 6. The planner

New file **`backend/tasks/action_plan_tasks.py`**, Celery task `action_plan.daily`.

```
for each organization:
    opps = crm.list_all(org)                       # already RBAC-free at store level
    for opp in opps:
        if terminal(opp): expire_open_actions(opp); continue
        step = next_step(opp)                      # §5 ladder, first unsatisfied
        if step is None: expire_open_actions(opp); continue
        upsert_action(org, opp, step)              # §4 dedupe_key
        expire_open_actions(opp, except_kind=step.kind)   # the chain moved on
    for card in crm.list_mail_triage_open(org):
        upsert_reply_action(card)
    unsnooze(org)                                  # snoozed_to <= today -> open
```

**Order in `app/worker.py`.** Runs at **12:00 UTC** — after the SAM.gov scan at 11:00, so
the morning's new notices already exist and produce `analyze` actions on the same day's
list. Mail sweep (07:00) and SharePoint resync (08:00) are also long done.

```python
"action-plan-daily": {
    "task": "action_plan.daily",
    "schedule": crontab(hour=12, minute=0),
},
```

**Also runs on demand** so the list never feels stale:
- after `sam_radar.daily_scan` finishes ingesting (chained, org-scoped)
- after the Analyst batch completes
- after a capture run finishes or fails
- from `POST /api/actions/replan`

Each on-demand call takes an optional `organization_id` and does one org only.

**Auto-completion.** A step's *satisfied* predicate (§5) is checked on every run. When it
holds, the open action is closed with `status="done"`, `auto_completed=True`. This is what
makes the list honest: approve capture from the Pipeline and the action disappears — the
rep never marks the same thing done twice.

**Re-planning is the self-correction.** Skip Tuesday's call and Wednesday's run finds
`days_left` one smaller, so `slack` drops and urgency rises, and if the deadline crosses
`lead_days` the action moves to today. No overdue-item bookkeeping required.

---

## 7. API — `backend/routers/actions.py`

Prefix `/api/actions`, registered in `app/server.py` next to `calls.router`.

| Method | Path | Body | Returns |
|---|---|---|---|
| `GET` | `/today` | `?scope=mine\|org` (org = admin only) | `{ overdue: [...], today: [...], upcoming: [...], counts: {...} }` |
| `POST` | `/{id}/done` | — | `{ id, status }` |
| `POST` | `/{id}/snooze` | `{ "days": 1 }` | `{ id, status, snoozed_to }` |
| `POST` | `/{id}/dismiss` | — | `{ id, status }` |
| `POST` | `/replan` | — | `{ queued: true }` |

**Bucketing in `/today`:**
- `overdue` — `status == "open"` and `due_on < today`
- `today` — `status == "open"` and `due_on == today`
- `upcoming` — `status == "open"` and `today < due_on <= today + 7`, **collapsed by day**,
  rendered as a peek strip, not as cards. Today's page must not contain next week's work.

Sort within each bucket: `urgency` (critical → high → normal), then `hard_deadline` asc,
then `priority_score` desc.

Every response row is denormalised enough to render without a second fetch:
`opportunity_title`, `agency`, `hard_deadline`, `priority_score`, `bid_decision`,
`risk_level`, plus `contact_name` for calls.

---

## 8. Frontend

### 8.1 Shell

- `lib/stores/uiStore.ts` — add `"today"` to `ViewKey`; `INITIAL.view = "today"`.
- `app/TopBar.tsx` — prepend `{ key: "today", label: "Today" }`. Dashboard stays.
- `app/page.tsx` — `view === "today"` branch renders `<TodayView />`.
- `lib/queries.ts` — `queryKeys.actions`, `actionsTodayQuery(scope)`. `lib/cache.ts` — an
  `actions` invalidation prefix, invalidated by every mutation in §8.3.

### 8.2 `app/TodayView.tsx`

```
┌─────────────────────────────────────────────────────────────┐
│  Today · Monday, 11 August            [ Mine ▾ ]  [ Replan ] │
│  3 overdue · 11 for today                                    │
├─────────────────────────────────────────────────────────────┤
│  OVERDUE                                                     │
│  ▌ Call Donna Scandaliato at DLA           Closes in 4 days  │
│    Capture is done — the customer call is the next move      │
│    [ Prep the call ]              Done · Snooze · Dismiss    │
│                                                              │
│  TODAY                                                       │
│  ▌ Decide bid / no-bid — GITSS-A            Closes in 9 days │
│    Analyst: P74, High risk, incumbent Stantec named          │
│    [ Bid ] [ No-Bid ]             Snooze · Dismiss           │
│    ...                                                       │
├─────────────────────────────────────────────────────────────┤
│  COMING UP    Tue 2 · Wed 5 · Thu 1 · Fri 3                  │
└─────────────────────────────────────────────────────────────┘
```

Empty state is the reward, and must read as one: **"Nothing left for today."** — not a
blank panel.

### 8.3 `app/ActionCard.tsx`

Each `kind` maps to a **primary button that does the thing inline**, so the rep never has
to go find the record. All of these endpoints already exist:

| `kind` | Primary control | Wires to |
|---|---|---|
| `analyze` | **Analyse now** | `POST /api/opportunities/analyze/selected` |
| `decide` | **Bid** / **No-Bid** | `POST /api/opportunities/{id}/decision` |
| `approve_capture` | **Approve capture** | `POST /api/opportunities/{id}/approve-capture` |
| `retry_capture` | **Retry capture** | same endpoint (it clears the failure) |
| `call` | **Prep the call** | opens `CallBriefDialog` on that contact's tab |
| `review_docs` | **Open documents** | opens the detail sheet on the Documents tab |
| `submit` | **Open in Pipeline** | opens the detail sheet |
| `reply_mail` | **Draft a reply** | opens `MailTriage` on that card |

Secondary row is always `Done · Snooze ▾ (1d / 3d / next week) · Dismiss`.

Urgency is a left stripe + a deadline chip: `critical` red, `high` amber, `normal` neutral.
`infeasible` gets its own treatment and leads with **Dismiss**.

All three status mutations are **optimistic** (`onMutate` removes the card, `onError`
restores it) — the list has to feel like ticking things off, not like waiting on a server.

---

## 9. What this explicitly does not do

- No new agent, no new LLM call, no change to any prompt.
- No change to how opportunities are ingested, analysed, or scored.
- Nothing is deleted from the Pipeline, Dashboard, or Call Plan — every existing view stays
  exactly as it is. This is a new front door, not a replacement for the building.
- No auto-execution. Actions ask; humans act.

---

## 10. What changed during the build

Five things the spec got wrong, found by running the planner against Nexagen's real data.
Recorded here because each was a design error, not a bug.

**1. `decide` had no priority floor — 204 cards.**
Every "Watch" verdict became a daily task. Against 8 live Bids, that is 204 cards asking a
human to overrule the Analyst. But **Watch is the Analyst deferring, not a request for a
ruling**, and `agent_tasks` already schedules a `recheck_opportunity` for each one. Added
`DECIDE_PRIORITY_FLOOR = 55`: 204 → 43. The rest are handled quietly by the recheck.

**2. Urgency saturated — 30 of 54 were "critical".**
Urgency was days of slack (`days_left - lead_days`). `decide` needs 18 days of runway and
most solicitations close inside 30, so nearly everything was behind schedule by that measure.
A word that describes everything describes nothing. Now it is the **fraction of runway left**
(`days_left / lead_days`), which reads the same whether a step needs three weeks or three
days: 7 critical / 18 high / 27 normal.

**3. Infeasible cards were noise on pursuits nobody had committed to.**
49 cards read "this cannot be delivered, drop it" — all of them deferrals that had quietly
run out of runway. Walking away from a live **Bid** is a real decision and still shows; a
Watch that expired is not news. Infeasible + not-a-Bid is now closed silently.

**4. The client and server disagreed about what day it was.**
`closes_in_days` is computed server-side. A rep in IST saw an "Overdue" chip on a card whose
own reason line said "closes today", because their local date had already rolled over past
the UTC day the planner schedules against.

**5. It took 193 seconds.**
Two causes. `list_all` returns whole documents including `document_text` — replaced with
`list_for_planning`, a projection of the dozen fields the ladder reads. And `upsert_action`
was ~0.6 s per call against the remote DB — replaced with one `bulk_write`. 193 s → ~25 s.

Also added, not in the original spec: `mark_capture_reviewed` (without it `review_docs` had
no state field, so the ladder could never advance to `submit`); `closed_action_kinds` (a
human's dismissal has to survive the next replan); and reopening auto-completed actions when
their state regresses, so a second capture failure after a fixed first one is not swallowed.

## 11. Status board

| # | Task | Status |
|---|---|---|
| 1 | `models/action.py` + `crm_store` action methods + indexes | ☑ |
| 2 | `tasks/action_plan_tasks.py` — the ladder + backward scheduler | ☑ |
| 3 | Beat entry + on-demand hooks (scan / analyst / capture) | ☑ |
| 4 | `routers/actions.py` + registration | ☑ |
| 5 | `TodayView` + `ActionCard` + shell wiring | ☑ |
| 6 | Landing-view switch + `lib/queries` / `lib/cache` | ☑ |
| 7 | Run against Nexagen's real data + verification | ☑ |

Verified end-to-end on Nexagen's org: 113 actions planned; a re-run produces no duplicates;
snoozing a card removes it optimistically, persists, and **survives a replan** (the
`user_scheduled` rule); light and dark both render; `tsc --noEmit` clean.

### Known, deliberately left

- **A replan is ~25 s on a 2,500-pursuit org.** It is a background job on a 2-minute debounce,
  so this is not user-facing. Most of what is left is the `$in`-over-every-id close-out
  sweeps; worth revisiting only if an org gets much larger.
- **Nexagen's plan is 102 `approve_capture` cards**, because that is the org's real backlog:
  110 Bids, almost none approved. Honest, and one click each — but if it stays that way, the
  right fix is upstream (fewer, better Bids) rather than hiding them here.
- **`reply_mail` opens the Dashboard**, not the specific triage card. Wiring it to the exact
  card needs the same store hand-off `prepCallFor` uses for calls.
