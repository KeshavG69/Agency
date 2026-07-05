"""Mail triage Celery tasks — the relevance filter + card creation for the
OUTLOOK_MESSAGE_TRIGGER webhook, and generating a suggested reply on request.

Every incoming mail is fetched and checked; only mail from a KNOWN CONTACT on an ACTIVE
Bid becomes a triage card — everything else is dropped silently (see the
mail-triage-followups design memory). Reply drafting is draft-only: this task only
generates suggested TEXT stored on the card, never a real Outlook draft or a send.
"""
import logging

from app.worker import celery_app
from client.crm_store import get_crm_store

logger = logging.getLogger(__name__)


@celery_app.task(bind=True, max_retries=2, default_retry_delay=15)
def process_outlook_message_task(self, employee_email: str, message_id: str) -> dict:
    """Fetch a newly-arrived Outlook message and, if its sender is a known contact on an
    ACTIVE Bid, save it as a triage card. Every other incoming mail is dropped silently —
    this IS the relevance filter."""
    from auth.crud import UserCRUD
    from utils.composio_utils import fetch_outlook_message

    user = UserCRUD.get_user_by_email(employee_email)
    if not user or not user.organization_id:
        logger.warning(
            "Mail triage: no org found for %s — dropping message %s", employee_email, message_id
        )
        return {"kept": False, "reason": "no_organization"}

    try:
        msg = fetch_outlook_message(message_id, user_id=employee_email)
    except Exception as exc:
        logger.warning(
            "Mail triage: fetching message %s for %s failed: %s", message_id, employee_email, exc
        )
        raise self.retry(exc=exc)

    crm = get_crm_store()
    opp = crm.find_active_bid_by_contact(user.organization_id, msg["sender_email"])
    if not opp:
        return {"kept": False, "reason": "not_a_known_bid_contact"}

    try:
        card_id = crm.create_mail_triage_card(
            organization_id=user.organization_id,
            employee_email=employee_email,
            opportunity_id=opp["id"],
            message_id=msg["message_id"],
            sender_email=msg["sender_email"],
            sender_name=msg["sender_name"],
            subject=msg["subject"],
            snippet=msg["snippet"],
            received_at=msg["received_at"],
            conversation_id=msg["conversation_id"],
            web_link=msg.get("web_link"),
        )
    except Exception as exc:  # noqa: BLE001 — e.g. a transient Mongo blip; retry like the fetch above
        logger.warning(
            "Mail triage: saving the triage card failed for %s/%s: %s",
            employee_email, message_id, exc,
        )
        raise self.retry(exc=exc)
    if card_id:
        logger.info(
            "Mail triage: kept message from %s for opp %s (employee %s)",
            msg["sender_email"], opp["id"], employee_email,
        )
    return {"kept": bool(card_id), "opportunity_id": opp["id"], "card_id": card_id}


@celery_app.task(bind=True, max_retries=1, default_retry_delay=15)
def draft_triage_reply_task(self, card_id: str, employee_email: str) -> dict:
    """Generate a suggested reply for one triage card — draft-only, stored on the card.
    It only becomes a real Outlook draft on a later, separate, explicit human action."""
    from agent.mail_agent import draft_reply

    crm = get_crm_store()
    card = crm.get_mail_triage_card(card_id, employee_email)
    if not card:
        return {"drafted": False, "reason": "not_found"}
    opp = crm.get_opportunity(card["opportunity_id"], card["organization_id"])
    if not opp:
        return {"drafted": False, "reason": "opportunity_not_found"}

    try:
        draft = draft_reply(opp, card, user_id=employee_email, employee_email=employee_email)
    except Exception as exc:
        logger.warning("Mail triage: reply draft failed for card %s: %s", card_id, exc)
        if self.request.retries >= self.max_retries:
            # Retries exhausted — record the failure ON THE CARD so the frontend's poll loop
            # can show it, instead of polling forever for a suggested_reply that will never
            # arrive (e.g. the LLM never produced valid ReplyDraft JSON).
            crm.update_mail_triage_card(card_id, employee_email, reply_error=str(exc))
            return {"drafted": False, "reason": "generation_failed"}
        raise self.retry(exc=exc)

    crm.update_mail_triage_card(
        card_id, employee_email, suggested_reply=draft.comment, reply_error=None
    )
    return {"drafted": True, "card_id": card_id}
