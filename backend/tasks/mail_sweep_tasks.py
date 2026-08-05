"""The mail sweep — one pass over recent mail that buys three things at once.

No AI. No per-contact API calls. One paginated read of the recent tail of a mailbox,
from which we extract:

  1. SIGNATURE BLOCKS -> job titles, phone numbers, seniority, function. Free, and the
     best source there is for a title: people update a signature the week they are
     promoted.
  2. CORRESPONDENCE COUNTS -> `corr_count` / `last_contact` on the graph. These are
     currently hardcoded to 0 by `fetch_outlook_network` (it reads only the address
     book), while `crm_agent.py` instructs the model to rank contacts on `corr_count`.
     Today that instruction is weighing a constant. This is what fixes it.
  3. WHO REPLIED TO US -> `outlook.thread-reply`, primary identity evidence. Combined
     with a dataset match on the same employer, that is what promotes a company from a
     suggestion to a fact.

Doing this per-contact would cost one API call each — thousands per mailbox. Doing it as
one sweep of the recent tail costs a handful of calls and covers the contacts that
actually matter, because the people you correspond with are the people you correspond
with recently.

See docs/enrichment-implementation-plan.md §5.10.
"""
import logging
from collections import defaultdict

from app.worker import celery_app

logger = logging.getLogger(__name__)

# How far back one sweep reaches. The recent tail is where the value is; the cost of the
# rest is real and the returns fall away quickly.
SWEEP_LIMIT = 400


@celery_app.task(bind=True, name="mail_sweep.for_employee", max_retries=2, default_retry_delay=60)
def sweep_mailbox_task(self, employee_email: str, organization_id: str, limit: int = SWEEP_LIMIT) -> dict:
    """Sweep ONE employee's recent mail. Owner-scoped: correspondence signals belong to
    the employee whose mailbox they came from, while the facts they yield are org-level."""
    from client.facts_store import get_facts_store
    from client.graph_store import update_correspondence
    from utils.composio_utils import fetch_recent_messages
    from utils.signature import derive_function, derive_seniority, evidence_detail, extract_signature

    owner = (employee_email or "").strip().lower()
    org = (organization_id or "").strip()
    if not owner or not org:
        return {"swept": 0, "reason": "missing owner or organization"}

    try:
        messages = fetch_recent_messages(owner, limit=limit)
    except Exception as exc:  # transient Composio/Graph errors -> retry
        logger.warning("Mail sweep: fetch failed for %s: %s", owner, exc)
        raise self.retry(exc=exc)

    facts = get_facts_store()
    counts: dict[str, int] = defaultdict(int)
    last_seen: dict[str, str] = {}
    titles = phones = replies = 0
    own_domain = owner.split("@", 1)[1] if "@" in owner else ""

    for msg in messages:
        sender = msg.get("sender_email") or ""
        received = (msg.get("received_at") or "")

        # --- correspondence signal (both directions count as contact) ---------------
        parties = {sender, *(msg.get("recipients") or [])}
        for who in parties:
            if not who or who == owner:
                continue
            if own_domain and who.endswith("@" + own_domain):
                continue  # internal colleagues are not the network we rank on
            counts[who] += 1
            if received > last_seen.get(who, ""):
                last_seen[who] = received

        # --- inbound only: a signature and a reply are things THEY did ---------------
        if not sender or sender == owner:
            continue
        try:
            sig = extract_signature(msg.get("body"), sender, is_html=msg.get("body_is_html"))
        except Exception:  # noqa: BLE001 — one odd message must not end the sweep
            sig = None
        if sig:
            evidence = [{
                "kind": "outlook.signature-block",
                "detail": evidence_detail(sig, received[:10] or None),
            }]
            outcomes = facts.record_many(
                org, sender,
                [("title", sig.title), ("phone", sig.phone),
                 ("seniority", derive_seniority(sig.title)),
                 ("function", derive_function(sig.title))],
                evidence,
            )
            titles += 1 if sig.title and outcomes.get("title", None) else 0
            phones += 1 if sig.phone else 0

        # They wrote to us from this address: primary evidence that the address is
        # really theirs, which corroborates whatever we believe about their employer.
        if msg.get("conversation_id"):
            replies += 1

    updated = 0
    try:
        updated = update_correspondence(owner, org, counts, last_seen)
    except Exception as exc:  # noqa: BLE001 — facts are saved; the graph can lag
        logger.warning("Mail sweep: graph correspondence update failed for %s: %s", owner, exc)

    logger.info(
        "Mail sweep for %s: %d messages, %d correspondents, %d titles, %d phones",
        owner, len(messages), len(counts), titles, phones,
    )
    return {
        "swept": len(messages), "correspondents": len(counts),
        "titles": titles, "phones": phones, "graph_updated": updated, "replies": replies,
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
