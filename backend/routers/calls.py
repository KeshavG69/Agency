"""Call plan — the consolidated BD call sheet across the whole pipeline.

The Analyst produces a per-opportunity call action (contact + talking point) for each Bid;
this rolls them all up into one view, and lets the user mark a call done / dismissed.
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from auth.dependencies import get_current_user
from client.crm_store import get_crm_store

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
