"""Celery tasks for the Analyst Agent.

- analyze_opportunity_task: runs ONE agent over ONE opportunity and writes the
  verdict (+ call/follow-up) back to the CRM store.
- run_analyst_batch: fetches every unanalyzed opportunity and fans them out as
  a group of analyze_opportunity_task's. Concurrency (the "15 at a time") is
  enforced by the worker:  --pool=threads --concurrency=15
"""
import logging

from celery import group

from agent.analyst_agent import analyze_opportunity
from app.worker import celery_app
from client.crm_store import get_crm_store

logger = logging.getLogger(__name__)


@celery_app.task(bind=True, max_retries=2, default_retry_delay=15)
def analyze_opportunity_task(self, opp: dict) -> dict:
    """Analyze one opportunity and write the verdict back to the CRM.

    `opp` is a canonical opportunity document (snake_case) straight from Mongo —
    the agent reads it directly, no mapping needed.
    """
    crm = get_crm_store()
    try:
        verdict = analyze_opportunity(opp)
    except Exception as exc:  # transient LLM / network errors -> retry
        logger.warning("Analyst failed for %s: %s", opp.get("id"), exc)
        if self.request.retries >= self.max_retries:
            # apply_verdict (which clears the ingest flag) will never run — clear it here so a
            # manual opp whose analysis permanently failed exits "Ingesting". No-op for the rest
            # of the batch (only the manual opp has ingesting=true).
            if opp.get("id") and opp.get("organization_id"):
                crm.set_ingesting(opp["id"], opp["organization_id"], False, error=str(exc))
            raise
        raise self.retry(exc=exc)

    crm.apply_verdict(opp["id"], verdict)

    name = opp.get("title") or "opportunity"
    if verdict.bid_decision == "Bid" and verdict.call_action:
        crm.create_call(
            opp["id"],
            name=f"Call: {name}",
            talking_point=verdict.call_action.talking_point,
        )
    elif verdict.bid_decision == "Watch":
        crm.create_task(opp["id"], name=f"Revisit: {name}", description=verdict.rationale)

    return {"id": opp["id"], "decision": verdict.bid_decision, "priority": verdict.priority_score}


@celery_app.task
def run_analyst_batch(organization_id: str) -> dict:
    """Fan out one analyze task per unanalyzed opportunity IN ONE ORG."""
    crm = get_crm_store()
    opps = crm.list_unanalyzed_opportunities(organization_id)
    if not opps:
        return {"dispatched": 0}
    group(analyze_opportunity_task.s(o) for o in opps).apply_async()
    logger.info("Dispatched %d analyst tasks for org %s", len(opps), organization_id)
    return {"dispatched": len(opps)}
