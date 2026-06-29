"""Member notification emails (sent in the background so endpoints stay snappy)."""
import logging

from app.worker import celery_app
from auth.database import get_mongodb_client
from client.email_service import EmailService

logger = logging.getLogger(__name__)


@celery_app.task(name="notify.assignment")
def notify_assignment_task(
    user_ids: list[str],
    opportunity_title: str,
    opportunity_link: str | None = None,
    assigned_by: str | None = None,
) -> dict:
    """Email each member (by user id) that an opportunity was assigned to them."""
    db = get_mongodb_client().get_database()
    svc = EmailService()
    sent = 0
    for uid in user_ids:
        user = db["users"].find_one({"_id": uid})
        if not user or not user.get("email"):
            continue
        try:
            svc.send_assignment_email(
                to_email=user["email"],
                user_name=(user.get("firstName") or user.get("email")),
                opportunity_title=opportunity_title,
                opportunity_link=opportunity_link,
                assigned_by=assigned_by,
            )
            sent += 1
        except Exception as exc:  # noqa: BLE001 — one bad address must not sink the rest
            logger.warning("assignment email to %s failed: %s", uid, exc)
    logger.info("notify_assignment: emailed %d/%d members", sent, len(user_ids))
    return {"sent": sent, "requested": len(user_ids)}
