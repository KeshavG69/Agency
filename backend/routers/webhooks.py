"""Composio webhook receiver — ONE endpoint for every Composio trigger in this project.

The webhook URL is configured ONCE, project-wide, in the Composio dashboard (Project
Settings -> Webhook) — it is NOT per-trigger, so every trigger type (today: mail triage's
OUTLOOK_MESSAGE_TRIGGER; future: others) lands here and is routed by `trigger_slug` in the
verified payload. Unrecognized trigger types are acknowledged and ignored so adding a new
trigger elsewhere never requires touching this endpoint's contract.
"""
from __future__ import annotations

import logging

from composio import exceptions as composio_exceptions
from fastapi import APIRouter, Header, HTTPException, Request

from app.settings import settings
from utils.composio_utils import MAIL_TRIAGE_TRIGGER_SLUG, get_composio_client

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/webhooks", tags=["webhooks"])


@router.post("/composio")
async def composio_webhook(
    request: Request,
    webhook_id: str = Header(..., alias="webhook-id"),
    webhook_timestamp: str = Header(..., alias="webhook-timestamp"),
    webhook_signature: str = Header(..., alias="webhook-signature"),
) -> dict:
    """Verify + route one Composio trigger event. Always returns 200 once verified and
    routed, even if the event itself turns out to be irrelevant (e.g. not a known Bid
    contact) — 'irrelevant' is a valid, expected outcome, not a webhook failure."""
    if not settings.COMPOSIO_WEBHOOK_SECRET:
        # Fail closed: with no secret configured, verification can't mean anything —
        # refuse rather than silently accept unverified events.
        logger.error("Composio webhook received but COMPOSIO_WEBHOOK_SECRET is not set.")
        raise HTTPException(status_code=500, detail="Webhook verification is not configured.")

    raw_body = (await request.body()).decode("utf-8")
    try:
        result = get_composio_client().triggers.verify_webhook(
            id=webhook_id,
            payload=raw_body,
            secret=settings.COMPOSIO_WEBHOOK_SECRET,
            signature=webhook_signature,
            timestamp=webhook_timestamp,
        )
    except composio_exceptions.WebhookSignatureVerificationError:
        raise HTTPException(status_code=401, detail="Invalid webhook signature.")
    except composio_exceptions.WebhookPayloadError as exc:
        raise HTTPException(status_code=400, detail=f"Malformed webhook payload: {exc}")

    event = result["payload"]
    slug = event.get("trigger_slug")

    if slug == MAIL_TRIAGE_TRIGGER_SLUG:
        employee_email = (event.get("user_id") or "").strip().lower()
        message_id = (event.get("payload") or {}).get("id")
        if employee_email and message_id:
            from tasks.mail_triage_tasks import process_outlook_message_task

            process_outlook_message_task.delay(employee_email, message_id)
        else:
            logger.warning("OUTLOOK_MESSAGE_TRIGGER event missing user_id/message id: %s", event)
    # else: some other/future trigger type landed here — acknowledged, no handler yet.

    return {"received": True}
