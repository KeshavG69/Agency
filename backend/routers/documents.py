"""Document access — mint a FRESH presigned URL on demand.

Generated docs live in iDrive e2 behind presigned URLs that expire (7 days). Instead of
handing the frontend a link that goes stale, it asks this stable endpoint for a fresh link
each time it previews/downloads. We don't store the object key separately — it's parsed from
the (possibly-expired) stored URL's path, so no migration is needed.
"""
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException

from app.settings import settings
from auth.dependencies import get_current_user
from client.crm_store import get_crm_store
from client.idrive_storage import get_idrive_storage

router = APIRouter(prefix="/api/documents", tags=["documents"])


def _object_key_from_url(url: str) -> str | None:
    """Pull the iDrive object key out of a stored (path-style) presigned URL."""
    try:
        path = urlparse(url).path.lstrip("/")  # "{bucket}/{key}"
    except Exception:  # noqa: BLE001
        return None
    if not path:
        return None
    bucket = settings.IDRIVE_E2_BUCKET
    if bucket and path.startswith(f"{bucket}/"):
        return path[len(bucket) + 1:]
    return path


@router.get("/{document_id}/url")
def fresh_document_url(
    document_id: str, current_user: dict = Depends(get_current_user)
) -> dict:
    """Return a freshly-minted presigned URL for a generated document.

    Org-scoped: the document's opportunity must belong to the caller's org, so one org
    can't fetch another's files. The link is short-lived (1 hour) and re-minted on demand.
    """
    crm = get_crm_store()
    doc = crm.get_document(document_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    # Verify the document's opportunity belongs to this org.
    opp = crm.get_opportunity(doc.get("opportunity_id", ""), str(current_user["organization_id"]))
    if opp is None:
        raise HTTPException(status_code=404, detail="Document not found")
    key = _object_key_from_url(doc.get("url") or "")
    if not key:
        raise HTTPException(status_code=404, detail="Document has no storage reference")
    try:
        url = get_idrive_storage().get_presigned_url(key, expiration=3600)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Could not mint a link: {exc}")
    return {"url": url}
