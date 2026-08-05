"""CRM Agent task — runs during capture, in parallel with the doc agents.

Searches the FalkorDB knowledge graph for contacts relevant to the opportunity
and stores the ranked shortlist on the opportunity (shown in the Contacts tab).
The agent may legitimately return an empty list — we still record that the search
ran (contacts_searched_at), so the UI can say "no relevant contacts found".
"""
import logging

from agent.crm_agent import recommend_contacts
from app.worker import celery_app
from client.crm_store import get_crm_store
from client.events_store import record_event

logger = logging.getLogger(__name__)


@celery_app.task(bind=True, max_retries=2, default_retry_delay=15)
def recommend_contacts_task(self, opp: dict, employee_email: str | None = None) -> dict:
    """Find the relevant network contacts for one opportunity and store them.

    `employee_email` (the acting rep who approved capture) scopes the search to that
    employee's OWN contact network.
    """
    crm = get_crm_store()
    try:
        result = recommend_contacts(opp, proposal=opp.get("description"), employee_email=employee_email)
    except Exception as exc:  # transient LLM / graph errors -> retry
        logger.warning("CRM recommend failed for %s: %s", opp.get("id"), exc)
        raise self.retry(exc=exc)

    recs = [r.model_dump() for r in result.recommendations]
    crm.set_recommended_contacts(opp["id"], recs)

    # Keep WHY each contact was surfaced. An empty result is recorded too (ok=False):
    # "the search ran and found nobody relevant" is a real answer, and without it the
    # UI cannot tell that apart from "the search never ran".
    org = str(opp.get("organization_id") or "")
    if recs:
        record_event(
            org, "relation", "opportunity", str(opp["id"]),
            f"surfaced {len(recs)} contact(s)",
            "; ".join(f"{r.get('name') or r.get('email')} — {r.get('reason', '')}" for r in recs[:10]),
        )
    else:
        record_event(org, "relation", "opportunity", str(opp["id"]),
                     "no relevant contacts in the network",
                     "The graph search ran and returned nobody worth engaging.", ok=False)

    logger.info("CRM found %d relevant contacts for %s", len(recs), opp.get("id"))
    return {"id": opp["id"], "relevant": len(recs)}
