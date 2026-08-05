# Enrichment & Agent-Queue Implementation Plan

**Status:** in progress · **Started:** 2026-08-05 · **Branch:** `keshav`

> **How to use this doc.** This is the working spec for the enrichment + agent-queue
> work. It is written to be self-contained: if context is lost, read this file top to
> bottom and you can continue without re-deriving anything. Update the **Status board**
> as items land. Background research lives in `docs/trycompai-deep-dive/`.

---

## 1. Why we're doing this

Collecct's enrichment pipeline works, but it has **three dead ends** — places where the
system notices something and then forgets it forever:

| Dead end | Where | Evidence |
|---|---|---|
| "Someone should research this company" | `utils/company_enrich.py:185` sets `company_needs_research=True`; `client/graph_store.py:160` stores it | **Nothing reads it.** Verified by grep. |
| "Let's watch this opportunity" | `tasks/analyst_tasks.py:51` creates a `Revisit:` card | Nothing ever re-analyses it. |
| A guess and a fact look identical | `utils/company_enrich.py:167` (dataset hit) vs `:180` (domain guess) | Both write to the same `company` field. Downstream cannot tell them apart. |

Root cause: **there are only two ways work can start** — the daily clock (SAM.gov 11:00,
SharePoint 08:00) and a human clicking a button. There is no "come back to this later."

This work adds:
1. **An evidence model** — every fact records *where it came from*; code (not the LLM) decides if it's a fact or a suggestion.
2. **A durable task queue** — a third way for work to start: a note with a due date.
3. **Free person data** — job titles/phones parsed from email signature blocks.
4. **One new agent** — researches companies the free dataset can't resolve.

Patterns are ported from `github.com/trycompai/crm` (full teardown in
`docs/trycompai-deep-dive/`). Their verbatim source is in `docs/trycompai-deep-dive/reference/`.

---

## 2. Decisions locked (do not re-litigate)

| Decision | Rationale |
|---|---|
| **Keep Collecct as the base.** Do not rebuild on trycompai/crm. | Their repo has no SAM.gov, no SharePoint, no Outlook, no multi-tenancy, and is TypeScript. We'd throw away ~80% of the product. |
| **No cross-organisation data sharing.** Facts are scoped per org. | Users are competing govcon primes. A fact derived from Org A's mailbox leaking to Org B is both a privacy breach and competitive-intel leakage. CUI/FOUO makes it worse. **User decision, 2026-08-05.** |
| **No relevance gate — sweep every contact.** | User explicitly declined the "only research contacts on live bids" filter. We sweep everyone, like trycompai does. Cost is controlled by stand-downs + budgets + once-only instead. **User decision.** |
| **The global `companies` collection stays global.** | It's public/paid data (PDL dataset), not mailbox-derived, so sharing it across orgs is safe. Newly-researched companies may be written back to it **only** when sourced from public web research. |
| **The agent is the last resort, not the engine.** | Exactly one LLM step in the whole pipeline (company research). Everything else is plain Python. |
| **UI work is deferred.** | Backend first. `agent_events` is captured now so the UI has history to read later. |
| **Don't copy their `eve` framework or sandbox model.** | Celery already gives durability + retries. Our tools are curated (no raw shell), so there's no egress surface to lock down. |

---

## 3. Existing architecture (the map)

### Agents
| Agent | File | Job |
|---|---|---|
| Analyst | `agent/analyst_agent.py` | One opportunity → Bid/Watch/No-Bid + priority + rationale + call action |
| Relation (CRM) | `agent/crm_agent.py` | Search FalkorDB graph → ranked contacts for an opportunity |
| Capture | `agent/capture_agent.py` | Capture strategy + deliverable documents |
| Mail | `agent/mail_agent.py` | Outreach drafts + triage replies |
| *(helper)* | `agent/company_profile.py` | Org profile from UEI → SAM.gov, Redis-cached |

