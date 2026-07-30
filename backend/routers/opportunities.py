"""Opportunity actions — the human approval gate + kicking off capture."""
import asyncio
import logging
import os
import tempfile
import uuid

from celery import group
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from pydantic import BaseModel

from auth.dependencies import get_current_user
from client.crm_store import get_crm_store
from client.idrive_storage import get_idrive_storage
from models.opportunity import Opportunity
from tasks.analyst_tasks import analyze_opportunity_task, run_analyst_batch
from tasks.capture_tasks import capture_task, run_capture_batch
from tasks.crm_tasks import recommend_contacts_task
from tasks.mail_tasks import draft_one_outreach_task, draft_outreach_task

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/opportunities", tags=["opportunities"])


_MAX_MANUAL_FILES = 20


def _upload_one(file_path: str, org: str, opp_id: str, filename: str) -> tuple[str, str]:
    """Blocking iDrive upload of a single file — run in a worker thread."""
    safe = os.path.basename(filename or "document").strip() or "document"
    key_name = f"{uuid.uuid4().hex[:12]}_{safe}"
    return get_idrive_storage().upload_document(
        file_path=file_path, user_id=org, proposal_id=opp_id, filename=key_name
    )


@router.post("/manual")
async def create_manual_opportunity(
    title: str = Form(...),
    number: str | None = Form(None),
    description: str | None = Form(None),
    files: list[UploadFile] = File(default=[]),
    current_user: dict = Depends(get_current_user),
) -> dict:
    """Manually add an opportunity: title + solicitation number + description + files.

    Creates the opportunity row, uploads each file to iDrive e2 (recording a document
    pointer with its object key), then hands off to a background task that parses the
    files (LiteParse), digests them into document_text (stuff-if-small / map-reduce
    with the small model), and kicks the Analyst. Returns immediately.
    """
    title = (title or "").strip()
    if not title:
        raise HTTPException(status_code=400, detail="title is required")
    if files and len(files) > _MAX_MANUAL_FILES:
        raise HTTPException(status_code=400, detail=f"Too many files (max {_MAX_MANUAL_FILES})")

    organization_id = str(current_user["organization_id"])
    crm = get_crm_store()

    opp = Opportunity(
        title=title,
        solicitation_number=(number or "").strip() or None,
        description=(description or "").strip() or None,
        source="manual",
    )
    opportunity_id = crm.insert_opportunity(opp, organization_id)
    # Mark it "ingesting" so the UI shows it in the Ingesting section until the
    # background pipeline (parse -> digest -> Analyst verdict) completes. Cleared by
    # apply_verdict on success, or by the tasks below on terminal failure.
    crm.set_ingesting(opportunity_id, organization_id, True)

    # Upload each file to iDrive (shared storage) here so the bytes are durable and
    # reachable by the worker container. The upload is blocking (boto3) -> offload to
    # a thread so it doesn't block the event loop. A per-file failure is skipped, not fatal.
    uploaded = 0
    for f in files:
        data = await f.read()
        if not data:
            continue
        with tempfile.NamedTemporaryFile(delete=True) as tmp:
            tmp.write(data)
            tmp.flush()
            try:
                url, key = await asyncio.to_thread(
                    _upload_one, tmp.name, organization_id, opportunity_id, f.filename or "document"
                )
            except Exception as exc:  # noqa: BLE001 — don't fail the whole add on one bad upload
                logger.warning("Manual upload: iDrive upload failed for %s: %s", f.filename, exc)
                continue
        crm.create_document(
            opportunity_id,
            agent_id="manual_upload",
            doc_type="solicitation",
            title=os.path.basename(f.filename or "document"),
            url=url,
            object_key=key,
        )
        uploaded += 1

    # Parse + digest + analyze off the request path. Lazy import: app.worker imports
    # manual_upload_tasks, so a top-level import here re-enters this module mid-load
    # (empty router at include time). Matches the notify_tasks/sharepoint_tasks pattern below.
    from tasks.manual_upload_tasks import process_manual_upload_task

    process_manual_upload_task.delay(opportunity_id, organization_id)

    return {
        "opportunity_id": opportunity_id,
        "created": True,
        "files": uploaded,
        "processing": True,
    }


