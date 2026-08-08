"""Celery task — rebuild ONE employee's contact network after they connect Outlook.

The OAuth callback triggers this. It (1) pulls the employee's network — email-history
correspondents MERGED with their Outlook address book — via Composio, (2) enriches them
from its email domain, and (3) prunes-then-rebuilds ONLY that employee's slice of the FalkorDB
graph — so a re-sync drops their stale contacts and never touches another employee's
network. `owner_email` is the employee's login email (their Composio entity).
"""
import logging

from app.worker import celery_app
from client.facts_store import get_facts_store
from client.graph_store import (
    _new_stamp,
    clear_owner_graph,
    sweep_owner_graph,
    upsert_contacts,
)
from client.task_store import PRIORITY, STAND_DOWN_DAYS, get_task_store
from utils.company_enrich import enrich_contacts_company
from utils.composio_utils import fetch_outlook_network
from utils.signature import derive_function, derive_seniority

logger = logging.getLogger(__name__)


def _queue_company_research(organization_id: str, contacts: list[dict]) -> int:
    """Queue the companies our free dataset could not resolve, so an agent can look them
    up later. This is what finally READS `company_needs_research` — the flag has been
    written on every ingest since day one and, until now, nothing ever acted on it.

    One job per DOMAIN, not per contact: fifty contacts at the same unknown company are
    one question, asked once. The stand-down stops a fruitless lookup being re-bought
    every time the mailbox is re-synced.
    """
    domains = {
        (c.get("domain") or "").strip().lower()
        for c in contacts
        if c.get("company_needs_research")
    }
    domains.discard("")
    if not domains:
        return 0
    return get_task_store().enqueue_many(
        organization_id, "research_company", "company", sorted(domains),
        "the company dataset had no entry for this domain",
        priority=PRIORITY["sweep"], budget=3, cooldown_days=STAND_DOWN_DAYS,
    )


def _record_contact_facts(organization_id: str, contacts: list[dict]) -> int:
    """Store what we learned about each contact, tagged with WHERE it came from.

    A plain loop — no agent, no network per contact. The evidence decides the outcome: a
    dataset/gov-rule match is written onto the record, while a company name derived off the
    domain becomes a SUGGESTION a rep settles in one click, instead of being asserted as
    though we knew it.

    Collected first and written in ONE bulk call. Writing each fact individually costs ~4
    round trips, which against a remote Mongo measured 1.8 s per fact — five hours for a
    single 2,700-contact mailbox. The invariants are identical either way (facts_store
    evaluates them in memory against one pre-loaded snapshot); only the round trips differ.

    Facts are org-level (organization_id + email), not per-owner: a job title is the same
    truth for every rep in the org. Relationship signals (corr_count, last_contact) stay
    per-owner in the graph, which is why they are not recorded here.
    """
    claims: list[tuple[str, str, str | None, list]] = []
    for c in contacts:
        email = (c.get("email") or "").strip().lower()
        if not email:
            continue

        # The company name and what that company does share one observation: whatever
        # resolved the domain (dataset hit, .gov/.mil rule, or the derived guess).
        company_evidence = c.get("evidence") or []
        if company_evidence:
            claims.append((email, "company", c.get("company"), company_evidence))
            claims.append((email, "industry", c.get("industry"), company_evidence))

        # A job title on the contact came from Outlook's own address book — a different
        # source from the domain, so it carries its own (weaker) evidence. Seniority and
        # function are derived from it for free, and are what let the Relation agent tell
        # a Capture Manager from a junior developer.
        title = (c.get("title") or "").strip()
        if title:
            book = [{
                "kind": "outlook.address-book",
                "detail": f'their Outlook contact card lists "{title}"',
            }]
            claims.append((email, "title", title, book))
            claims.append((email, "seniority", derive_seniority(title), book))
            claims.append((email, "function", derive_function(title), book))

    if not claims:
        return 0
    tally = get_facts_store().record_bulk(organization_id, claims)
    return tally["applied"] + tally["proposed"]