### How work starts today
| Trigger | Entry point |
|---|---|
| Clock 11:00 UTC | `sam_radar.daily_scan` → bulk CSV once → per-org NAICS filter → upsert → `run_analyst_batch` |
| Clock 08:00 | `resync.daily` (SharePoint structure) |
| Human: Pull from SAM | `scan_org_sam` (`analyze=False` — human picks) |
| Human: Analyze | `run_analyst_batch` / `analyze_opportunity_task` |
| Human: Bid decision | `provision_bid_folders_task` |
| Human: Approve capture | `group(capture_task, recommend_contacts_task)` — parallel |
| Human: Draft outreach | `draft_outreach_task` (per contact, cap 15) |
| Human: Connect Outlook | `sync_outlook_contacts_task` / `ingest_selected_contacts_task` |
| Webhook: inbound mail | `process_outlook_message_task` → triage card |
| Human: Draft reply | `draft_triage_reply_task` |

### Storage
- **MongoDB** — system of record (opportunities, documents, users, orgs, `companies` PDL dataset)
- **FalkorDB** — contact graph, one graph per org (`collecct_network_<org_id>`); nodes carry `owner_email`; dedup key `(email, owner_email)`; re-sync prunes only that owner's slice
- **Redis** — Celery broker (db 0) / result backend (db 1)

### Codebase facts worth remembering
- `utils/db_indexes.py` has a central idempotent `ensure_indexes()` called on startup — **new indexes go there**.
- Beat schedule lives in `app/worker.py` → `celery_app.conf.beat_schedule`.
- `client/graph_store.py::_contact_text` (line ~45) embeds `name/title/seniority/department/company/industry/domain/skills`. **Filling `title`/`industry` improves hybrid search — always re-embed after enrichment.**
- `utils/composio_utils.py::fetch_outlook_network` reads **only the address book**, and hardcodes `count: 0, last_seen: None`. ⚠️ **`corr_count` is always zero**, yet `crm_agent.py` instructs the LLM to rank on it.
- `fetch_outlook_contacts` already selects `["displayName","emailAddresses","companyName","jobTitle"]` — so a (sparse) job title is already free.
- `fetch_outlook_message` selects only `bodyPreview`, truncated to 280 chars → **top of message only, signatures are at the bottom**.
- Analyst worker runs `--pool=threads --concurrency=15`.

---

## 4. Status board

| # | Item | Type | Status |
|---|---|---|---|
| 1 | `models/evidence.py` — kinds, weights, scorer | no-AI | ✅ **done** — verified by `scripts/test_evidence.py` (20/20) |
| 2 | `utils/signature.py` — signature → title/phone/seniority | no-AI | ✅ **done** — verified by `scripts/test_signature.py` (38/38) |
| 3 | `client/facts_store.py` — `contact_facts` + write-path | no-AI | ✅ **done** — verified by `scripts/test_facts_store.py` (25/25, real Mongo, self-cleaning) |
| 4 | `company_enrich.py` — emit evidence | edit | ✅ **done** — verified by `scripts/test_company_evidence.py` (17/17) |
| 5 | `contacts_tasks.py` — record facts + enqueue research | edit | ✅ **done** — `_record_contact_facts` + `_queue_company_research` in both ingest tasks |
| 6 | `client/task_store.py` — `agent_tasks` + leasing | no-AI | ✅ **done** — verified by `scripts/test_task_store.py` (26/26, incl. a 2-thread race) |
| 7 | `tasks/agent_tasks.py` — beat tick + fan-out | no-AI | ✅ **done** — beat entry `agent-task-tick` every minute; two lanes; handlers for `research_company` + `recheck_opportunity` |
| 8 | `agent/company_research_agent.py` + task | **AI** | ✅ **done** — verified by `scripts/test_agent_loop.py` (24/24, LLM stubbed, ~65s: real embeddings) |
| 9 | Mail-triage signature hook | edit | ✅ **done** — `body` added to the Graph select (no extra API call); `_record_signature_facts` runs before the keep/drop decision |
| 10 | Mail sweep (signatures + `corr_count`) | no-AI | ✅ **done** — `OUTLOOK_LIST_MESSAGES` + the existing `_paginate`; beat `mail-sweep-daily` 07:00; verified by `scripts/test_mail_sweep.py` (13/13) |
| 11 | Analyst recheck fields + enqueue | edit | ✅ **done** — `recheck_after_days`/`recheck_reason` on the verdict, prompt updated, `_schedule_recheck` enqueues |
| 12 | `agent_events` audit log | no-AI | ✅ **done** — `client/events_store.py`; emitted from analyst, relation, company research |
| 13 | Agent rules → versioned markdown | edit | ✅ **done** — 5 Agno skills in `agent/bd_skills/`, shared loader `agent/skills_registry.py`, attached to Analyst + Relation + Research. Inline rules kept (de-dup is a follow-up) |