@router.get("")
def list_opportunities(current_user: dict = Depends(get_current_user)) -> dict:
    """DEPRECATED (kept during the pagination migration): the whole org enriched in one payload.
    ~10MB/9.5s on a large org — the UI now uses /page + /counts + /{id} instead.

    Admins see everything; members see only opportunities assigned to them or unassigned.
    """
    crm = get_crm_store()
    is_admin = current_user.get("role") == "admin"
    return {
        "opportunities": crm.list_all_enriched(
            str(current_user["organization_id"]),
            viewer_id=str(current_user["_id"]),
            is_admin=is_admin,
        )
    }


def _list_filters(
    status: str | None = Query(None),
    agency: list[str] = Query(default=[]),
    naics: list[str] = Query(default=[]),
    set_aside: list[str] = Query(default=[]),
    source: str | None = Query(None),
    value: str | None = Query(None),
    due: int | None = Query(None),
    q: str | None = Query(None),
    posted_date: str | None = Query(None),
) -> dict:
    """The shared pipeline filter/search/calendar params, mapped to crm_store kwargs."""
    return {
        "status": status, "agencies": agency, "naics": naics, "set_asides": set_aside,
        "source": source, "value_bucket": value, "due_days": due, "q": q, "posted_date": posted_date,
    }


def _org_scope(current_user: dict) -> tuple[str, str, bool]:
    return (
        str(current_user["organization_id"]),
        str(current_user["_id"]),
        current_user.get("role") == "admin",
    )


@router.get("/page")
def list_opportunities_page(
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    filters: dict = Depends(_list_filters),
    current_user: dict = Depends(get_current_user),
) -> dict:
    """One SLIM, filtered, sorted page of opportunities + the total match count."""
    org, viewer, is_admin = _org_scope(current_user)
    crm = get_crm_store()
    items, total = crm.list_page(org, viewer_id=viewer, is_admin=is_admin,
                                 offset=offset, limit=limit, **filters)
    return {"items": items, "total": total, "offset": offset, "limit": limit}


@router.get("/counts")
def opportunity_counts(
    filters: dict = Depends(_list_filters),
    current_user: dict = Depends(get_current_user),
) -> dict:
    """Per-status pill counts for the current facet/search/date filter + an in-flight total
    (ingesting + processing) used to arm the frontend's background poll."""
    org, viewer, is_admin = _org_scope(current_user)
    crm = get_crm_store()
    counts = crm.status_counts(org, viewer_id=viewer, is_admin=is_admin, **filters)
    return {"counts": counts, "in_flight": counts.get("ingesting", 0) + counts.get("processing", 0)}


@router.get("/facets")
def opportunity_facets(current_user: dict = Depends(get_current_user)) -> dict:
    """Distinct agency / NAICS / set-aside dropdown options (RBAC-scoped)."""
    org, viewer, is_admin = _org_scope(current_user)
    return get_crm_store().facet_values(org, viewer_id=viewer, is_admin=is_admin)


@router.get("/posted-dates")
def opportunity_posted_dates(
    filters: dict = Depends(_list_filters),
    current_user: dict = Depends(get_current_user),
) -> dict:
    """Distinct posted dates across the active filter — for the calendar dots across all pages."""
    org, viewer, is_admin = _org_scope(current_user)
    return {"dates": get_crm_store().posted_dates(org, viewer_id=viewer, is_admin=is_admin, **filters)}


class AssignRequest(BaseModel):
    user_ids: list[str]


@router.post("/{opportunity_id}/assign")
def assign_opportunity(
    opportunity_id: str,
    req: AssignRequest,
    current_user: dict = Depends(get_current_user),
) -> dict:
    """Assign an opportunity to members (by user id). Admin only. Empty list = unassign.

    Emails each NEWLY-added member (not those already assigned) that it's now theirs.
    """
    if current_user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Only an admin can assign opportunities")
    crm = get_crm_store()
    organization_id = str(current_user["organization_id"])
    opp = crm.get_opportunity(opportunity_id, organization_id)
    if opp is None:
        raise HTTPException(status_code=404, detail="Opportunity not found")

    previous = set(opp.get("assigned_to") or [])
    crm.set_assignment(opportunity_id, organization_id, req.user_ids)

    new_ids = [u for u in req.user_ids if u not in previous]
    if new_ids:
        from tasks.notify_tasks import notify_assignment_task  # lazy: avoid task import cycle

        assigned_by = (
            f"{current_user.get('firstName', '')} {current_user.get('lastName', '')}".strip()
            or current_user.get("email")
        )
        notify_assignment_task.delay(
            new_ids, opp.get("title") or "an opportunity", opp.get("link"), assigned_by
        )
    return {"opportunity_id": opportunity_id, "assigned_to": req.user_ids, "notified": len(new_ids)}


