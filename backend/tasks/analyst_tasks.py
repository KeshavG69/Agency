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
from client.events_store import record_event

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

    # Keep the reasoning, not just the verdict. A No-Bid is recorded as ok=False so the
    # trail can show WHY we walked away — the question a rep actually asks later.
    record_event(
        str(opp.get("organization_id") or ""), "analyst", "opportunity", str(opp["id"]),
        f"{verdict.bid_decision} — priority {verdict.priority_score}",
        verdict.rationale, ok=verdict.bid_decision != "No-Bid",
    )

    _schedule_recheck(opp, verdict)
    # A verdict is what turns an opportunity into somebody's task, so refresh the day's plan.
    # Debounced org-wide, so a 300-opportunity batch buys exactly one sweep, not 300.
    if opp.get("organization_id"):
        from tasks.action_plan_tasks import request_replan  # lazy: avoid an import cycle

        request_replan(str(opp["organization_id"]))
    return {"id": opp["id"], "decision": verdict.bid_decision, "priority": verdict.priority_score}


def _schedule_recheck(opp: dict, verdict) -> None:  # noqa: ANN001
    """Put a deferred opportunity back on the agent to-do list with a date and a reason.

    The `Revisit:` card above is for a human to find. This is for the system to act on:
    without it a "Watch" is a dead end — an early-stage notice that becomes a real
    solicitation next month is simply never looked at again, because nothing re-reads
    cards. Best-effort: a scheduling miss must never lose the verdict we just wrote.
    """
    days = getattr(verdict, "recheck_after_days", None)
    org = str(opp.get("organization_id") or "")
    if not days or not org or not opp.get("id"):
        return
    try:
        from datetime import datetime, timedelta, timezone

        from client.task_store import PRIORITY, get_task_store

        get_task_store().enqueue(
            org, "recheck_opportunity", "opportunity", str(opp["id"]),
            getattr(verdict, "recheck_reason", None) or "scheduled re-judgement",
            priority=PRIORITY["recheck"], budget=4,
            due_at=datetime.now(timezone.utc) + timedelta(days=int(days)),
            cooldown_days=0,  # a recheck is MEANT to recur
        )
        logger.info("Recheck scheduled for %s in %d days", opp["id"], days)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not schedule recheck for %s: %s", opp.get("id"), exc)


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
