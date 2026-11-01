"""The required-documents checklist for one opportunity."""
import asyncio
import logging
import os
import tempfile
from typing import get_args

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel

from auth.dependencies import get_current_user
from client.crm_store import get_crm_store
from client.requirements_store import get_requirements_store
from models.requirements import ItemKind

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/opportunities/{opportunity_id}/requirements", tags=["requirements"])

_KINDS = set(get_args(ItemKind))
_EDITABLE = {"title", "detail", "page_limit", "done", "note"}


def _scope(opportunity_id: str, user: dict) -> str:
    """The caller's org, after confirming this opportunity belongs to it."""
    org = str(user["organization_id"])
    if get_crm_store().get_opportunity(opportunity_id, org) is None:
        raise HTTPException(status_code=404, detail="Opportunity not found")
    return org


def _found(row: dict | None) -> dict:
    if row is None:
        raise HTTPException(status_code=404, detail="Requirement not found")
    return row


@router.get("")
def get_requirements(opportunity_id: str, current_user: dict = Depends(get_current_user)) -> dict:
    org = _scope(opportunity_id, current_user)
    return get_requirements_store().get(org, opportunity_id) or {"status": "none", "items": []}


@router.post("/read")
def read_now(opportunity_id: str, current_user: dict = Depends(get_current_user)) -> dict:
    """Read (or re-read, after an amendment) the solicitation's files."""
    org = _scope(opportunity_id, current_user)
    from tasks.requirements_tasks import queue_read

    queue_read(opportunity_id, org, only_if_new=False)
    return {"status": "reading"}


class ItemEdit(BaseModel):
    title: str | None = None
    detail: str | None = None
    page_limit: str | None = None
    done: bool | None = None
    note: str | None = None


@router.patch("/items/{item_id}")
def edit_item(opportunity_id: str, item_id: str, body: ItemEdit,
              current_user: dict = Depends(get_current_user)) -> dict:
    org = _scope(opportunity_id, current_user)
    fields = {k: v for k, v in body.model_dump(exclude_unset=True).items() if k in _EDITABLE}
    if not fields:
        raise HTTPException(status_code=400, detail="Nothing to update")
    if fields.get("title") is not None and not fields["title"].strip():
        raise HTTPException(status_code=400, detail="An item needs a title")
    # A person's edit is the truth from here on, so an unconfirmed quote stops mattering.
    if set(fields) & {"title", "detail", "page_limit"}:
        fields["verified"] = True
    return _found(get_requirements_store().edit_item(org, opportunity_id, item_id, fields))


class ItemAdd(BaseModel):
    title: str
    kind: str = "other"
    detail: str = ""


@router.post("/items")
def add_item(opportunity_id: str, body: ItemAdd, current_user: dict = Depends(get_current_user)) -> dict:
    org = _scope(opportunity_id, current_user)
    if not body.title.strip():
        raise HTTPException(status_code=400, detail="An item needs a title")
    kind = body.kind if body.kind in _KINDS else "other"
    return _found(get_requirements_store().add_item(org, opportunity_id, body.title.strip(), kind, body.detail.strip()))


@router.delete("/items/{item_id}")
def remove_item(opportunity_id: str, item_id: str, current_user: dict = Depends(get_current_user)) -> dict:
    org = _scope(opportunity_id, current_user)
    return _found(get_requirements_store().remove_item(org, opportunity_id, item_id))


@router.post("/items/{item_id}/file")
async def attach_file(opportunity_id: str, item_id: str, file: UploadFile = File(...),
                      current_user: dict = Depends(get_current_user)) -> dict:
    """Attach the finished document to a checklist line. Stored like any other document, so it
    also appears under the opportunity's documents."""
    org = _scope(opportunity_id, current_user)
    from routers.opportunities import _upload_one

    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="That file is empty")
    name = os.path.basename(file.filename or "document")
    with tempfile.NamedTemporaryFile(delete=True) as tmp:
        tmp.write(data)
        tmp.flush()
        url, key = await asyncio.to_thread(_upload_one, tmp.name, org, opportunity_id, name)
    doc_id = get_crm_store().create_document(
        opportunity_id, agent_id="checklist", doc_type="response", title=name, url=url, object_key=key,
    )
    return _found(get_requirements_store().attach_file(org, opportunity_id, item_id, {"id": doc_id, "title": name}))


@router.post("/approve")
def approve(opportunity_id: str, current_user: dict = Depends(get_current_user)) -> dict:
    """A person confirms the list is right. This is what satisfies the task flow's
    'check requirements' step, so the next step comes up straight away."""
    org = _scope(opportunity_id, current_user)
    row = _found(get_requirements_store().approve(org, opportunity_id, (current_user.get("email") or "").lower()))
    from tasks.action_plan_tasks import plan_org_actions

    plan_org_actions.delay(org)
    return row