@router.post("/analyze/run")
def run_analyst(current_user: dict = Depends(get_current_user)) -> dict:
    """Phase 1 — kick off the Analyst on this org's unanalyzed opportunities (Celery)."""
    task = run_analyst_batch.delay(str(current_user["organization_id"]))
    return {"task_id": task.id}


class AnalyzeSelectedRequest(BaseModel):
    ids: list[str]


@router.post("/analyze/selected")
def run_analyst_selected(
    req: AnalyzeSelectedRequest, current_user: dict = Depends(get_current_user)
) -> dict:
    """Analyze ONLY the opportunities the user hand-picked from the SAM.gov pull.

    Each id is verified to belong to the caller's org before it's dispatched, so a
    user can't queue analysis on another org's records.
    """
    crm = get_crm_store()
    organization_id = str(current_user["organization_id"])
    started = 0
    for oid in req.ids:
        opp = crm.get_opportunity(oid, organization_id)
        if opp is not None:
            analyze_opportunity_task.delay(opp)
            started += 1
    return {"started": started, "requested": len(req.ids)}


@router.get("/{opportunity_id}/sharepoint-files")
def sharepoint_files(opportunity_id: str, current_user: dict = Depends(get_current_user)) -> dict:
    """List the Bid folder's contents LIVE from SharePoint (grouped client-side by subfolder).

    This is the read half of the two-way sync: a file a human drops into the SharePoint
    folder shows up here automatically. Returns {connected, folder, files:[…]}.
    """
    crm = get_crm_store()
    org = str(current_user["organization_id"])
    opp = crm.get_opportunity(opportunity_id, org)
    if opp is None:
        raise HTTPException(status_code=404, detail="Opportunity not found")
    folder = opp.get("sharepoint_folder")
    if not folder:
        return {"connected": False, "folder": None, "files": []}
    from utils.sharepoint_writer import (
        SharePointNotConnectedError, SharePointReadError, list_bid_documents,
    )

    try:
        files = list_bid_documents(org, folder)
    except SharePointNotConnectedError:
        # A Bid folder exists but SharePoint has since been disconnected — don't pretend "empty".
        return {"connected": False, "folder": folder, "files": [],
                "error": "SharePoint is disconnected — reconnect it to see this folder's files."}
    except SharePointReadError as exc:
        return {"connected": True, "folder": folder, "files": [], "error": str(exc)}
    except Exception as exc:  # noqa: BLE001 — surface as empty, don't 500 the detail view
        logger.warning("Live SharePoint listing failed for opp %s: %s", opportunity_id, exc)
        return {"connected": True, "folder": folder, "files": [], "error": "Couldn't read the SharePoint folder just now."}
    return {"connected": True, "folder": folder, "files": files}


class SetDecisionRequest(BaseModel):
    decision: str  # "Bid" | "Watch" | "No-Bid"


@router.post("/{opportunity_id}/decision")
def set_decision(
    opportunity_id: str,
    req: SetDecisionRequest,
    current_user: dict = Depends(get_current_user),
) -> dict:
    """Human override of the Analyst's verdict — flip an opportunity between
    Bid / Watch / No-Bid. Bid enables the capture flow; No-Bid/Watch disable it."""
    decision = req.decision.strip()
    if decision not in ("Bid", "Watch", "No-Bid"):
        raise HTTPException(status_code=400, detail="decision must be Bid, Watch, or No-Bid")
    crm = get_crm_store()
    organization_id = str(current_user["organization_id"])
    ok = crm.set_decision(opportunity_id, organization_id, decision)
    if not ok:
        raise HTTPException(status_code=404, detail="Opportunity not found")

    # The moment an opportunity becomes a Bid, provision its SharePoint folder tree
    # (idempotent + a graceful no-op if SharePoint isn't connected). Non-blocking.
    if decision == "Bid":
        try:
            from tasks.sharepoint_tasks import provision_bid_folders_task

            provision_bid_folders_task.delay(organization_id, opportunity_id)
        except Exception as exc:  # noqa: BLE001 — never fail the decision on dispatch trouble
            logger.warning("Could not dispatch Bid folder provisioning for %s: %s", opportunity_id, exc)

    return {"opportunity_id": opportunity_id, "bid_decision": decision, "overridden": True}


