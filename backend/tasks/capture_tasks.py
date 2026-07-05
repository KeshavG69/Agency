"""Celery tasks for the capture pipeline (Phase 2).

One Capture agent per opportunity (was Capture Plan -> Shaping, now merged). Across
opportunities it's a GROUP (parallel, capped by worker concurrency). Only runs on
human-APPROVED, not-yet-captured opportunities (capture_approved == True, captured_at null).
"""
import logging

from celery import group

from agent.capture_agent import deliverable_url_key, generate_capture
from app.worker import celery_app
from client.crm_store import get_crm_store

logger = logging.getLogger(__name__)


@celery_app.task(bind=True, name="capture.run", max_retries=2, default_retry_delay=15)
def capture_task(self, opp: dict, employee_email: str | None = None) -> dict:
    """Run the Capture agent on one opportunity → strategy + deliverable(s), recorded.

    `employee_email` (the approving rep) RBAC-scopes the agent's SharePoint past-performance
    search to documents that employee may read.

    The capture agent's upload tool files each deliverable into the Bid's SharePoint 'Capture
    Docs' folder in the SAME pass that saves it to iDrive (see generate_capture); it returns
    `sp_uploads` mapping each iDrive url → {sharepoint_url, sharepoint_item_id}, which we record
    onto the CRM document here. SharePoint filing is best-effort — a miss just leaves the doc as
    an iDrive-only 'draft'.
    """
    crm = get_crm_store()
    try:
        output, sp_uploads = generate_capture(opp, employee_email)
    except Exception as exc:
        logger.warning("Capture failed for %s: %s", opp.get("id"), exc)
        raise self.retry(exc=exc)

    matched = 0
    for d in output.deliverables:
        document_id = crm.create_document(
            opp["id"], agent_id="capture_agent", doc_type=d.doc_type,
            title=d.title, url=d.doc_url,
        )
        # Match on the stable url PATH (see deliverable_url_key) so signature-param drift in the
        # model's echoed doc_url doesn't lose the SharePoint linkage.
        sp = sp_uploads.get(deliverable_url_key(d.doc_url))
        if sp and sp.get("sharepoint_url"):
            crm.update_document(document_id, status="filed",
                                sharepoint_url=sp["sharepoint_url"],
                                sharepoint_item_id=sp.get("sharepoint_item_id"))
            matched += 1
    if sp_uploads and matched < len(sp_uploads):
        # Files were filed to SharePoint but some couldn't be linked back to a deliverable — make
        # that visible instead of silently leaving an orphaned SharePoint copy.
        logger.warning("Capture for %s filed %d SharePoint cop(ies) but linked only %d to "
                       "deliverables (doc_url echo mismatch?)", opp.get("id"), len(sp_uploads), matched)
    crm.mark_captured(opp["id"])
    return {"id": opp["id"], "deliverables": [d.doc_type for d in output.deliverables]}


@celery_app.task
def run_capture_batch(organization_id: str) -> dict:
    """Fan out a Capture agent per approved, uncaptured opp IN ONE ORG."""
    crm = get_crm_store()
    opps = crm.list_capture_ready(organization_id)
    if not opps:
        return {"dispatched": 0}
    # Per opp: Capture agent  ||  CRM contact search.
    # NOTE: this batch path has no acting employee, so the owner-scoped contact search runs
    # without an owner and returns nothing. The per-opportunity "Approve for capture" button
    # (routers/opportunities.py) is the owner-scoped path that attributes the CRM search to
    # the approving employee's network.
    from tasks.crm_tasks import recommend_contacts_task  # lazy: avoid import cycle

    branches = []
    for opp in opps:
        branches.append(capture_task.s(opp))
        branches.append(recommend_contacts_task.s(opp))
    group(branches).apply_async()
    logger.info("Dispatched capture for %d opportunities", len(opps))
    return {"dispatched": len(opps)}
