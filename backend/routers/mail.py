"""Send an outreach email — the human-approved 'Send' action.

The frontend mail artifact posts the (possibly edited) draft here when the user
clicks Send. This is the ONLY place email actually goes out, and only ever in
response to that explicit click — never automatically.
"""
from fastapi import APIRouter, Depends, HTTPException

from auth.dependencies import get_current_user
from models.mail import MailDraft
from utils.composio_utils import send_outlook_email

router = APIRouter(prefix="/api/mail", tags=["mail"])


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

    result = send_outlook_email(draft.outlook_send_args(user_id="me"),
                                user_id=current_user["email"].lower())
    if not result.get("successful", False):
        raise HTTPException(status_code=502, detail=f"Send failed: {result.get('error')}")
    return {"sent": True, "to": draft.to}
