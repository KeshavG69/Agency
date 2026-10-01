"""Send an outreach email — the human-approved 'Send' action.

The frontend mail artifact posts the (possibly edited) draft here when the user
clicks Send. This is the ONLY place email actually goes out, and only ever in
response to that explicit click — never automatically.
"""
import re

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from auth.dependencies import get_current_user
from client.crm_store import get_crm_store
from models.mail import MailDraft
from utils.composio_utils import send_outlook_email

router = APIRouter(prefix="/api/mail", tags=["mail"])

# Unfilled template tokens a drafting agent leaves for the human to complete. TARGETED, not
# "any [bracketed] text": a real email can legitimately contain brackets, and blocking a
# legitimate send is its own failure. These are the shapes the agents actually emit.
_UNFILLED_PLACEHOLDER = re.compile(
    r"\[\s*(?:Your|Insert|Recipient|Contact)\b[^\]]*\]"
    r"|\[\s*(?:Name|Title|Company|Company Name|Phone|Date)\s*\]",
    re.IGNORECASE,
)


class CollisionRequest(BaseModel):
    emails: list[str]


@router.post("/collisions")
def outreach_collisions(
    req: CollisionRequest, current_user: dict = Depends(get_current_user)
) -> dict:
    """For each contact email, list OTHER teammates who've drafted/sent to them — so the UI
    can warn 'someone's already talking to this person'. Excludes the caller."""
    crm = get_crm_store()
    data = crm.outreach_collisions(
        str(current_user["organization_id"]), req.emails, current_user["email"].lower()
    )
    return {"collisions": data}


@router.post("/send")
def send_mail(draft: MailDraft, current_user: dict = Depends(get_current_user)) -> dict:
    """Send one outreach email from the acting employee's OWN connected Outlook mailbox.

    Outward action — only ever fired by an explicit human 'Send' click. The sender is
    always the authenticated employee (their Composio Outlook connection), never a
    shared account, so mail goes out from the person who approved it.
    """
    if not draft.to or not draft.to.strip():
        raise HTTPException(status_code=400, detail="A recipient ('to') is required.")
    if not draft.subject.strip() or not draft.body.strip():
        raise HTTPException(status_code=400, detail="Subject and body are required.")
    # The last line of defence against mailing a TEMPLATE to a customer. The drafting agents
    # now sign with the rep's real name, but a placeholder can still arrive — an unresolved
    # name, a model that disobeyed, a hand-edited draft. Every outbound path (outreach, reply,
    # relationship nudge) sends through here, so this one check covers all of them.
    leftover = _UNFILLED_PLACEHOLDER.findall(f"{draft.subject}\n{draft.body}")
    if leftover:
        raise HTTPException(
            status_code=422,
            detail=(f"This email still has an unfilled placeholder ({', '.join(dict.fromkeys(leftover))}). "
                    "Replace it before sending — nothing was sent."),
        )

    result = send_outlook_email(draft.outlook_send_args(user_id="me"),
                                user_id=current_user["email"].lower())
    if not result.get("successful", False):
        raise HTTPException(status_code=502, detail=f"Send failed: {result.get('error')}")
    # Log the outreach for collision detection (best-effort — never fail the send on this).
    try:
        get_crm_store().log_outreach(
            str(current_user["organization_id"]), draft.to, current_user["email"].lower(), "sent",
        )
    except Exception:  # noqa: BLE001
        pass
    return {"sent": True, "to": draft.to}
