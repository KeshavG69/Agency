"""Celery task — rebuild ONE employee's contact network after they connect Outlook.

The OAuth callback triggers this. It (1) pulls the employee's network — email-history
correspondents MERGED with their Outlook address book — via Composio, (2) enriches them
via Explorium, and (3) prunes-then-rebuilds ONLY that employee's slice of the FalkorDB
graph — so a re-sync drops their stale contacts and never touches another employee's
network. `owner_email` is the employee's login email (their Composio entity).
"""
import logging

from app.worker import celery_app
from client.graph_store import clear_owner_graph, upsert_contacts
from utils.composio_utils import fetch_outlook_network
from utils.explorium import enrich_contacts

logger = logging.getLogger(__name__)


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

    # Resolve designation / company / name from the email via Explorium.
    enriched = enrich_contacts(contacts)
    resolved = sum(1 for c in enriched if c.get("enriched"))
    logger.info("Explorium resolved %d/%d contacts", resolved, len(enriched))

    # Prune-then-rebuild THIS employee's subgraph only, inside their org graph. Done
    # after a successful fetch/enrich, so a transient failure never empties the network.
    try:
        clear_owner_graph(owner, org)
        written = upsert_contacts(enriched, owner, org)
        logger.info("Rebuilt %s's graph with %d contacts", owner, written)
    except Exception as exc:  # graph down shouldn't lose the fetched data
        logger.error("FalkorDB rebuild failed: %s", exc)
        written = 0

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

    enriched = enrich_contacts(picked)
    resolved = sum(1 for c in enriched if c.get("enriched"))
    logger.info("Explorium resolved %d/%d selected contacts for %s", resolved, len(enriched), owner)

    try:
        clear_owner_graph(owner, org)
        written = upsert_contacts(enriched, owner, org)
        logger.info("Rebuilt %s's graph with %d selected contacts", owner, written)
    except Exception as exc:  # noqa: BLE001
        logger.error("FalkorDB rebuild failed: %s", exc)
        raise self.retry(exc=exc)

    return {"selected": len(picked), "enriched": resolved, "graphed": written, "owner_email": owner}
