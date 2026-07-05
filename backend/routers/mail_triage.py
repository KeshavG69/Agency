"""Mail triage — Dashboard cards for incoming mail tied to an active Bid.

Read-only relevance surfacing + a human-gated draft-reply loop. Nothing here ever sends
mail: `draft-reply` only generates suggested TEXT (stored on the card), and
`create-outlook-draft` only creates a DRAFT sitting in the employee's OWN Outlook mailbox
for them to review and send — mirroring (and going one step more cautious than) the
draft-only discipline of the outreach Mail Agent.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from auth.dependencies import get_current_user
from client.crm_store import get_crm_store

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/mail-triage", tags=["mail-triage"])


@router.get("")
def list_triage(current_user: dict = Depends(get_current_user)) -> dict:
    """This employee's triage cards (their OWN inbox only), newest first, excluding
    dismissed ones."""
    crm = get_crm_store()
    cards = crm.list_mail_triage(
        str(current_user["organization_id"]), current_user["email"].lower()
    )
    return {"cards": cards}


@router.post("/{card_id}/read")
def mark_read(card_id: str, current_user: dict = Depends(get_current_user)) -> dict:
    crm = get_crm_store()
    ok = crm.update_mail_triage_card(card_id, current_user["email"].lower(), status="read")
    if not ok:
        raise HTTPException(status_code=404, detail="Triage card not found.")
    return {"updated": True}


@router.post("/{card_id}/dismiss")
def dismiss(card_id: str, current_user: dict = Depends(get_current_user)) -> dict:
    crm = get_crm_store()
    ok = crm.update_mail_triage_card(card_id, current_user["email"].lower(), status="dismissed")
    if not ok:
        raise HTTPException(status_code=404, detail="Triage card not found.")
    return {"updated": True}


@router.post("/{card_id}/draft-reply")
def draft_reply_endpoint(card_id: str, current_user: dict = Depends(get_current_user)) -> dict:
    """Kick off suggested-reply generation (async — poll GET '' and read `suggested_reply`
    on this card once it's filled in)."""
    crm = get_crm_store()
    email = current_user["email"].lower()
    card = crm.get_mail_triage_card(card_id, email)
    if not card:
        raise HTTPException(status_code=404, detail="Triage card not found.")
    from tasks.mail_triage_tasks import draft_triage_reply_task

    task = draft_triage_reply_task.delay(card_id, email)
    return {"drafting_started": True, "task_id": task.id}


class CreateOutlookDraftRequest(BaseModel):
    comment: str  # the (possibly human-edited) reply text


@router.post("/{card_id}/create-outlook-draft")
def create_outlook_draft(
    card_id: str, req: CreateOutlookDraftRequest, current_user: dict = Depends(get_current_user)
) -> dict:
    """Create a REAL threaded draft reply in the employee's OWN Outlook mailbox — sits in
    their Drafts folder for them to review and send. We never send it ourselves."""
    if not req.comment.strip():
        raise HTTPException(status_code=400, detail="A reply body is required.")
    crm = get_crm_store()
    email = current_user["email"].lower()
    card = crm.get_mail_triage_card(card_id, email)
    if not card:
        raise HTTPException(status_code=404, detail="Triage card not found.")

    from utils.composio_utils import create_outlook_draft_reply

    try:
        result = create_outlook_draft_reply(card["message_id"], req.comment, user_id=email)
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "Creating Outlook draft reply failed for card %s: %s", card_id, exc, exc_info=True
        )
        raise HTTPException(status_code=502, detail=f"Couldn't create the draft in Outlook: {exc}")

    crm.update_mail_triage_card(card_id, email, status="replied", suggested_reply=req.comment)
    return {"created": True, "web_link": result.get("web_link")}
