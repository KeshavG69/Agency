"""Celery tasks for the capture pipeline (Phase 2).

One Capture agent per opportunity (was Capture Plan -> Shaping, now merged). Across
opportunities it's a GROUP (parallel, capped by worker concurrency). Only runs on
human-APPROVED, not-yet-captured opportunities (capture_approved == True, captured_at null).
"""
import logging

from celery import group

from agent.capture_agent import generate_capture
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
    `real_uploads` mapping each uploaded filename → {url, object_key, sharepoint_url,
    sharepoint_item_id}. We resolve each deliverable's real download link from there by filename
    (never from a URL the model self-reports) and drop any deliverable that was never uploaded.
    SharePoint filing is best-effort — a miss just leaves the doc as an iDrive-only 'draft'.
    """
    crm = get_crm_store()
    try:
        output, real_uploads = generate_capture(opp, employee_email)
    except Exception as exc:
        logger.warning("Capture failed for %s: %s", opp.get("id"), exc)
        if self.request.retries >= self.max_retries:
            # Terminal failure — stamp captured_at so the opp leaves the UI's "Processing"
            # window (capture_approved && !captured_at) instead of sitting there forever.
            crm.mark_capture_failed(opp["id"], str(exc))
            raise
        raise self.retry(exc=exc)

    # Persist ONLY deliverables backed by a real upload. The download link comes from the tool
    # (real_uploads), never from the model — so a deliverable the model listed but never actually
    # uploaded is dropped instead of stored with a fabricated URL (e.g. "https://example.com").
    remaining = dict(real_uploads)  # filename -> {url, object_key, sharepoint_url, sharepoint_item_id}
    created = 0
    for d in output.deliverables:
        up = remaining.pop(d.filename, None)
        if up is None and remaining:
            # Filename drift tolerance: the model reported a name that doesn't exactly match what
            # it uploaded, but a real upload is still unclaimed — pair them in order.
            _, up = remaining.popitem()
        if up is None:
            logger.warning("Capture for %s: deliverable '%s' (%s) has no real upload — dropping "
                           "(model did not upload it).", opp.get("id"), d.title, d.doc_type)
            continue
        document_id = crm.create_document(
            opp["id"], agent_id="capture_agent", doc_type=d.doc_type,
            title=d.title, url=up["url"], object_key=up.get("object_key"),
        )
        if up.get("sharepoint_url"):
            crm.update_document(document_id, status="filed",
                                sharepoint_url=up["sharepoint_url"],
                                sharepoint_item_id=up.get("sharepoint_item_id"))
        created += 1

    if output.deliverables and created == 0:
        logger.warning("Capture for %s produced %d deliverable(s) but NONE were backed by a real "
                       "upload — nothing persisted.", opp.get("id"), len(output.deliverables))
    crm.mark_captured(opp["id"])
    return {"id": opp["id"], "deliverables": [d.doc_type for d in output.deliverables], "created": created}


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
