"""Library router — the analysed library: each file's category, summary and facts.

Every read is filtered to the files the acting employee may open in SharePoint (admins see
the whole library, as on /api/sharepoint/graph).
"""
import logging
from typing import get_args

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from auth.dependencies import get_current_user
from client.library_store import FAILED, PENDING, get_library_store
from models.library import Category

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/library", tags=["library"])

_CATEGORIES = set(get_args(Category))


def _scope(user: dict) -> tuple[str, str | None]:
    """(organization_id, email to ACL-filter by — None for an admin)."""
    org = str(user["organization_id"])
    return org, None if user.get("role") == "admin" else (user.get("email") or "").lower()


@router.get("/documents")
def list_documents(category: str | None = None, status: str | None = None,
                   current_user: dict = Depends(get_current_user)) -> dict:
    org, email = _scope(current_user)
    store = get_library_store()
    return {
        "documents": store.list(org, email=email, category=category, status=status),
        "counts": store.counts(org, email=email),
    }


@router.get("/documents/{item_id}")
def get_document(item_id: str, current_user: dict = Depends(get_current_user)) -> dict:
    org, email = _scope(current_user)
    row = get_library_store().get(org, item_id, email=email)
    if not row:
        raise HTTPException(status_code=404, detail="Document not found.")
    return row


class CategoryFix(BaseModel):
    category: str


@router.post("/documents/{item_id}/category")
def fix_category(item_id: str, body: CategoryFix, current_user: dict = Depends(get_current_user)) -> dict:
    """A person corrects a file's category. It sticks through future syncs, and the file is
    re-read once so its facts match the corrected category."""
    if body.category not in _CATEGORIES:
        raise HTTPException(status_code=400, detail=f"Unknown category '{body.category}'.")
    org, email = _scope(current_user)
    store = get_library_store()
    if not store.get(org, item_id, email=email):
        raise HTTPException(status_code=404, detail="Document not found.")
    if not store.set_category_override(org, item_id, body.category, (current_user.get("email") or "").lower()):
        raise HTTPException(status_code=409, detail="This file type isn't one we read.")
    from tasks.library_tasks import analyse_document_task

    analyse_document_task.delay(org, item_id)
    return {"ok": True, "category": body.category}


@router.post("/analyse")
def analyse_now(current_user: dict = Depends(get_current_user)) -> dict:
    """Admin: retry failed files and read anything still pending, without a full re-crawl."""
    if current_user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Only an admin can re-run the library analysis.")
    org = str(current_user["organization_id"])
    store = get_library_store()
    retried = store.docs.update_many(
        {"organization_id": org, "status": FAILED}, {"$set": {"status": PENDING, "error": None}}
    ).modified_count
    from tasks.library_tasks import analyse_library_task

    analyse_library_task.delay(org)
    return {"retried_failed": retried}