@router.post("/{opportunity_id}/approve-capture")
def approve_capture(opportunity_id: str, current_user: dict = Depends(get_current_user)) -> dict:
    """Human approves ONE opportunity → immediately runs the Capture agent for that one.

    Marks it approved (the gate), then fires the Capture agent for just this opportunity.
    The CRM contact search runs against the APPROVING employee's own network (their email
    from the JWT). Other opportunities are untouched.
    """
    crm = get_crm_store()
    opp = crm.get_opportunity(opportunity_id, str(current_user["organization_id"]))
    if opp is None:
        raise HTTPException(status_code=404, detail="Opportunity not found")

    crm.mark_capture_approved(opportunity_id)
    opp["capture_approved"] = True
    employee_email = current_user["email"].lower()
    # In parallel: Capture agent (strategy + deliverables)  ||  CRM contact search. Both are
    # owner-scoped to the approving employee (SharePoint past-performance + contact network).
    task = group(
        capture_task.s(opp, employee_email),
        recommend_contacts_task.s(opp, employee_email),
    ).apply_async()
    return {
        "opportunity_id": opportunity_id,
        "capture_approved": True,
        "capture_started": True,
        "task_id": task.id,
    }


@router.post("/{opportunity_id}/outreach")
def run_outreach(opportunity_id: str, current_user: dict = Depends(get_current_user)) -> dict:
    """Run the Mail Agent over this opportunity's recommended contacts.

    Generates one outreach draft per emailable contact (in parallel) and stores
    them on the opportunity. The UI polls `outreach_drafted_at`, then renders each
    draft as a mail artifact with a Send button. Nothing is sent here.
    The acting employee is taken from the JWT (NOT the request body) and scopes the
    agent's SharePoint search to documents that employee may read — so the RBAC
    filter can't be bypassed by spoofing an email.
    """
    crm = get_crm_store()
    opp = crm.get_opportunity(opportunity_id, str(current_user["organization_id"]))
    if opp is None:
        raise HTTPException(status_code=404, detail="Opportunity not found")

    emailable = [c for c in (opp.get("recommended_contacts") or []) if c.get("email")]
    employee_email = current_user["email"].lower()
    task = draft_outreach_task.delay(opp, employee_email=employee_email)
    return {
        "opportunity_id": opportunity_id,
        "outreach_started": True,
        "contacts": len(emailable),
        "task_id": task.id,
    }


class OutreachOneRequest(BaseModel):
    email: str


@router.post("/{opportunity_id}/outreach/one")
def run_outreach_one(
    opportunity_id: str,
    req: OutreachOneRequest,
    current_user: dict = Depends(get_current_user),
) -> dict:
    """Regenerate the outreach draft for ONE contact on this opportunity."""
    crm = get_crm_store()
    opp = crm.get_opportunity(opportunity_id, str(current_user["organization_id"]))
    if opp is None:
        raise HTTPException(status_code=404, detail="Opportunity not found")

    target = req.email.strip().lower()
    contact = next(
        (c for c in (opp.get("recommended_contacts") or [])
         if (c.get("email") or "").lower() == target),
        None,
    )
    if contact is None:
        raise HTTPException(status_code=404, detail="Contact not found on this opportunity")

    task = draft_one_outreach_task.delay(opp, contact, employee_email=current_user["email"].lower())
    return {"opportunity_id": opportunity_id, "email": req.email, "task_id": task.id, "started": True}


@router.post("/capture/run")
def run_capture(current_user: dict = Depends(get_current_user)) -> dict:
    """Kick off the capture pipeline for this org's approved, not-yet-captured opportunities."""
    task = run_capture_batch.delay(str(current_user["organization_id"]))
    return {"task_id": task.id}


# NOTE: declared LAST so the single-segment path param doesn't shadow the static GET routes
# above (/page, /counts, /facets, /posted-dates) — FastAPI matches in declaration order.
@router.get("/{opportunity_id}")
def get_opportunity_detail(
    opportunity_id: str, current_user: dict = Depends(get_current_user)
) -> dict:
    """The FULL enriched opportunity (documents/calls/tasks + heavy fields) for the detail pane —
    fetched only when a row is opened, so the list stays slim."""
    crm = get_crm_store()
    org = str(current_user["organization_id"])
    opp = crm.get_opportunity_enriched(opportunity_id, org)
    if opp is None:
        raise HTTPException(status_code=404, detail="Opportunity not found")
    # Member-visibility parity: a non-admin can't open an opp assigned away from them.
    if current_user.get("role") != "admin":
        assigned = opp.get("assigned_to") or []
        if assigned and str(current_user["_id"]) not in assigned:
            raise HTTPException(status_code=404, detail="Opportunity not found")
    return opp
