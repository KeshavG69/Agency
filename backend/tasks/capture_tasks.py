"""Celery tasks for the capture pipeline (Phase 2).

Per opportunity it's a CHAIN: Capture Plan Agent -> Shaping Agent (sequential,
because Shaping needs the plan). Across opportunities it's a GROUP (parallel,
capped by worker concurrency). Only runs on human-APPROVED, not-yet-captured
opportunities (capture_approved == True, captured_at is null).
"""
import logging

from celery import chain, group

from agent.capture_plan_agent import generate_capture_plan
from agent.shaping_agent import generate_shaping_docs
from app.worker import celery_app
from client.crm_store import get_crm_store

logger = logging.getLogger(__name__)


@celery_app.task(bind=True, max_retries=2, default_retry_delay=15)
def capture_plan_task(self, opp: dict) -> dict:
    """Run the Capture Plan Agent, record the doc pointer, pass the plan forward."""
    crm = get_crm_store()
    try:
        result = generate_capture_plan(opp)
    except Exception as exc:
        logger.warning("Capture Plan failed for %s: %s", opp.get("id"), exc)
        raise self.retry(exc=exc)

    crm.create_document(
        opp["id"], agent_id="capture_plan_agent", doc_type="capture_plan",
        title=result.title, url=result.doc_url,
    )
    # Hand the FULL plan text to the Shaping task (next in the chain).
    return {"opp": opp, "capture_plan": result.content}


@celery_app.task(bind=True, max_retries=2, default_retry_delay=15)
def shaping_task(self, prev: dict) -> dict:
    """Run the Shaping Agent on the capture plan, record each deliverable, finish capture."""
    opp = prev["opp"]
    capture_plan = prev["capture_plan"]
    crm = get_crm_store()
    try:
        output = generate_shaping_docs(opp, capture_plan)
    except Exception as exc:
        logger.warning("Shaping failed for %s: %s", opp.get("id"), exc)
        raise self.retry(exc=exc)

    for d in output.deliverables:
        crm.create_document(
            opp["id"], agent_id="shaping_agent", doc_type=d.doc_type,
            title=d.title, url=d.doc_url,
        )
    crm.mark_captured(opp["id"])
    return {"id": opp["id"], "deliverables": [d.doc_type for d in output.deliverables]}


@celery_app.task
def run_capture_batch() -> dict:
    """Fan out a (Capture Plan -> Shaping) chain per approved, uncaptured opportunity."""
    crm = get_crm_store()
    opps = crm.list_capture_ready()
    if not opps:
        return {"dispatched": 0}
    group(
        chain(capture_plan_task.s(opp), shaping_task.s()) for opp in opps
    ).apply_async()
    logger.info("Dispatched capture pipeline for %d opportunities", len(opps))
    return {"dispatched": len(opps)}
