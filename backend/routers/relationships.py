"""Relationships router — the relationship engine's nudges, and the human decision on them.

The nightly sweep (tasks/relationship_tasks.py) produces the nudges; the action planner turns
open ones into Today cards. This router serves the draft behind a card and takes the one human
action that matters: approve or dismiss.

SCOPING mirrors the rest of the app — `organization_id` comes from the JWT, never the client.

WHAT APPROVE DOES HERE. Following the product's hard rule (the AI never sends on its own) and
the mail-triage precedent, approving marks the nudge approved and closes its Today card; the
actual send is a human-initiated step. How the tap physically sends (create an Outlook draft
vs. send immediately) is wired with the Today card — this endpoint records the decision and
retires the card either way.
"""
import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from auth.dependencies import get_current_user
from client.crm_store import get_crm_store
from client.nudge_store import APPROVED, DISMISSED, get_nudge_store

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/relationships", tags=["relationships"])


def _org(current_user: dict) -> str:
    org = str(current_user.get("organization_id") or "").strip()
    if not org:
        raise HTTPException(status_code=403, detail="No organization on this account")
    return org


@router.get("/nudges/{nudge_id}")
def get_nudge(nudge_id: str, current_user: dict = Depends(get_current_user)) -> dict:
    """The drafted outreach + its evidence behind one Today card."""
    nudge = get_nudge_store().get(nudge_id, _org(current_user))
    if not nudge:
        raise HTTPException(status_code=404, detail="Nudge not found")
    return nudge


class DecideNudgeRequest(BaseModel):
    action: str  # "approve" | "dismiss"


@router.post("/nudges/{nudge_id}/decide")
def decide_nudge(
    nudge_id: str, req: DecideNudgeRequest, current_user: dict = Depends(get_current_user)
) -> dict:
    """Approve or dismiss one nudge. Terminal: the sweep never reopens a decided nudge, and
    the paired Today card is closed so it does not linger after the rep has acted."""
    org = _org(current_user)
    action = (req.action or "").strip().lower()
    status = {"approve": APPROVED, "dismiss": DISMISSED}.get(action)
    if not status:
        raise HTTPException(status_code=422, detail="action must be 'approve' or 'dismiss'")

    updated = get_nudge_store().decide(nudge_id, org, status)
    if not updated:
        raise HTTPException(status_code=404, detail="Nudge not found")

    # An APPROVED nudge means the rep just sent this message. Record the touch on the graph
    # immediately: it keeps "quiet N days" honest the next morning instead of repeating a
    # staleness the rep already fixed, and it advances the cycle so this contact can become
    # nudge-able again later (the dedupe key is built from `last_contact`).
    if status == APPROVED:
        try:
            from datetime import datetime, timezone

            from client.graph_store import record_touch

            record_touch(
                (updated.get("owner_email") or current_user["email"]).lower(), org,
                updated.get("contact_email") or "",
                when=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                subject=updated.get("subject"),
            )
        except Exception as exc:  # noqa: BLE001 — the send already happened; never fail on this
            logger.warning("decide_nudge: recording the touch failed for %s: %s", nudge_id, exc)

    # Retire the Today card that pointed at this nudge. Approve -> done, dismiss -> dismissed.
    try:
        get_crm_store().close_action_by_ref(
            org, nudge_id, status="done" if status == APPROVED else "dismissed"
        )
    except Exception as exc:  # noqa: BLE001 — the nudge decision is already committed
        logger.warning("decide_nudge: closing the paired action failed for %s: %s", nudge_id, exc)

    return {"nudge": updated, "decided": action}
