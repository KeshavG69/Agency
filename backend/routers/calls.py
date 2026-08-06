"""Call plan — the consolidated BD call sheet across the whole pipeline.

The Analyst produces a per-opportunity call action (contact + talking point) for each Bid;
this rolls them all up into one view, and lets the user mark a call done / dismissed.
"""
import re

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from auth.dependencies import get_current_user
from client.crm_store import get_crm_store
from client.task_store import PRIORITY, get_task_store

router = APIRouter(prefix="/api/calls", tags=["calls"])

_CALL_STATUSES = ("Planned", "Done", "Dismissed")


@router.get("/plan")
def call_plan(current_user: dict = Depends(get_current_user)) -> dict:
    """All planned calls across this org's opportunities (priority-sorted)."""
    crm = get_crm_store()
    return {"calls": crm.call_plan(str(current_user["organization_id"]))}


class CallStatusRequest(BaseModel):
    status: str  # "Planned" | "Done" | "Dismissed"


@router.post("/{call_id}/status")
def set_call_status(
    call_id: str, req: CallStatusRequest, current_user: dict = Depends(get_current_user)
) -> dict:
    """Mark a call Done / Dismissed / Planned. Org-scoped to the call's opportunity."""
    status = req.status.strip()
    if status not in _CALL_STATUSES:
        raise HTTPException(status_code=400, detail=f"status must be one of {_CALL_STATUSES}")
    crm = get_crm_store()
    ok = crm.set_call_status(call_id, str(current_user["organization_id"]), status)
    if not ok:
        raise HTTPException(status_code=404, detail="Call not found")
    return {"call_id": call_id, "status": status}


# --- per-contact call briefs ("how do I talk to this person?") --------------------------
# The Call Plan dialog has one TAB PER CONTACT on a pursuit; each tab is its own brief, run
# on demand when the rep opens it. Every brief is grounded in that contact's WHOLE ORG — the
# agent reads every thread the rep's mailbox holds with anyone at their domain. Keyed per-rep
# because it reads that rep's own mailbox.


def _brief_subject(opportunity_id: str, contact_email: str, rep_email: str) -> str:
    return "::".join(
        (x or "").strip().lower() for x in (opportunity_id, contact_email, rep_email)
    )


class BriefRequest(BaseModel):
    opportunity_id: str
    contact_email: str


@router.post("/brief")
def prep_call(req: BriefRequest, current_user: dict = Depends(get_current_user)) -> dict:
    """Queue the brief for ONE contact on a pursuit — fired when the rep opens that tab.

    Runs in the background (LLM lane): searches the rep's mailbox for every thread with the
    contact's organisation, then writes how to approach them. Returns immediately; the client
    polls GET /brief/{opportunity_id}.
    """
    org = str(current_user["organization_id"])
    rep = current_user["email"].lower()
    contact_email = (req.contact_email or "").strip().lower()
    if "@" not in contact_email:
        raise HTTPException(status_code=400, detail="A contact email is required")
    opp = get_crm_store().get_opportunity(req.opportunity_id, org)
    if not opp:
        raise HTTPException(status_code=404, detail="Opportunity not found")

    task_id = get_task_store().enqueue(
        org, "call_brief", "opportunity",
        _brief_subject(req.opportunity_id, contact_email, rep),
        reason=f"Rep prepping a call with {contact_email} about "
               f"“{opp.get('title') or 'this opportunity'}”",
        priority=PRIORITY["requested"], budget=8,
    )
    # task_id is None when one is already queued — either way a brief is on its way. When it
    # is newly queued, kick it off now rather than waiting up to a minute for the tick (the
    # rep is watching a spinner). run_task_now leases it first, so the tick won't double-run.
    if task_id:
        from tasks.agent_tasks import run_task_now
        run_task_now.delay(task_id)
    return {"opportunity_id": req.opportunity_id, "contact_email": contact_email,
            "queued": task_id is not None, "pending": True}


@router.get("/brief/{opportunity_id}")
def read_call_briefs(
    opportunity_id: str, current_user: dict = Depends(get_current_user)
) -> dict:
    """Every contact-brief this rep holds for one pursuit, plus which contacts are still being
    prepared — one payload for the whole dialog, so the tabs don't each poll separately."""
    org = str(current_user["organization_id"])
    rep = current_user["email"].lower()
    briefs = get_crm_store().list_call_briefs(org, opportunity_id, rep)
    prefix = f"{opportunity_id.strip().lower()}::"
    pending = [
        t["subject"]["id"][len(prefix):].split("::")[0]
        for t in get_task_store().tasks.find({
            "organization_id": org, "kind": "call_brief", "finished_at": None,
            "subject.id": {"$regex": f"^{re.escape(prefix)}"},
        })
        if t.get("subject", {}).get("id", "").endswith(f"::{rep}")
    ]
    return {"opportunity_id": opportunity_id, "briefs": briefs, "pending": pending}
