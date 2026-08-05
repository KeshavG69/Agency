"""The mail sweep — an incremental pass over a mailbox that buys three things at once.

INCREMENTAL, via a per-mailbox bookmark (mailbox_sync). The first run backfills the recent
tail; every run after reads only what arrived since, so there is no fixed message cap and no
re-reading. What each run extracts:

  1. SIGNATURES -> job titles, phone numbers, seniority, function. Read by a small model
     (Gemma via SIGNATURE_MODEL), not regex: it handles the free-form blocks a pattern never
     will. This is what trycompai/crm does — let a model read the block — done as a FALLBACK-
     free first pass here, one model call per sender per sweep, run concurrently and capped by
     LLM_SIG_BUDGET so a backfill can't fan out. A lone read is a suggestion; a reply from
     that address corroborates it into a fact.
  2. CORRESPONDENCE COUNTS -> `corr_count` / `last_contact` on the graph. These are
     otherwise hardcoded to 0 by `fetch_outlook_network` (it reads only the address book),
     while `crm_agent.py` instructs the model to rank contacts on `corr_count`. On an
     incremental sweep the count ACCUMULATES (see graph_store.update_correspondence): each
     run adds the new messages to a running lifetime total, never overwrites it.
  3. WHO REPLIED TO US -> primary identity evidence. Combined with a dataset match on the
     same employer, that is what promotes a company from a suggestion to a fact.

See docs/mail-sync-bookmark-and-ai-plan.md and docs/enrichment-implementation-plan.md §5.10.
"""
import logging
from collections import defaultdict

from app.worker import celery_app

logger = logging.getLogger(__name__)

# How far back the FIRST sweep of a mailbox reaches. After that a bookmark takes over and
# each sweep reads only new mail, so this only bounds the one-time backfill.
BACKFILL_LIMIT = 400
# Safety cap for an incremental sweep: normally it stops at the bookmark long before this,
# but a mailbox that received a flood since the last run should not read unbounded.
INCREMENTAL_CAP = 600
# Ceiling on model signature reads PER SWEEP. Signatures are read by the model (one call per
# sender), so this bounds a first backfill; incremental sweeps see only a handful of new
# senders and never approach it. The reads run concurrently and the model is small and cheap,
# so this can be raised freely — a sender skipped this run is simply read on the next.
LLM_SIG_BUDGET = 120


