"""Opportunity ingestion endpoints."""
import asyncio
import tempfile

from fastapi import APIRouter, Depends, File, UploadFile

from auth.dependencies import get_current_user
from client.crm_store import get_crm_store
from tasks.analyst_tasks import run_analyst_batch
from tasks.sam_radar_tasks import scan_org_sam
from utils.doc_parse import parse_document
from utils.excel_ingest import ingest_excel

router = APIRouter(prefix="/api/ingestion", tags=["ingestion"])


@router.post("/excel")
async def ingest_excel_file(
    file: UploadFile = File(...),
    current_user: dict = Depends(get_current_user),
) -> dict:
    """Upload an .xlsx of opportunities; normalize, parse each PDF, upsert, run Analyst.

    Every opportunity is tagged with the uploading employee's organization, and the
    Analyst batch is scoped to that org.
    """
    organization_id = str(current_user["organization_id"])
    with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=True) as tmp:
        tmp.write(await file.read())
        tmp.flush()
        opportunities = ingest_excel(tmp.name)

    # Parse each opportunity's solicitation document (PDF/PWS) -> text via LiteParse,
    # so every downstream agent is grounded in the real document. Best-effort + in a
    # worker thread so the parse (download + OCR) doesn't block the event loop.
    docs_parsed = 0
    for opp in opportunities:
        if opp.document_url:
            text = await asyncio.to_thread(parse_document, opp.document_url)
            if text:
                opp.document_text = text
                docs_parsed += 1

    crm = get_crm_store()
    created = updated = 0
    results = []
    for opp in opportunities:
        action, crm_id = crm.upsert_opportunity(opp, organization_id)
        created += action == "created"
        updated += action == "updated"
        results.append({"id": crm_id, "action": action, "title": opp.title})

    # Auto-kick the Analyst batch for THIS org — it picks up everything still
    # unanalyzed (including what we just upserted). Non-blocking.
    analysis_started = False
    if created or updated:
        run_analyst_batch.delay(organization_id)
        analysis_started = True

    return {
        "parsed": len(opportunities),
        "documents_parsed": docs_parsed,
        "created": created,
        "updated": updated,
        "crm_total": crm.count(organization_id),
        "analysis_started": analysis_started,
        "results": results,
    }


@router.post("/sam/scan")
def trigger_sam_scan(
    lookback_days: int = 1,
    current_user: dict = Depends(get_current_user),
) -> dict:
    """On-demand SAM.gov pull for THIS org (the scheduled daily scan, run now).

    Pulls recently-posted notices for the org's NAICS, ingests them, and kicks the
    Analyst. `lookback_days` widens the posted-date window (1 = today, e.g. 30 = backfill).
    """
    organization_id = str(current_user["organization_id"])
    task = scan_org_sam.delay(organization_id, lookback_days=lookback_days)
    return {"organization_id": organization_id, "scan_started": True, "task_id": task.id}
