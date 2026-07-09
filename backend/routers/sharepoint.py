"""SharePoint router — serves the document structure graph for the UI."""
import logging

from fastapi import APIRouter, Depends, HTTPException

from auth.dependencies import get_current_user
from client.sharepoint_graph import get_structure, stats

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/sharepoint", tags=["sharepoint"])


@router.get("/graph")
def sharepoint_graph(files: int = 0, current_user: dict = Depends(get_current_user)) -> dict:
    """The SharePoint structure as { nodes, edges }, RBAC-filtered to what the acting
    employee may read. Files are excluded by default (there are hundreds — too dense
    for the graph view); pass ?files=1 to include them."""
    org_id = str(current_user["organization_id"])
    # Admins see the full structure — they connected the library and own the ACL config.
    # RBAC filtering only applies to non-admin employees who may not have access to all folders.
    is_admin = current_user.get("role") == "admin"
    try:
        g = get_structure(org_id, employee_email=None if is_admin else current_user["email"].lower())
    except Exception as e:  # noqa: BLE001
        logger.error("Failed to read SharePoint graph: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"SharePoint graph unavailable: {e}")

    if not files:
        keep = {n["id"] for n in g["nodes"] if n["type"] != "file"}
        g["nodes"] = [n for n in g["nodes"] if n["id"] in keep]
        g["edges"] = [e for e in g["edges"] if e["source"] in keep and e["target"] in keep]
    g["stats"] = stats(org_id)
    return g