@celery_app.task(bind=True, name="mail_sweep.for_employee", max_retries=2, default_retry_delay=60)
def sweep_mailbox_task(self, employee_email: str, organization_id: str, limit: int | None = None) -> dict:
    """Sweep ONE employee's mail. Owner-scoped: correspondence signals belong to the employee
    whose mailbox they came from, while the facts they yield are org-level.

    Incremental by default: a per-mailbox bookmark (mailbox_sync) means each run reads only
    what arrived since the last one. The first run (no bookmark) does a bounded backfill and
    sets the mark. `limit` overrides the backfill size for a manual deep pass.
    """
    from concurrent.futures import ThreadPoolExecutor

    from client.facts_store import get_facts_store
    from client.graph_store import update_correspondence
    from client.mailbox_sync_store import get_mailbox_sync_store
    from utils.composio_utils import fetch_recent_messages
    from utils.signature_llm import extract_signature_llm

    owner = (employee_email or "").strip().lower()
    org = (organization_id or "").strip()
    if not owner or not org:
        return {"swept": 0, "reason": "missing owner or organization"}

    # The bookmark decides everything: where to read from, how far, and whether the
    # correspondence write ADDS to the running total (incremental) or SETS it (backfill).
    sync = get_mailbox_sync_store()
    state = sync.get(owner, org)
    incremental = bool(state and state.get("backfilled") and state.get("high_water"))
    since = state.get("high_water") if incremental else None
    read_limit = limit if limit else (INCREMENTAL_CAP if incremental else BACKFILL_LIMIT)
    mode = "accumulate" if incremental else "overwrite"

    sync.mark_running(owner, org)
    try:
        messages = fetch_recent_messages(owner, limit=read_limit, since=since)
    except Exception as exc:  # transient Composio/Graph errors -> retry
        logger.warning("Mail sweep: fetch failed for %s: %s", owner, exc)
        sync.mark_failed(owner, org, str(exc))
        raise self.retry(exc=exc)

    facts = get_facts_store()
    counts: dict[str, int] = defaultdict(int)
    last_seen: dict[str, str] = {}
    newest = since or ""
    own_domain = owner.split("@", 1)[1] if "@" in owner else ""

    # Pass 1: the correspondence signal, and pick the FIRST inbound message per sender as the
    # one whose signature we will read. Signatures are read by the model, not regex — it
    # handles the free-form blocks a pattern never will (this is what trycompai/crm does). We
    # read one message per sender per sweep, so a busy thread is one model call, not fifty.
    first_inbound: dict[str, dict] = {}
    replies = 0
    for msg in messages:
        sender = msg.get("sender_email") or ""
        received = (msg.get("received_at") or "")
        if received > newest:
            newest = received  # advance the bookmark to the newest message actually seen

        parties = {sender, *(msg.get("recipients") or [])}
        for who in parties:
            if not who or who == owner:
                continue
            if own_domain and who.endswith("@" + own_domain):
                continue  # internal colleagues are not the network we rank on
            counts[who] += 1
            if received > last_seen.get(who, ""):
                last_seen[who] = received

        if not sender or sender == owner:
            continue
        first_inbound.setdefault(sender, msg)  # first (newest, since desc) wins
        if msg.get("conversation_id"):
            replies += 1  # they wrote from this address — corroborates their identity

    # Pass 2: read those signatures with the model, CONCURRENTLY. The per-sweep budget caps a
    # first backfill so it cannot fan out into hundreds of calls; incremental sweeps see only a
    # handful of new senders and never approach it. The worker is already a thread pool, so a
    # bounded pool here just overlaps the network waits.
    candidates = list(first_inbound.items())[:LLM_SIG_BUDGET]

    def _read(item):
        sender, msg = item
        try:
            return sender, extract_signature_llm(
                msg.get("body"), sender, is_html=msg.get("body_is_html"))
        except Exception:  # noqa: BLE001 — one odd message must not end the sweep
            return sender, None

    sig_claims: list[tuple[str, str, str, list]] = []
    seen_claim: set[tuple[str, str, str]] = set()
    llm_sigs = phones = 0
    if candidates:
        with ThreadPoolExecutor(max_workers=min(8, len(candidates))) as pool:
            for sender, lsig in pool.map(_read, candidates):
                if not lsig:
                    continue
                llm_sigs += 1
                phones += 1 if lsig.phone else 0
                ev = [{"kind": "llm.signature-extraction",
                       "detail": f'a model read "{lsig.title or lsig.phone}" from their signature'}]
                for field, value in (
                    ("title", lsig.title), ("phone", lsig.phone),
                    ("seniority", lsig.seniority), ("function", lsig.function),
                ):
                    key = (sender, field, value or "")
                    if value and key not in seen_claim:
                        seen_claim.add(key)
                        sig_claims.append((sender, field, value, ev))

    # CORRESPONDENCE FIRST — it is the signal the Relation agent ranks on, and it is one bulk
    # graph write (fast). Doing it before the fact write means a slow or failed Mongo write
    # can never again cost us the ranking data, which is what happened before this reorder.
    updated = 0
    try:
        updated = update_correspondence(owner, org, counts, last_seen, mode=mode)
        logger.info("Mail sweep: wrote correspondence onto %d contacts for %s (%s)",
                    updated, owner, mode)
    except Exception as exc:  # noqa: BLE001 — facts still get written below; the graph can lag
        logger.warning("Mail sweep: graph correspondence update failed for %s: %s", owner, exc)

    # All signature facts in ONE bulk call (2 queries + 1 write) instead of ~4 round trips
    # per message. This is the line that used to hang the task.
    tally = {"applied": 0, "proposed": 0, "skipped": 0}
    if sig_claims:
        try:
            tally = facts.record_bulk(org, sig_claims)
        except Exception as exc:  # noqa: BLE001 — correspondence is already saved
            logger.warning("Mail sweep: signature fact write failed for %s: %s", owner, exc)

    # Move the bookmark forward only after a successful pass. `newest` never goes backwards
    # (commit takes the max), so an empty incremental sweep simply leaves it where it was.
    sync.commit(owner, org, high_water=(newest or None), backfilled=True)
    if len(first_inbound) > LLM_SIG_BUDGET:
        logger.info("Mail sweep: %d senders this run exceeded the %d-call model budget for %s "
                    "(the rest are read on later sweeps)",
                    len(first_inbound), LLM_SIG_BUDGET, owner)

    logger.info(
        "Mail sweep for %s (%s): %d messages, %d correspondents, %d signatures read by model "
        "(%d facts applied, %d suggested)",
        owner, mode, len(messages), len(counts), llm_sigs,
        tally.get("applied", 0), tally.get("proposed", 0),
    )
    return {
        "swept": len(messages), "correspondents": len(counts), "mode": mode,
        "signatures": llm_sigs, "llm_signatures": llm_sigs,
        "facts_applied": tally.get("applied", 0), "facts_suggested": tally.get("proposed", 0),
        "phones": phones, "graph_updated": updated, "replies": replies,
        "high_water": newest or None,
    }


@celery_app.task(name="mail_sweep.daily")
def sweep_all_mailboxes() -> dict:
    """Beat entry — sweep every employee with a connected Outlook mailbox."""
    from auth.database import get_mongodb_client

    db = get_mongodb_client().get_database()
    dispatched = 0
    for user in db["users"].find(
        {"email": {"$exists": True}, "organization_id": {"$exists": True}},
        {"email": 1, "organization_id": 1},
    ):
        email, org = user.get("email"), str(user.get("organization_id") or "")
        if email and org:
            sweep_mailbox_task.delay(email.lower(), org)
            dispatched += 1
    logger.info("Mail sweep: dispatched %d mailbox sweeps", dispatched)
    return {"dispatched": dispatched}
