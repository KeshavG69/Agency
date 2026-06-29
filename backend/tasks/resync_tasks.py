"""Daily resync — refresh each connected employee's Outlook contacts and each org's
SharePoint structure every 24h (Celery beat). Also triggerable per-user/per-org from the
UI "Resync" buttons (those hit the existing composio sync endpoints directly)."""
import logging

from app.worker import celery_app
from auth.database import get_mongodb_client
from utils.composio_utils import connection_status, sharepoint_entity

logger = logging.getLogger(__name__)


@celery_app.task(name="resync.daily")
def daily_resync() -> dict:
    """Re-pull contacts for every employee with Outlook connected, and SharePoint for every
    org with it connected. Connection is checked first so we never retry-storm unconnected
    accounts."""
    db = get_mongodb_client().get_database()
    from tasks.contacts_tasks import sync_outlook_contacts_task  # lazy: avoid import cycle
    from tasks.sharepoint_tasks import sync_sharepoint_structure_task

    contacts = sp = 0
    # Contacts — per employee (their own mailbox), only if Outlook is connected.
    for u in db["users"].find({}, {"_id": 1, "email": 1, "current_organization_id": 1}):
        email = (u.get("email") or "").strip().lower()
        org = u.get("current_organization_id")
        if not email or not org:
            continue
        try:
            if connection_status("outlook", email).get("connected"):
                sync_outlook_contacts_task.delay(email, str(org))
                contacts += 1
        except Exception as exc:  # noqa: BLE001
            logger.warning("daily_resync: outlook check failed for %s: %s", email, exc)

    # SharePoint — per org, only if connected (the task also no-ops if not).
    for o in db["organizations"].find({}, {"_id": 1}):
        oid = str(o["_id"])
        try:
            if connection_status("sharepoint", sharepoint_entity(oid)).get("connected"):
                sync_sharepoint_structure_task.delay(oid)
                sp += 1
        except Exception as exc:  # noqa: BLE001
            logger.warning("daily_resync: sharepoint check failed for %s: %s", oid, exc)

    logger.info("daily_resync: dispatched contacts=%d sharepoint=%d", contacts, sp)
    return {"contacts_dispatched": contacts, "sharepoint_dispatched": sp}
