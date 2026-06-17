"""Opportunity actions — the human approval gate + kicking off capture."""
from fastapi import APIRouter

from client.crm_store import get_crm_store
from tasks.analyst_tasks import run_analyst_batch
from tasks.capture_tasks import run_capture_batch

router = APIRouter(prefix="/api/opportunities", tags=["opportunities"])


@router.get("")
def list_opportunities() -> dict:
    """All opportunities, each enriched with its documents and calls (for the UI)."""
    crm = get_crm_store()
    opps = crm.list_all()
    for o in opps:
        o["documents"] = crm.list_documents(o["id"])
        o["calls"] = crm.list_calls(o["id"])
    return {"opportunities": opps}


@router.post("/analyze/run")
def run_analyst() -> dict:
    """Phase 1 — kick off the Analyst on all unanalyzed opportunities (Celery)."""
    task = run_analyst_batch.delay()
    return {"task_id": task.id}


@router.post("/{opportunity_id}/approve-capture")
def approve_capture(opportunity_id: str) -> dict:
    """Human approves an opportunity for capture (Gate before the capture agents run)."""
    get_crm_store().mark_capture_approved(opportunity_id)
    return {"opportunity_id": opportunity_id, "capture_approved": True}


@router.post("/capture/run")
def run_capture() -> dict:
    """Kick off the capture pipeline for all approved, not-yet-captured opportunities."""
    task = run_capture_batch.delay()
    return {"task_id": task.id}
