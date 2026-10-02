"""The mail sweep — an incremental pass over a mailbox that buys three things at once.

INCREMENTAL, via a per-mailbox bookmark (mailbox_sync). The first run backfills the recent
tail; every run after reads only what arrived since, so there is no fixed message cap and no
re-reading. What each run extracts:

  1. SIGNATURES -> job titles, phone numbers, seniority, function. Read by a small model
     (Gemma via SIGNATURE_MODEL), not regex: it handles the free-form blocks a pattern never
     will. This is what trycompai/crm does — let a model read the block — done as a FALLBACK-
     free first pass here: one model call per sender per sweep, for EVERY sender in the run,
     concurrently. The only ceiling is how many messages the sweep reads (BACKFILL_LIMIT /
     INCREMENTAL_CAP). A lone read is a suggestion; a reply from that address corroborates it
     into a fact.
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
# Above this many recipients a message is an announcement, not a conversation, so its
# recipients do not earn correspondence credit (the sender still does). See the counting
# comment in the sweep below.
MASS_RECIPIENT_THRESHOLD = 5
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
    from utils.personal_llm import extract_personal_facts_llm
    from utils.signature import is_automated_address
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
    # Subject of each correspondent's most recent message, kept in lockstep with last_seen.
    # This is what lets a relationship nudge say "you last spoke about X on <date>" instead
    # of only "quiet 47 days" — the evidence a rep actually checks before sending.
    last_subject: dict[str, str] = {}
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

        # WHAT COUNTS AS CORRESPONDENCE. `corr_count` is read as relationship strength — it
        # decides who the Relation agent ranks and who the relationship engine nudges — so it
        # has to mean "we actually deal with this person", not "this address appeared".
        #   * the sender always counts (someone wrote to us, or we wrote to them);
        #   * recipients count only on a NARROWLY addressed message: a note to twenty people
        #     is an announcement, not a relationship, and counting it made every name on a
        #     distribution list look warm;
        #   * automated senders (noreply@, notifications@) never count at all — a system that
        #     mails you nightly would otherwise become your warmest "contact".
        recipients = msg.get("recipients") or []
        parties = {sender}
        if len(recipients) <= MASS_RECIPIENT_THRESHOLD:
            parties.update(recipients)
        for who in parties:
            if not who or who == owner:
                continue
            if own_domain and who.endswith("@" + own_domain):
                continue  # internal colleagues are not the network we rank on
            if is_automated_address(who):
                continue  # a robot is not a relationship
            counts[who] += 1
            if received > last_seen.get(who, ""):
                last_seen[who] = received
                last_subject[who] = (msg.get("subject") or "").strip()

        if not sender or sender == owner:
            continue
        first_inbound.setdefault(sender, msg)  # first (newest, since desc) wins
        if msg.get("conversation_id"):
            replies += 1  # they wrote from this address — corroborates their identity

    # Pass 2: read EVERY sender's signature with the model, CONCURRENTLY.
    #
    # There is no separate budget here on purpose: the read is already bounded upstream by how
    # many messages the sweep pulls (BACKFILL_LIMIT / INCREMENTAL_CAP), and one distinct sender
    # per message is the ceiling — so this can never exceed a few hundred small, cheap calls.
    # The old 120 cap sat *below* that ceiling and silently dropped the rest of the mailbox:
    # at 3,200 contacts it needed roughly a month of daily runs to cover one mailbox once, and
    # because senders are picked by recency it kept re-reading the same recent ones instead of
    # working through the tail. The worker is already a thread pool, so a bounded pool here
    # just overlaps the network waits.
    candidates = list(first_inbound.items())

    # Each sender's newest message is read TWICE by the model in one shot: once for the
    # signature (work facts), once for the personal thread (relationship facts). Same message,
    # same pool — the personal read is what feeds the relationship engine's hooks. Personal
    # facts are recorded as SUGGESTIONS (non-primary evidence), so a human still confirms them.
    def _read(item):
        sender, msg = item
        body, is_html = msg.get("body"), msg.get("body_is_html")
        try:
            sig = extract_signature_llm(body, sender, is_html=is_html)
        except Exception:  # noqa: BLE001 — one odd message must not end the sweep
            sig = None
        try:
            personal = extract_personal_facts_llm(body, sender, is_html=is_html)
        except Exception:  # noqa: BLE001
            personal = None
        return sender, sig, personal

    sig_claims: list[tuple[str, str, str, list]] = []
    personal_claims: list[tuple[str, str, str, list]] = []
    seen_claim: set[tuple[str, str, str]] = set()
    llm_sigs = phones = personal_hits = 0
    if candidates:
        with ThreadPoolExecutor(max_workers=min(8, len(candidates))) as pool:
            for sender, lsig, lpers in pool.map(_read, candidates):
                if lsig:
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
                if lpers:
                    personal_hits += 1
                    for field, value in lpers.as_items():
                        key = (sender, field, value)
                        if key not in seen_claim:
                            seen_claim.add(key)
                            pev = [{"kind": "llm.mail-personal-extraction",
                                    "detail": f'a model read "{value}" from an email with them'}]
                            personal_claims.append((sender, field, value, pev))

    # CORRESPONDENCE FIRST — it is the signal the Relation agent ranks on, and it is one bulk
    # graph write (fast). Doing it before the fact write means a slow or failed Mongo write
    # can never again cost us the ranking data, which is what happened before this reorder.
    updated = 0
    try:
        updated = update_correspondence(
            owner, org, counts, last_seen, mode=mode, last_subject=last_subject,
        )
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

    # Personal facts, separately, so a failure here can never lose the signature facts above.
    # They land PROPOSED (supporting evidence), i.e. as suggestions for the rep to confirm.
    ptally = {"applied": 0, "proposed": 0, "skipped": 0}
    if personal_claims:
        try:
            ptally = facts.record_bulk(org, personal_claims)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Mail sweep: personal fact write failed for %s: %s", owner, exc)

    # Move the bookmark forward only after a successful pass. `newest` never goes backwards
    # (commit takes the max), so an empty incremental sweep simply leaves it where it was.
    sync.commit(owner, org, high_water=(newest or None), backfilled=True)

    logger.info(
        "Mail sweep for %s (%s): %d messages, %d correspondents, %d signatures read by model "
        "(%d facts applied, %d suggested); %d senders with personal facts (%d suggested)",
        owner, mode, len(messages), len(counts), llm_sigs,
        tally.get("applied", 0), tally.get("proposed", 0),
        personal_hits, ptally.get("proposed", 0),
    )
    return {
        "swept": len(messages), "correspondents": len(counts), "mode": mode,
        "signatures": llm_sigs, "llm_signatures": llm_sigs,
        "facts_applied": tally.get("applied", 0), "facts_suggested": tally.get("proposed", 0),
        "personal_senders": personal_hits,
        "personal_suggested": ptally.get("proposed", 0),
        "phones": phones, "graph_updated": updated, "replies": replies,
        "high_water": newest or None,
    }


@celery_app.task(name="mail_sweep.daily")
def sweep_all_mailboxes() -> dict:
    """Beat entry — sweep every employee with a connected Outlook mailbox."""
    from utils.organizations import iter_active_memberships

    # NOT a query on users.organization_id — that field does not exist on a user document
    # (see iter_active_memberships), and querying it made this beat job dispatch nothing.
    dispatched = 0
    for email, org in iter_active_memberships():
        sweep_mailbox_task.delay(email, org)
        dispatched += 1
    logger.info("Mail sweep: dispatched %d mailbox sweeps", dispatched)
    return {"dispatched": dispatched}