---

## 5. Specifications

### 5.1 `backend/models/evidence.py`

Pure functions, no I/O. The LLM **never** emits a score — it names a `kind` from the enum;
this module prices it.

**Evidence kinds and weights** (`primary` = can carry a fact alone):

| Kind | Weight | Primary | Meaning |
|---|---|---|---|
| `samgov.entity-record` | 0.90 | ✓ | Company's own SAM.gov registration |
| `sam.poc-listed` | 0.90 | ✓ | Named as POC on a SAM.gov notice |
| `outlook.thread-reply` | 0.85 | ✓ | They replied from that address on a thread we hold |
| `gov-domain-rule` | 0.85 | ✓ | `.gov`/`.mil` → agency via the deterministic `_AGENCY_NAMES` table |
| `outlook.signature-block` | 0.80 | ✓ | Their own email signature states it |
| `pdl.domain-company` | 0.80 | ✓ | Domain matched the PDL company dataset |
| `sharepoint.authored-doc` | 0.75 | ✓ | Authored/owns a document in our tenant |
| `outlook.meeting-attend` | 0.70 | ✓ | Accepted a calendar invite we hold |
| `company.own-website` | 0.85 | ✓ | The company's own site describes its business — the corporate equivalent of a signature block. **Decided in code** (`_is_own_site` compares hosts), never by the model |
| `web.cited-claim` | 0.40 | ✗ | A *third-party* page states it — **requires `source_url`** |
| `outlook.address-book` | 0.35 | ✗ | `jobTitle` saved in the user's Outlook contacts |
| `handle.name-form` | 0.35 | ✗ | Local-part is a construction of their name |
| `domain-derived-name` | 0.30 | ✗ | Company name guessed from the domain ← today's silent guess |
| `employer-only` | 0.20 | ✗ | Employer matches, name does not |
| `contradiction` | 0.00 | ✗ | Sources disagree — **holds** the fact |

**Scoring — noisy-OR:**
```
remaining = Π (1 - weight_i)
score     = min(0.99, 1 - remaining)
if any contradiction: score = min(score, 0.45)
```

**Bands:**
| Band | Floor | Extra condition | Result |
|---|---|---|---|
| `VERIFIED` | 0.85 | **must have ≥1 primary** | written to the record |
| `PROBABLE` | 0.55 | — | suggestion |
| `POSSIBLE` | 0.30 | — | suggestion |
| `None` | <0.30 | — | dropped, not stored |