@celery_app.task(bind=True, max_retries=2, default_retry_delay=20)
def sync_outlook_contacts_task(self, owner_email: str, organization_id: str) -> dict:
    """Build ONE employee's network from their email history → enrich → upsert.

    Owner-scoped (`owner_email`) inside the employee's ORG graph (`organization_id`).
    """
    owner = (owner_email or "").strip().lower()
    org = (organization_id or "").strip()
    if not owner or not org:
        logger.warning("sync_outlook_contacts_task missing owner_email/organization_id — skipping.")
        return {"fetched": 0, "enriched": 0, "graphed": 0, "owner_email": owner or None}

    try:
        # The Composio entity for this employee IS their email, so it doubles as
        # both the mailbox to read and the graph owner. Network = email-history
        # correspondents + Outlook address book, merged + deduped.
        contacts = fetch_outlook_network(user_id=owner)
    except Exception as exc:  # transient Composio / Graph errors -> retry
        logger.warning("Outlook network fetch failed for %s: %s", owner, exc)
        raise self.retry(exc=exc)
    logger.info("Found %d network contacts for owner=%s", len(contacts), owner)

    # Resolve each contact's COMPANY (+ what it does) for free from its email domain via the
    # PDL Free Company Dataset. No paid API; unknown companies keep a derived name + a
    # research flag for the CRM agent.
    enriched = enrich_contacts_company(contacts)
    resolved = sum(1 for c in enriched if c.get("enriched"))
    logger.info("Company-enriched %d/%d contacts from the domain dataset", resolved, len(enriched))

    # Record WHERE each value came from, so a guess is stored as a suggestion rather than
    # asserted. Never fatal: the graph rebuild below is the important part of this task.
    try:
        facts = _record_contact_facts(org, enriched)
        queued = _queue_company_research(org, enriched)
        logger.info("Recorded %d contact facts, queued %d company lookups for %s",
                    facts, queued, owner)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Recording contact facts failed for %s: %s", owner, exc)

    # WRITE FIRST, THEN SWEEP — never the other way round.
    #
    # This used to clear the owner's slice and then rebuild it. That window between the
    # two is a data-loss bug: any failure inside it (a proxy blip, a timeout, a crash)
    # leaves the employee with an EMPTY contact network and nothing the user ever sees —
    # the error was logged and the task still returned success. It fired twice on real
    # data while this was being tested.
    #
    # Writing first means a mid-run failure leaves the OLD contacts in place next to the
    # new ones: stale, but present, and corrected by the next successful sync. The sweep
    # only runs once the write has fully succeeded.
    written = 0
    try:
        stamp = _new_stamp()
        written = upsert_contacts(enriched, owner, org, sync_stamp=stamp)
        removed = sweep_owner_graph(owner, org, stamp)
        logger.info("Rebuilt %s's graph with %d contacts (%d stale removed)",
                    owner, written, removed)
    except Exception as exc:  # graph down shouldn't lose the fetched data
        logger.error("FalkorDB rebuild failed (old contacts left intact): %s", exc)

    return {
        "fetched": len(contacts),
        "enriched": resolved,
        "graphed": written,
        "owner_email": owner,
    }


@celery_app.task(bind=True, max_retries=2, default_retry_delay=20)
def ingest_selected_contacts_task(
    self, owner_email: str, organization_id: str, contacts: list[dict]
) -> dict:
    """Enrich + graph ONLY the contacts the user hand-picked in the review dialog.

    Unlike `sync_outlook_contacts_task`, this skips the Outlook fetch entirely — the
    candidates were already previewed and selected on the client. It still prunes-then-
    rebuilds the owner's subgraph, so the graph ends up being EXACTLY the selected set.
    """
    owner = (owner_email or "").strip().lower()
    org = (organization_id or "").strip()
    picked = [c for c in (contacts or []) if (c.get("email") or "").strip()]
    if not owner or not org:
        logger.warning("ingest_selected_contacts_task missing owner/org — skipping.")
        return {"selected": 0, "enriched": 0, "graphed": 0, "owner_email": owner or None}
    if not picked:
        # Nothing chosen: clear the owner's slice so the graph reflects "import none".
        try:
            clear_owner_graph(owner, org)
        except Exception as exc:  # noqa: BLE001
            logger.error("FalkorDB clear failed for %s: %s", owner, exc)
        return {"selected": 0, "enriched": 0, "graphed": 0, "owner_email": owner}

    enriched = enrich_contacts_company(picked)
    resolved = sum(1 for c in enriched if c.get("enriched"))
    logger.info("Company-enriched %d/%d selected contacts for %s", resolved, len(enriched), owner)

    # Same as the sync path: provenance is recorded, and never fatal to the ingest.
    try:
        facts = _record_contact_facts(org, enriched)
        queued = _queue_company_research(org, enriched)
        logger.info("Recorded %d contact facts, queued %d company lookups for %s",
                    facts, queued, owner)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Recording contact facts failed for %s: %s", owner, exc)

    # Write-then-sweep, same as the sync path: a failure mid-write leaves the previous
    # contacts intact rather than wiping the employee's network. (The "nothing picked"
    # branch above is the one place a bare clear is correct — importing none MEANS empty.)
    try:
        stamp = _new_stamp()
        written = upsert_contacts(enriched, owner, org, sync_stamp=stamp)
        removed = sweep_owner_graph(owner, org, stamp)
        logger.info("Rebuilt %s's graph with %d selected contacts (%d stale removed)",
                    owner, written, removed)
    except Exception as exc:  # noqa: BLE001
        logger.error("FalkorDB rebuild failed (old contacts left intact): %s", exc)
        raise self.retry(exc=exc)

    return {"selected": len(picked), "enriched": resolved, "graphed": written, "owner_email": owner}
