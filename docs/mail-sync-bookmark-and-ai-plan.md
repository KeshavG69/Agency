# Mail sync: the bookmark + the small-model signature reader

Porting two behaviours from trycompai/crm into our Outlook mail sweep.

1. **The bookmark** — stop re-reading the last 400 messages every day. Keep a high-water
   mark per mailbox and read only what is NEW since last time. No 400 cap, near-real-time,
   no wasted re-reads.
2. **The AI thing** — the model READS the signatures (not regex). Gemma 4
   (`SIGNATURE_MODEL` = `google/gemma-4-26b-a4b-it`, a 26B MoE with ~4B active params, via
   OpenRouter) reads the title/phone/company out of each sender's message. This is the
   trycompai model exactly: let a model read the block, because it handles the free-form
   signatures a pattern never will. One call per sender per sweep, run concurrently, capped
   per sweep. (An earlier draft kept regex as the first pass and used the model only on a
   miss; the decision was model-first, no regex — the model is a better reader and cheap.)

Their design, for reference (from `docs/trycompai-deep-dive/03-backend-plumbing.md`):
- A `MailboxSync` row per (user, source) holds a `cursor` (Gmail `historyId`), `status`,
  `lastSyncedAt`, `retryAfter`. First connect records the cursor and does NOT backfill —
  forward-only. Each tick reads `history.messagesAdded` since the cursor, capped at 120.
- Signature extraction is the AGENT's job (`crm.signature-block` evidence). The sync stores
  the raw body; something smarter reads it later.

Where we differ, and why:
- Composio exposes **no Outlook `delta` tool** (only `OUTLOOK_LIST_MESSAGES` with
  `skip`/`top`/`orderby`). So our "cursor" is a **timestamp high-water mark**, not an opaque
  delta token: page newest-first, stop at the mark. Same effect, works with the tools we have.
- We **do a bounded backfill on the first sweep** (unlike their forward-only `start()`),
  because our value is the recent tail and a new mailbox should light up immediately.
- Regex first, model second — we keep the free path we already built and only reach for the
  model on a miss. Cheaper than their always-agent approach, same end result.

---

## Part A — The bookmark

### A1. New store: `client/mailbox_sync_store.py` → collection `mailbox_sync`

One row per mailbox. Mirrors their `MailboxSync`, trimmed to what we use.

    { _id, owner_email, organization_id,
      high_water: ISO8601 str | None,   # receivedDateTime of the NEWEST message we've processed
      status: "idle" | "running" | "failed",
      backfilled: bool,                 # has the first bounded backfill run?
      last_swept_at, first_synced_at, updated_at,
      last_error: str | None }

Unique index on `(owner_email, organization_id)`. Methods:
- `get(owner, org) -> row | None`
- `mark_running(owner, org)`
- `commit(owner, org, high_water, backfilled=True)` — set high_water (only ever moves
  forward: `max(old, new)`), status idle, stamp times.
- `mark_failed(owner, org, err)`

### A2. `fetch_recent_messages` gains a `since` bound

Add `since: str | None = None`. When set, stop paging as soon as a page's messages are
`receivedDateTime <= since` (they arrive desc, so the first one that old means done). Drop
any straggler `<= since` from the returned list. `limit` stays as the hard safety cap AND as
the first-sweep backfill size.

Termination becomes: `len(raw) >= limit` OR `no nextLink` OR **hit the bookmark**.

### A3. `update_correspondence` gains an accumulate mode  ← the load-bearing change

    def update_correspondence(..., mode="overwrite"):
        # overwrite (backfill / today's behaviour): SET p.corr_count = row.corr_count
        # accumulate (incremental):                SET p.corr_count = coalesce(p.corr_count,0) + row.delta

`last_contact` is always `greatest(existing, incoming)` — a max, never a blind overwrite, so
an out-of-order or overlapping sweep can't move it backwards.

WHY: an incremental sweep only counts the NEW messages. Overwriting would set the count to
just the delta and erase the history. Accumulate turns `corr_count` into a true lifetime
total, which is strictly more correct than the "last 400 window" number it replaces.

