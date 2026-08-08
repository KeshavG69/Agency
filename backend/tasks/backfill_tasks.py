"""Backfill — give the contacts we ALREADY have the treatment new arrivals get.

Everything imported before the evidence model existed has no facts, no provenance and no
suggestions: its company name sits on a graph node with no record of whether it was a
dataset match or a guess off the domain. New imports are handled at ingest; the back
catalogue needs one deliberate pass.

Reads the graph (the system of record for who we know), re-derives the company from the
domain exactly as ingestion does, records the evidence, and queues a lookup for any
company the free dataset still cannot resolve.

DELIBERATELY BORING, ON PURPOSE
  * No AI. It is the same plain-code path ingestion uses.
  * Nothing is overwritten. `record_fact` refuses to touch a human-set value and never
    re-offers a dismissed one, so this is safe to run repeatedly.
  * Chunked, and it queues rather than researches. A backfill that fires thousands of
    lookups at once is how you turn a one-off improvement into a surprise invoice — the
    queue's own stand-downs and lanes then meter the actual work.

Run it per org:
    celery -A app.worker call backfill.contacts --args='["<org_id>"]'
"""
import logging

from app.worker import celery_app

logger = logging.getLogger(__name__)

CHUNK = 500


@celery_app.task(bind=True, name="backfill.contacts", max_retries=1, default_retry_delay=60)
def backfill_contacts_task(self, organization_id: str, limit: int = 5000) -> dict:
    """Re-derive company evidence for existing contacts in ONE org."""
    from client.facts_store import get_facts_store
    from client.graph_store import get_graph
    from tasks.contacts_tasks import _queue_company_research, _record_contact_facts
    from utils.company_enrich import enrich_contacts_company

    org = (organization_id or "").strip()
    if not org:
        return {"contacts": 0, "reason": "no organization_id"}

    try:
        res = get_graph(org).ro_query(
            """
            MATCH (p:Person)
            OPTIONAL MATCH (p)-[:WORKS_AT]->(c:Company)
            RETURN p.email, p.name, p.title, p.domain, c.name, p.industry
            LIMIT $limit
            """,
            params={"limit": int(limit)},
        )
    except Exception as exc:  # noqa: BLE001 — graph down: retry once, then give up quietly
        logger.warning("Backfill: graph read failed for org %s: %s", org, exc)
        raise self.retry(exc=exc)

    # One row per distinct email: facts are org-level, so a contact known to three
    # employees is still one set of facts and must not be processed three times.
    seen: set[str] = set()
    contacts: list[dict] = []
    for r in res.result_set:
        email = (r[0] or "").strip().lower()
        if not email or email in seen:
            continue
        seen.add(email)
        contacts.append({
            "email": email, "name": r[1], "title": r[2],
            "domain": r[3], "company": r[4], "industry": r[5],
        })

    if not contacts:
        return {"contacts": 0, "facts": 0, "queued": 0}

    facts = queued = 0
    for start in range(0, len(contacts), CHUNK):
        chunk = contacts[start:start + CHUNK]
        enriched = enrich_contacts_company(chunk)
        try:
            facts += _record_contact_facts(org, enriched)
            queued += _queue_company_research(org, enriched)
        except Exception as exc:  # noqa: BLE001 — one bad chunk must not lose the rest
            logger.warning("Backfill: chunk at %d failed for org %s: %s", start, org, exc)

    logger.info(
        "Backfill org %s: %d contacts -> %d facts recorded, %d company lookups queued",
        org, len(contacts), facts, queued,
    )
    return {"contacts": len(contacts), "facts": facts, "queued": queued}


@celery_app.task(name="backfill.all_orgs")
def backfill_all_orgs(limit: int = 5000) -> dict:
    """Fan out the backfill across every organisation. Manual — not on a schedule.

    Left off the beat deliberately: this is a one-off catch-up, and a catch-up that runs
    itself nightly is just an expensive no-op once it has caught up.
    """
    from auth.database import get_mongodb_client

    db = get_mongodb_client().get_database()
    dispatched = 0
    for org in db["organizations"].find({}, {"_id": 1}):
        backfill_contacts_task.delay(str(org["_id"]), limit)
        dispatched += 1
    logger.info("Backfill dispatched for %d orgs", dispatched)
    return {"orgs": dispatched}
