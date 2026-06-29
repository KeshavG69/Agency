"""Mail Agent task — generate the outreach drafts for one opportunity.

Runs the Mail Agent over the opportunity's recommended_contacts (one agent per
email address, capped at 15 concurrent) and stores the resulting MailDraft JSON
on the opportunity. The UI renders each draft as a mail artifact with a Send
button; nothing is sent here — sending happens only on the human's click.
"""
import logging

from agent.mail_agent import draft_outreach, draft_outreach_batch
from app.worker import celery_app
from client.crm_store import get_crm_store

logger = logging.getLogger(__name__)


@celery_app.task(bind=True, max_retries=1, default_retry_delay=15)
def draft_outreach_task(self, opp: dict, employee_email: str | None = None) -> dict:
    """Draft outreach emails for an opportunity's recommended contacts.

    `employee_email` (the acting rep, from the request payload) RBAC-scopes the Mail
    agent's SharePoint search to documents that employee may read.
    """
    crm = get_crm_store()
    contacts = [c for c in (opp.get("recommended_contacts") or []) if c.get("email")]
    if not contacts:
        crm.set_outreach_drafts(opp["id"], [])  # mark 'generated, none to send'
        logger.info("No emailable contacts for %s — no drafts.", opp.get("id"))
        return {"id": opp["id"], "drafts": 0}

    try:
        drafts = draft_outreach_batch(
            opp, contacts, proposal=opp.get("description"),
            limit=15, employee_email=employee_email,
        )
    except Exception as exc:  # transient LLM / tool errors -> one retry
        logger.warning("Outreach drafting failed for %s: %s", opp.get("id"), exc)
        raise self.retry(exc=exc)

    out = [d.model_dump() for d in drafts if d is not None]
    crm.set_outreach_drafts(opp["id"], out)
    # Log each draft for collision detection (best-effort).
    if employee_email and opp.get("organization_id"):
        for d in out:
            try:
                crm.log_outreach(
                    str(opp["organization_id"]), d.get("to"), employee_email, "drafted",
                    opp.get("id"), opp.get("title"),
                )
            except Exception:  # noqa: BLE001
                pass
    logger.info("Drafted %d/%d outreach emails for %s", len(out), len(contacts), opp.get("id"))
    return {"id": opp["id"], "drafts": len(out)}


@celery_app.task(bind=True, max_retries=1, default_retry_delay=15)
def draft_one_outreach_task(self, opp: dict, contact: dict, employee_email: str | None = None) -> dict:
    """Regenerate the outreach email for ONE contact and replace just that draft.

    `employee_email` (the acting rep, from the JWT) RBAC-scopes the SharePoint search.
    """
    crm = get_crm_store()
    try:
        draft = draft_outreach(
            opp, contact, proposal=opp.get("description"), employee_email=employee_email,
        )
    except Exception as exc:
        logger.warning("Single-draft failed for %s: %s", contact.get("email"), exc)
        raise self.retry(exc=exc)

    crm.upsert_outreach_draft(opp["id"], draft.model_dump())
    if employee_email and opp.get("organization_id"):
        try:
            crm.log_outreach(
                str(opp["organization_id"]), draft.to, employee_email, "drafted",
                opp.get("id"), opp.get("title"),
            )
        except Exception:  # noqa: BLE001
            pass
    logger.info("Re-drafted outreach for %s on %s", draft.to, opp.get("id"))
    return {"id": opp["id"], "to": draft.to}
