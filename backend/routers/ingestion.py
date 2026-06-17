"""Opportunity ingestion endpoints."""
import tempfile

from fastapi import APIRouter, File, UploadFile

from client.crm_store import get_crm_store
from utils.excel_ingest import ingest_excel

router = APIRouter(prefix="/api/ingestion", tags=["ingestion"])


@router.post("/excel")
async def ingest_excel_file(file: UploadFile = File(...)) -> dict:
    """Upload an .xlsx of opportunities; normalize and upsert into EspoCRM."""
    with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=True) as tmp:
        tmp.write(await file.read())
        tmp.flush()
        opportunities = ingest_excel(tmp.name)

    crm = get_crm_store()
    created = updated = 0
    results = []
    for opp in opportunities:
        action, crm_id = crm.upsert_opportunity(opp)
        created += action == "created"
        updated += action == "updated"
        results.append({"id": crm_id, "action": action, "title": opp.title})

    return {
        "parsed": len(opportunities),
        "created": created,
        "updated": updated,
        "crm_total": crm.count(),
        "results": results,
    }
