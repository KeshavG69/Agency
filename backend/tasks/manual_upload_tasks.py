"""Background processing for a manually-uploaded opportunity.

The HTTP handler (routers.opportunities.create_manual_opportunity) has already:
  1. created the Opportunity row,
  2. uploaded each file to iDrive e2 and recorded a document pointer per file.

This task does the slow part off the request path: parse each stored file with
LiteParse, build the opportunity's document_text with the stuff/map-reduce digest
(small model), persist it, then kick the EXISTING Analyst batch — which analyzes
the manual opp unchanged (it has no analyzed_at yet).

Why parse from the stored URL rather than a temp file: web and worker run in
SEPARATE containers (no shared filesystem), so the bytes must reach the worker via
shared storage (iDrive). parse_document() accepts the presigned URL directly.
"""
import logging

from app.worker import celery_app
from client.crm_store import get_crm_store
from utils.doc_digest import digest_documents
from utils.doc_parse import parse_document

logger = logging.getLogger(__name__)


@celery_app.task(bind=True, max_retries=2, default_retry_delay=15)
def process_manual_upload_task(self, opportunity_id: str, organization_id: str) -> dict:
    """Parse the opp's uploaded docs -> digest -> store document_text -> run Analyst."""
    crm = get_crm_store()
    try:
        docs = crm.list_documents(opportunity_id)

        texts: list[str] = []
        first_url: str | None = None
        for d in docs:
            url = d.get("url")
            if not url:
                continue
            first_url = first_url or url
            try:
                text = parse_document(url)  # LiteParse downloads + parses from the presigned URL
            except Exception as exc:  # noqa: BLE001 — one bad doc must not fail the batch
                logger.warning("parse_document failed for doc %s (opp %s): %s", d.get("id"), opportunity_id, exc)
                text = None
            if text:
                texts.append(f"===== FILE: {d.get('title') or 'document'} =====\n{text}")

        document_text = digest_documents(texts)
        crm.set_document_text(opportunity_id, organization_id, document_text, document_url=first_url)
        logger.info(
            "Manual opp %s: parsed %d/%d docs -> %d chars document_text",
            opportunity_id, len(texts), len(docs), len(document_text),
        )

        # Kick the existing per-org Analyst fan-out; the manual opp qualifies (no analyzed_at).
        # Lazy import: app.worker imports this module, so a top-level import of analyst_tasks
        # (which also imports app.worker) would be a circular import at load time.
        from tasks.analyst_tasks import run_analyst_batch

        run_analyst_batch.delay(organization_id)
        return {
            "opportunity_id": opportunity_id,
            "documents": len(docs),
            "parsed": len(texts),
            "document_text_chars": len(document_text),
        }
    except Exception as exc:  # noqa: BLE001 — retry transient failures; clear the flag on terminal.
        if self.request.retries >= self.max_retries:
            # The Analyst will never run, so apply_verdict can't clear the flag — clear it here
            # with the error so the opp exits "Ingesting" instead of hanging there forever.
            logger.error("Manual ingest permanently failed for opp %s: %s", opportunity_id, exc)
            crm.set_ingesting(opportunity_id, organization_id, False, error=str(exc))
            raise
        raise self.retry(exc=exc)