Idempotency guard: because accumulate is not naturally idempotent, the bookmark comparison
is strict (`received > high_water`), and `high_water` only moves forward. A message is
counted exactly once: the sweep that first sees it moves the mark past it.

### A4. Sweep task rewrite (`tasks/mail_sweep_tasks.py`)

    state = get_mailbox_sync_store()
    row = state.get(owner, org)
    since = row["high_water"] if (row and row.get("backfilled")) else None
    mode  = "accumulate" if since else "overwrite"
    state.mark_running(owner, org)
    messages = fetch_recent_messages(owner, limit=BACKFILL_LIMIT if since is None else INCREMENTAL_CAP, since=since)
    ... extract correspondence + signatures (unchanged) ...
    update_correspondence(owner, org, counts, last_seen, mode=mode)
    facts.record_bulk(org, sig_claims)
    newest = max(received for all messages) or since
    state.commit(owner, org, high_water=newest, backfilled=True)

- First sweep: `since=None` → bounded backfill (`BACKFILL_LIMIT`, default 400), overwrite.
- Every later sweep: `since=high_water` → only new mail, accumulate. Usually a handful of
  messages, finishes in seconds.
- `mail_sweep.daily` can now run more often safely (nothing new = one cheap list call that
  stops at the bookmark immediately).

---

## Part B — The model reads the signatures (model-first, no regex)

### B1. New util: `utils/signature_llm.py`

    def extract_signature_llm(body, sender_email, is_html=None) -> LLMSignature | None

One OpenRouter chat call, `SIGNATURE_MODEL` (Gemma 4), `response_format=json_object`,
`temperature=0`, following the `utils/excel_ingest.py` pattern. Returns `{title, phone,
company}` + derived seniority/function, or None. Body is cleaned + quote-stripped (reuse
`signature.strip_quoted` / `html_to_text`), hard-capped to ~2k chars so a giant thread can't
run up cost. Machine senders and a missing API key short-circuit to None; any transport error
is swallowed to None (a fallback that fails is just "no fact this time").

Prompt: "the tail of an email FROM {sender}. If it contains the sender's OWN signature block,
return their title/phone/employer. If there is no clear signature, return nulls — do not
guess from the domain." Strict JSON.

### B2. Wire it as the FIRST (and only) signature reader in the sweep

Two passes: pass 1 builds correspondence and picks the first inbound message per sender;
pass 2 reads those signatures with the model, CONCURRENTLY (`ThreadPoolExecutor`, ≤8), capped
at `LLM_SIG_BUDGET` (default 120) so a first backfill cannot fan out unbounded. One call per
sender per sweep; incremental sweeps see only a few new senders. A sender skipped by the cap
is simply read on a later sweep.

### B3. New evidence kind

`llm.signature-extraction` in `models/evidence.py`. PRIMARY, weight **0.80** — the same
standing as the regex `outlook.signature-block` it replaces: it is the person's own signature
on their own mail. A lone read is a suggestion (PROBABLE); a reply from that address
corroborates it into a VERIFIED fact via noisy-OR. The model is told to return nulls rather
than guess and runs at temperature 0, which is what keeps a primary weight honest.

---

## Tests
- `test_mailbox_sync_store.py` — get/commit/high-water-moves-forward-only/failed.
- Extend `test_mail_sweep.py` — first sweep backfills + sets mark; second sweep with `since`
  reads only the new message and ACCUMULATES corr_count (2 then +1 = 3, not overwritten to 1);
  regex-miss triggers the (stubbed) LLM fallback and yields a suggestion.
- Full regression: evidence, signature, facts_store, company_evidence, task_store, wiring,
  mail_sweep, intelligence_api, agent_loop, mailbox_sync.

## Rollout / safety
- Backwards compatible: no `mailbox_sync` row → behaves like today (bounded backfill), then
  self-upgrades to incremental on the next run.
- `EXTRACTION_MODEL` and OpenRouter are already configured; no new secrets.
- Cost ceiling is explicit (`LLM_SIG_BUDGET`), logged when hit — no silent fan-out.
