"""Library router — the analysed library: each file's category, summary and facts.

Every read is filtered to the files the acting employee may open in SharePoint (admins see
the whole library, as on /api/sharepoint/graph).
"""
import asyncio
import logging
import os
import tempfile
import uuid
from typing import get_args

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
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


@router.post("/upload")
async def upload_documents(files: list[UploadFile] = File(...),
                           current_user: dict = Depends(get_current_user)) -> dict:
    """Add company documents by hand: contracts, past performance, resumes. With SharePoint
    connected they go into its 'Company Library' folder and are read from there. Only a company
    with no SharePoint yet has them held in Collecct."""
    org = str(current_user["organization_id"])
    email = (current_user.get("email") or "").lower()
    store = get_library_store()
    from client.idrive_storage import get_idrive_storage
    from tasks.library_tasks import analyse_document_task
    from tasks.sharepoint_tasks import sync_sharepoint_structure_task
    from utils.file_store import file_to_library
    from utils.sharepoint_writer import SharePointTransientError, SharePointWriteError

    added = []
    in_sharepoint = False
    for f in files:
        data = await f.read()
        if not data:
            continue
        name = os.path.basename(f.filename or "document")
        try:
            sp = await asyncio.to_thread(file_to_library, org, name, data)
        except (SharePointWriteError, SharePointTransientError) as exc:
            logger.warning("Library upload to SharePoint failed for %s: %s", name, exc)
            raise HTTPException(status_code=502, detail="Your file storage wouldn't take that file. An "
                                                        "admin may need to reconnect it with write access.")
        if sp:
            in_sharepoint = True
            row = store.add_sharepoint_file(org, sp, email)
            added.append({"item_id": row["item_id"], "name": name, "status": row["status"],
                          "skip_reason": row.get("skip_reason")})
            continue
        with tempfile.NamedTemporaryFile(delete=True) as tmp:
            tmp.write(data)
            tmp.flush()
            _, key = await asyncio.to_thread(
                get_idrive_storage().upload_document, tmp.name, org, "library",
                f"{uuid.uuid4().hex[:12]}_{name}",
            )
        row = store.add_upload(org, name, len(data), key, email)
        if row["status"] == PENDING:
            analyse_document_task.delay(org, row["item_id"])
        added.append({"item_id": row["item_id"], "name": name, "status": row["status"],
                      "skip_reason": row.get("skip_reason")})
    if not added:
        raise HTTPException(status_code=400, detail="No readable files were uploaded.")
    if in_sharepoint:
        sync_sharepoint_structure_task.delay(org)  # the crawl finds them and the reading follows
    return {"added": added}


@router.delete("/documents/{item_id}")
def delete_upload(item_id: str, current_user: dict = Depends(get_current_user)) -> dict:
    """Remove an uploaded document. SharePoint files are managed in SharePoint."""
    org, _ = _scope(current_user)
    row = get_library_store().remove_upload(org, item_id)
    if not row:
        raise HTTPException(status_code=404, detail="Uploaded document not found.")
    from client.idrive_storage import get_idrive_storage
    from client.library_graph import remove_items

    get_idrive_storage().delete_document(row["object_key"])
    remove_items(org, [item_id])
    return {"ok": True}


@router.get("/documents/{item_id}/url")
def document_url(item_id: str, current_user: dict = Depends(get_current_user)) -> dict:
    """A short-lived link to an uploaded document (SharePoint files carry their own link)."""
    org, email = _scope(current_user)
    row = get_library_store().get(org, item_id, email=email, with_embedding=True)
    if not row or not row.get("object_key"):
        raise HTTPException(status_code=404, detail="Document not found.")
    from client.idrive_storage import get_idrive_storage

    return {"url": get_idrive_storage().get_presigned_url(row["object_key"], expiration=3600)}


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
