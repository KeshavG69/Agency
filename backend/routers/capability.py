"""An opportunity's capability matrix: read it, or build it again."""
from fastapi import APIRouter, Depends, HTTPException

from auth.dependencies import get_current_user
from client.capability_store import get_capability_store
from client.requirements_store import get_requirements_store
from models.capability import RUBRIC
from routers.requirements import _reader, _scope

router = APIRouter(prefix="/api/opportunities/{opportunity_id}/capability", tags=["capability"])


@router.get("")
def get_matrix(opportunity_id: str, current_user: dict = Depends(get_current_user)) -> dict:
    org = _scope(opportunity_id, current_user)
    row = get_capability_store().get(org, opportunity_id) or {"status": None}
    return {**row, "rubric": RUBRIC}


@router.post("/build")
def build_matrix(opportunity_id: str, current_user: dict = Depends(get_current_user)) -> dict:
    """Build, or rebuild, from the solicitation text the requirements reader stored."""
    org = _scope(opportunity_id, current_user)
    if not get_requirements_store().source_text(org, opportunity_id).strip():
        raise HTTPException(status_code=400, detail="Read the requirements first, so there is solicitation text to work from.")
    from tasks.capability_tasks import queue_build

    queue_build(opportunity_id, org, _reader(current_user))
    return {**(get_capability_store().get(org, opportunity_id) or {}), "rubric": RUBRIC}