**Rules that must hold (test these):**
1. Signature alone (0.80, primary) → 0.80 → `PROBABLE` → suggestion, **not** written.
2. Signature + thread-reply → `1-(0.2×0.15)` = 0.97 → `VERIFIED` → written.
3. Three `web.cited-claim` (0.40) → 0.784 → `PROBABLE` but **no primary** → can *never* reach VERIFIED.
4. Any `contradiction` → capped at 0.45 → `POSSIBLE`, rationale says sources disagree.
5. Empty evidence → score 0, band `None`.
6. Two observations on the **same source** count as **one** entry (caller's responsibility; document it).

**API:**
```python
EvidenceKind = Literal[...]           # the 14 above
WEIGHTS: dict[EvidenceKind, Weighting]        # .weight, .primary, .label
class Evidence(TypedDict): kind, detail, source_url(optional)
@dataclass Scored: score, band, has_primary, rationale
def score_evidence(evidence: Sequence[Evidence]) -> Scored
def band_for(score: float, has_primary: bool) -> Band | None
```
`rationale` must be human-readable (shown in a tooltip), built from the `label` strings.

---

### 5.2 `backend/utils/signature.py`

Plain regex. No AI. No network.

```python
@dataclass
class SignatureFacts:
    name: str | None
    title: str | None
    phone: str | None
    seniority: str | None      # derived from title
    function: str | None       # derived from title

def extract_signature(body: str, sender_email: str) -> SignatureFacts | None
```

**Method:**
1. `_strip_quoted(body)` — cut at `-----Original Message-----`, `From:` header blocks,
   `On <date> ... wrote:`, and lines starting with `>`. HTML → text first if needed.
2. Take the last ~12 non-empty lines of the remaining top-post.
3. Find a title line via the keyword tables; find a phone via regex.
4. Sanity-check the name against the sender's local part where possible.

**Seniority table** (first match wins, ordered):
`chief|c[teoif]o|president|founder|partner` → `C-level` ·
`svp|senior vice president` → `SVP` · `vp|vice president` → `VP` ·
`director` → `Director` · `manager|lead|principal|head of` → `Manager` · else `IC`

**Function table:**
`business development|capture|\bbd\b|growth` → `BD & Capture` ·
`contract|procurement|acquisition|contracting officer|\bko\b|\bcor\b` → `Contracts` ·
`program|project|\bpmo\b` → `Program` ·
`engineer|architect|developer|scientist|technical` → `Technical` · else `Other`

Emitted as evidence kind `outlook.signature-block` (0.80, primary).
`detail` must read like: `their signature on 14 Jul reads "VP, Business Development"`.

---

### 5.3 `backend/client/facts_store.py` — `contact_facts`

**Mongo document:**
```python
{
  "organization_id": str,     # ALWAYS scoped. never cross-org.
  "email": str,               # lowercased
  "field": str,               # title|company|industry|phone|seniority|function
  "value": str,
  "score": float, "band": str,
  "evidence": [{"kind","detail","source_url"}],
  "status": "APPLIED"|"PROPOSED"|"DISMISSED"|"SUPERSEDED",
  "decided_by": str|None,     # user email, set when a human accepts/dismisses
  "created_at", "updated_at",
}
```
**Indexes** (add to `ensure_indexes()`):
- unique `(organization_id, email, field, value)`
- `(organization_id, email, status)`

**Write path — three invariants enforced in code, not prompts:**
```python
def record_fact(org_id, email, field, value, evidence) -> FactOutcome:
    1. if a DISMISSED row exists for this (org,email,field,value) → skip "dismissed by a human"
    2. scored = score_evidence(evidence); if scored.band is None → skip "too weak"
    3. current = applied row for (org,email,field)
       if current.decided_by and current.value != value → skip "a human owns this field"
    4. if band == VERIFIED → supersede prior APPLIED, upsert APPLIED
       else                → upsert PROPOSED
```
Plus `decide_fact(org_id, email, fact_id, accept: bool, user_email)` — the **only**
router-owned mutation. Accept → APPLIED + `decided_by`. Dismiss → DISMISSED (never re-offered).

---

### 5.4 `company_enrich.py` — emit evidence *(edit)*

Keep the existing dict shape (`graph_store` depends on it); **add** an `evidence` key:

| Branch | Today | Add |
|---|---|---|
| gov domain (`is_gov_domain`) | derives agency name | `gov-domain-rule` |
| dataset hit (`rec`) | company from PDL | `pdl.domain-company` |
| unknown work domain | `company_name_from_domain` + `needs_research` | `domain-derived-name` |
| personal / no domain | nothing | *(no evidence)* |

---

### 5.5 `contacts_tasks.py` — wire it up *(edit)*

In both `sync_outlook_contacts_task` and `ingest_selected_contacts_task`, after
`enrich_contacts_company(...)`:
```python
record_facts_for_contacts(org, enriched)      # plain loop → record_fact
...existing clear_owner_graph + upsert_contacts...
enqueue_company_research(org, {c["domain"] for c in enriched
                               if c.get("company_needs_research")})
```
`record_facts_for_contacts` is a **plain for-loop**, not an agent. Runs inline, milliseconds.

---

### 5.6 `backend/client/task_store.py` — `agent_tasks`

**Mongo document:**
```python
{
  "organization_id": str,
  "kind": str,                # research_company | recheck_opportunity | parse_signatures | meeting_prep
  "subject": {"type": "company"|"contact"|"opportunity", "id": str},   # domain / email / opp id
  "reason": str,              # human-readable, shown in the UI later
  "priority": int, "budget": int,
  "due_at": dt, "lease_until": dt|None, "attempts": int,
  "finished_at": dt|None, "outcome": str|None,
  "created_at": dt,
}
```
**Indexes:** `(organization_id, kind, subject.id, finished_at)` for de-dup ·
`(finished_at, due_at, lease_until, priority)` for the claim query.

**Constants:**
```python
MAX_ATTEMPTS = 3
PRIORITY = {"requested":300, "meeting":200, "signature":150,
            "identify":100, "sweep":50, "recheck":0}
LLM_KINDS    = ["research_company", "recheck_opportunity"]
DIRECT_KINDS = ["parse_signatures"]
LEASE_LLM_MS = 30*60_000 ; BATCH_LLM = 12
LEASE_DIR_MS =  2*60_000 ; BATCH_DIR = 60
STAND_DOWN_DAYS = 30
```

**`claim_due` — Mongo's `FOR UPDATE SKIP LOCKED` analogue.**
`find_one_and_update` is atomic per document, so two workers can never claim the same row:
```python
doc = coll.find_one_and_update(
    {"finished_at": None, "due_at": {"$lte": now},
     "attempts": {"$lt": MAX_ATTEMPTS}, "kind": {"$in": kinds},
     "$or": [{"lease_until": None}, {"lease_until": {"$lt": now}}]},
    {"$set": {"lease_until": until}, "$inc": {"attempts": 1}},
    sort=[("priority", -1), ("due_at", 1)],
    return_document=ReturnDocument.AFTER)
```
Loop up to `limit` times. Also:
- `enqueue(...)` — **de-dup**: skip if an unfinished task with the same `(org, kind, subject.id)`
  exists, **or** a finished one within `STAND_DOWN_DAYS`.
- `complete_task(id, outcome)` · `stand_down(id, days, why)` · `retire_exhausted()`
  (attempts ≥ MAX and lease expired → finish with "gave up after 3 attempts").

---

### 5.7 `backend/tasks/agent_tasks.py` — the tick

Beat entry in `app/worker.py`:
```python
"agent-task-tick": {"task": "agent_tasks.tick", "schedule": crontab(minute="*")},
```
Two lanes, mechanical never queues behind LLM work:
```python
@celery_app.task(name="agent_tasks.tick")
def tick():
    retire_exhausted()
    for t in claim_due(BATCH_DIR, DIRECT_KINDS, LEASE_DIR_MS): dispatch_direct.delay(t)
    for t in claim_due(BATCH_LLM, LLM_KINDS,    LEASE_LLM_MS): dispatch_llm.delay(t)
```
Remember to add `import tasks.agent_tasks` to the explicit task-import list in `worker.py`.

---

### 5.8 `agent/company_research_agent.py` + task — **the one AI step**

Mirror the existing builder idiom (`build_analyst_agent`): `Agent(name=..., model=get_chat_llm_agno(...), tools=[create_exa_web_search_tool()], instructions=...)` then `coerce_output`.

```python
class CompanyResearch(BaseModel):
    found: bool
    industry: str | None
    description: str | None    # 1-2 lines, what they actually do
    website: str | None
    source_url: str | None     # REQUIRED when found=True
```
Instructions must enforce: never invent; cite a URL; if you can't find it, return `found=false`
(a miss must stay a miss). Budget ≈ 3 searches.

Task `research_company_task(organization_id, domain)`:
1. run agent → if `not found` → `stand_down(30d, "nothing found")`
2. `record_fact(...)` for industry/description with `web.cited-claim` + `source_url`
3. `graph_store.update_company_for_domain(org, domain, {...})` — clears `company_needs_research`
4. **re-embed** affected contacts (industry feeds `_contact_text`)
5. optionally write back into the global `companies` collection marked `source="researched"`

---

### 5.9 Mail-triage signature hook *(edit)*

In `utils/composio_utils.py::fetch_outlook_message`, add `"body"` to the `select` list.
In `tasks/mail_triage_tasks.py::process_outlook_message_task`, after fetching:
```python
sig = extract_signature(msg["body"], msg["sender_email"])
if sig and sig.title:
    record_fact(org, sender, "title", sig.title, [{"kind":"outlook.signature-block", ...}])
record_fact(org, sender, "_identity", "confirmed", [{"kind":"outlook.thread-reply", ...}])
```
**Costs zero extra API calls** — the message is already being downloaded.
Keep it non-fatal: wrap in try/except so triage never breaks on a parse error.

---

### 5.10 Mail sweep *(new, no AI)*

One paginated pass over recent messages per connected mailbox (daily, alongside `resync.daily`).
Yields three things in one pass:
- signature blocks → titles/phones
- correspondent counts → **`corr_count` / `last_seen`, which are currently hardcoded to 0**
- who replied to us → `outlook.thread-reply` (primary, 0.85)

⚠️ This is higher value than first estimated: `crm_agent.py` already tells the LLM to rank on
`corr_count`, so today it is ranking on a constant zero.

---

### 5.11 Analyst recheck *(edit)*

`models/verdict.py`:
```python
recheck_after_days: Optional[int] = Field(None, ge=1, le=365)
recheck_reason: Optional[str] = None
```
Update `_instructions` so a `Watch` verdict must supply both.
`tasks/analyst_tasks.py` — after `crm.apply_verdict`, replace the passive card (line 51):
```python
if verdict.recheck_after_days:
    enqueue(org, kind="recheck_opportunity",
            subject={"type":"opportunity","id":opp["id"]},
            due_at=now + timedelta(days=verdict.recheck_after_days),
            reason=verdict.recheck_reason, priority=PRIORITY["recheck"], budget=4)
```

---

### 5.12 `agent_events` audit log

Append-only. Backend now; UI reads it later.
```python
{"organization_id", "subject":{"type","id"}, "agent", "step", "detail",
 "tool": str|None, "ok": bool, "created_at"}
```
Index `(organization_id, subject.id, created_at)`. Emit from: `analyze_opportunity_task`
(the verdict + rationale), `recommend_contacts_task` (why each contact was/wasn't picked),
`research_company_task`. A `No-Bid` should render later as a warning-style step **with its reason**.

---

### 5.13 Agent rules → versioned markdown (Agno skills)

**Agno 2.6.16 supports skills natively** — `Agent(skills=Skills(loaders=[LocalSkills(dir)]))`.
The repo already used this for the Capture agent (`agent/skills/` = pdf/docx/pptx).

Five BD reasoning skills now live in **`backend/agent/bd_skills/`**, deliberately a
SEPARATE directory: `LocalSkills` loads a whole directory, and the Capture agent has no
use for identity matching (nor this one for writing .pptx files).

| Skill | Covers |
|---|---|
| `grounding` | Never write a fact you have not read. "Could not verify" ≠ "absent". Never infer from a name. |
| `evidence` | The kinds table, one-entry-per-independent-source, writing `detail` for a human. |
| `company-research` | Where to look (own site → SAM.gov → LinkedIn), what matters for teaming, the name trap. |
| `identity-matching` | *"Guess where to look, never guess what you will find."* Fail closed on two checks. |
| `writing-a-brief` | Facts then read; no adjectives about people; the say-it-to-their-face test; when to write nothing. |

**Progressive disclosure:** only name + description enter the system prompt (~3.1 KB for
all five); the agent calls `get_skill_instructions(name)` to pull the full text when it
decides a skill applies. So carrying extra skills costs about a line each, and the
guidance is versioned markdown editable without a deploy.

Loader: `agent/company_research_agent.py::get_bd_skills()` (`@lru_cache`, mirrors
`get_capture_skills()`).

**Remaining:** attach to Analyst / Relation / Mail and strip their duplicated inline
rules. Deliberately not done in the same pass — those are working production prompts and
adding skills changes their tool surface, so it wants its own change and its own check
against `scripts/test_analyst.py`.

Reference copies of trycompai's originals: `docs/trycompai-deep-dive/reference/skill-*.md`.

---

## 6. Build order

Leaf modules first (nothing depends on them → safe to land and test in isolation):

```
1. models/evidence.py          ← pure maths, testable alone
2. utils/signature.py          ← pure regex, testable alone
3. client/facts_store.py       ← needs (1)
4. company_enrich.py edit      ← needs (1)
5. contacts_tasks.py edit      ← needs (3)(4)
6. client/task_store.py        ← independent
7. tasks/agent_tasks.py        ← needs (6)
8. company_research_agent      ← needs (3)(6)(7)   ⚡ first AI step
9. mail-triage hook            ← needs (2)(3)
10. mail sweep                 ← needs (2)(3)
11. analyst recheck            ← needs (6)
12. agent_events
13. skills → markdown
```

**Milestone A** = items 1–5: guesses stop posing as facts (visible win, no new infra).
**Milestone B** = items 6–8: the dead flag finally leads somewhere.
**Milestone C** = items 9–13: self-scheduling + transparency.

---

## 7. Multi-tenancy rules (non-negotiable)

trycompai is **single-tenant by design** — their `WORKSPACE_ID` is a constant and their
`read_crm_history(contactId)` takes no org parameter. **We must invert every one of those.**

- `organization_id` on **every** new document: `contact_facts`, `agent_tasks`, `agent_events`.
- Every query filters by it. Every task carries it. No exceptions.
- A task running for org A must never read org B's Outlook, SharePoint, graph, or facts.
- The graph is already isolated two ways (per-org graph name + per-owner nodes) — preserve that.
- **Facts are org-level** (`(organization_id, email)`), *not* per-owner: a job title is the same
  truth for everyone in the org, so research once and all employees benefit.
  **Relationship signals** (`corr_count`, `last_seen`) stay **per-owner** in the graph.

---

## 8. Open questions

1. **`outlook.address-book` weight (0.35).** A title the user typed into their own Outlook is
   arguably stronger than a web claim. Currently supporting-only. Revisit after seeing real data.
2. ~~**Which Composio action lists recent messages**~~ — **resolved.** `OUTLOOK_LIST_MESSAGES`
   was already in the toolkit, and `_paginate` already follows `@odata.nextLink`. Implemented as
   `fetch_recent_messages(user_id, limit=400)`.
3. **Backfill.** Do we run the sweep/scoring over already-imported contacts, or only new ones?
   Leaning: a one-off backfill task, rate-limited, after Milestone B is stable.
4. ~~**HTML email bodies**~~ — **resolved.** `utils/signature.py` auto-detects HTML and
   converts with a stdlib `html.parser` subclass (no new dependency; `bs4`/`lxml` are not
   in `pyproject.toml`). Handles `<br>/<p>/<div>/<tr>` as breaks and drops `<style>/<script>`.
   Caller may still pass `is_html=` explicitly from Graph's `body.contentType`.
5. **Global `companies` write-back** (§5.8 step 5) — safe (public web data) but confirm we tag
   `source="researched"` so a PDL refresh doesn't silently overwrite, and vice-versa.

---

## 9. Verification

- `models/evidence.py` — unit tests for the six rules in §5.1.
- `utils/signature.py` — unit tests over a fixture set of real-shaped signature blocks
  (incl. quoted replies, HTML, no-signature).
- `client/task_store.py` — a concurrency test proving two `claim_due` callers get disjoint rows.
- End-to-end smoke: import a contact on an unknown domain → confirm a `POSSIBLE` company
  suggestion, an `agent_tasks` row, then after a tick → a `web.cited-claim` fact + flag cleared.
- Nothing here touches the Analyst/Capture/Mail agents' behaviour — regression risk is low, but
  re-run `scripts/test_analyst.py` after item 11.
