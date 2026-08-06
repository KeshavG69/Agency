"""Contacts router — serves the CRM knowledge graph + the CRM agent."""
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from agent.crm_agent import recommend_contacts
from auth.dependencies import get_current_user
from client.graph_store import get_network, list_contacts_page

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/contacts", tags=["contacts"])

MAX_CONTACTS_PAGE = 100


@router.get("")
def contacts_list(
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=MAX_CONTACTS_PAGE),
    q: str = Query(""),
    current_user: dict = Depends(get_current_user),
) -> dict:
    """One page of the acting employee's contacts (warmest first) for the list view — the
    scalable replacement for the whole-graph payload, which froze the browser at ~3k nodes."""
    try:
        return list_contacts_page(
            current_user["email"].lower(), str(current_user["organization_id"]),
            offset=offset, limit=limit, q=q,
        )
    except Exception as e:  # noqa: BLE001
        logger.error("Failed to list contacts: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Contacts unavailable: {e}")


@router.get("/graph")
def contact_graph(current_user: dict = Depends(get_current_user)) -> dict:
    """The acting employee's OWN people/companies/relationships graph as { nodes, edges }."""
    try:
        return get_network(
            current_user["email"].lower(), str(current_user["organization_id"])
        )
    except Exception as e:  # noqa: BLE001
        logger.error("Failed to read contact graph: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Graph unavailable: {e}")


class RecommendRequest(BaseModel):
    title: Optional[str] = None
    agency: Optional[str] = None
    naics: Optional[str] = None
    set_aside: Optional[str] = None
    place_of_performance: Optional[str] = None
    description: Optional[str] = None
    proposal: Optional[str] = None  # free-text capture plan / scope (extra context)


@router.post("/recommend")
def recommend(req: RecommendRequest, current_user: dict = Depends(get_current_user)) -> dict:
    """CRM Agent: search the acting employee's network for contacts relevant to this proposal."""
    opp = req.model_dump(exclude={"proposal"}, exclude_none=True)
    try:
        result = recommend_contacts(
            opp, proposal=req.proposal, employee_email=current_user["email"].lower()
        )
    except Exception as e:  # noqa: BLE001
        logger.error("CRM recommend failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"CRM agent failed: {e}")
    return {"recommendations": [r.model_dump() for r in result.recommendations]}
