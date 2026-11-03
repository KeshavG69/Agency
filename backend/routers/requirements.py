"""The required-documents checklist for one opportunity."""
import asyncio
import io
import logging
import os
import re
import tempfile
import zipfile
from typing import get_args

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile
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


def _reader(user: dict) -> str | None:
    """Whose library access a writer uses: an admin's covers the whole org."""
    return None if user.get("role") == "admin" else (user.get("email") or "").lower()


def _need_download(user: dict) -> None:
    """The final files leave Collecct only through the people named for it: everyone else works
    on the online master."""
    from utils.org_settings import can_download

    if not can_download(user):
        raise HTTPException(status_code=403, detail="Only the people named in Organisation settings "
                                                    "can download the final files.")


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


@router.get("/access")
def access(opportunity_id: str, current_user: dict = Depends(get_current_user)) -> dict:
    """What this person can do with the bid's files, and where those files live."""
    org = _scope(opportunity_id, current_user)
    from utils.composio_utils import sharepoint_entity
    from utils.org_settings import can_download
    from utils.sharepoint_graph_client import graph_account

    opp = get_crm_store().get_opportunity(opportunity_id, org) or {}
    return {
        "can_download": can_download(current_user),
        "sharepoint_connected": bool(graph_account(sharepoint_entity(org))),
        "folder_url": (opp.get("sharepoint_folder") or {}).get("web_url"),
    }


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
    """Attach the finished document to a checklist line. It goes in the bid's SharePoint folder
    like everything else, and also appears under the opportunity's documents."""
    org = _scope(opportunity_id, current_user)
    from tasks.proposal_tasks import free_name
    from utils import bid_files

    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="That file is empty")
    store = get_requirements_store()
    row = _found(store.get(org, opportunity_id))
    item = next((i for i in row.get("items") or [] if i["id"] == item_id), None)
    if item is None:
        raise HTTPException(status_code=404, detail="Requirement not found")
    name = free_name(row, item_id, os.path.basename(file.filename or "document"))
    try:
        saved = await asyncio.to_thread(bid_files.save, org, opportunity_id, name, data,
                                        bid_files.subfolder_for(item.get("title")))
    except bid_files.NoSharePoint as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    # The same name replaced the file in SharePoint, so it replaces the entry here too.
    files = item.get("files") or []
    same = next((f for f in files if f["title"] == name), None)
    doc_id = bid_files.record(opportunity_id, "checklist", "response", name, saved, same and same["id"])
    entry = {"id": doc_id, "title": name, "size": len(data), "edit_url": saved["edit_url"]}
    fields: dict = {"files": [*[f for f in files if f is not same], entry]}
    # For a signed form or a provided document, the upload IS the work, so the line is done. A
    # written document still needs a person to read it first.
    if item.get("production", "write") != "write":
        fields["done"] = True
    return _found(store.edit_item(org, opportunity_id, item_id, fields))


@router.post("/amendment")
async def add_amendment(opportunity_id: str, files: list[UploadFile] = File(...),
                        current_user: dict = Depends(get_current_user)) -> dict:
    """Add an amendment: it joins the solicitation's files, goes in the Solicitation folder, and
    the requirements are read again with it. Ticks, notes and files on the list are kept."""
    org = _scope(opportunity_id, current_user)
    from routers.opportunities import _upload_one
    from tasks.requirements_tasks import queue_read
    from tasks.sharepoint_tasks import provision_bid_folders_task

    added = 0
    for f in files:
        data = await f.read()
        if not data:
            continue
        name = os.path.basename(f.filename or "amendment")
        with tempfile.NamedTemporaryFile(delete=True) as tmp:
            tmp.write(data)
            tmp.flush()
            url, key = await asyncio.to_thread(_upload_one, tmp.name, org, opportunity_id, name)
        get_crm_store().create_document(opportunity_id, agent_id="manual_upload", doc_type="solicitation",
                                        title=name, url=url, object_key=key)
        added += 1
    if not added:
        raise HTTPException(status_code=400, detail="Those files are empty")
    provision_bid_folders_task.delay(org, opportunity_id)
    queue_read(opportunity_id, org, only_if_new=False)
    return get_requirements_store().get(org, opportunity_id)


@router.post("/approve")
def approve(opportunity_id: str, current_user: dict = Depends(get_current_user)) -> dict:
    """A person confirms the list is right. This is what satisfies the task flow's
    'check requirements' step, so the next step comes up straight away."""
    org = _scope(opportunity_id, current_user)
    row = _found(get_requirements_store().approve(org, opportunity_id, (current_user.get("email") or "").lower()))
    from tasks.action_plan_tasks import plan_org_actions

    plan_org_actions.delay(org)
    # The list is agreed, so start writing.
    from tasks.proposal_tasks import queue_package

    queue_package(opportunity_id, org, _reader(current_user))
    return get_requirements_store().get(org, opportunity_id)


@router.post("/generate")
def generate(opportunity_id: str, current_user: dict = Depends(get_current_user)) -> dict:
    """Write every document Collecct can, skipping those already written."""
    org = _scope(opportunity_id, current_user)
    from tasks.proposal_tasks import queue_package

    queue_package(opportunity_id, org, _reader(current_user))
    return get_requirements_store().get(org, opportunity_id)


@router.post("/items/{item_id}/write")
def write_item(opportunity_id: str, item_id: str, current_user: dict = Depends(get_current_user)) -> dict:
    """Write, or rewrite, one document."""
    org = _scope(opportunity_id, current_user)
    from tasks.proposal_tasks import queue_item

    queue_item(opportunity_id, org, item_id, _reader(current_user))
    return get_requirements_store().get(org, opportunity_id)


def _package_files(org: str, opportunity_id: str, row: dict) -> list[tuple[str, bytes]]:
    """Every file on the checklist as it stands in SharePoint now, as (unique name, bytes)."""
    from utils import bid_files

    crm = get_crm_store()
    out: list[tuple[str, bytes]] = []
    used: set[str] = set()
    for item in row.get("items") or []:
        for f in item.get("files") or []:
            doc = crm.get_document(f["id"])
            if not doc or not doc.get("sharepoint_item_id"):
                continue
            base, ext = os.path.splitext(f["title"])
            name, n = f["title"], 2
            while name in used:
                name, n = f"{base} ({n}){ext}", n + 1
            try:
                content = bid_files.read(org, doc)
            except Exception as exc:  # noqa: BLE001 — moved, deleted, or SharePoint is down
                logger.warning("Couldn't fetch %s from SharePoint: %s", f["title"], exc)
                raise HTTPException(status_code=502, detail=f"Couldn't fetch {f['title']} from SharePoint.")
            used.add(name)
            out.append((name, content))
    return out


@router.get("/package.zip")
def download_package(opportunity_id: str, current_user: dict = Depends(get_current_user)) -> Response:
    """All the bid's files in one download."""
    org = _scope(opportunity_id, current_user)
    _need_download(current_user)
    row = _found(get_requirements_store().get(org, opportunity_id))
    files = _package_files(org, opportunity_id, row)
    if not files:
        raise HTTPException(status_code=404, detail="No files yet")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, content in files:
            z.writestr(name, content)
    return Response(buf.getvalue(), media_type="application/zip",
                    headers={"Content-Disposition": 'attachment; filename="bid-package.zip"'})


def _outstanding(row: dict) -> list[str]:
    """What still stands between this package and being sendable, in plain words."""
    out = []
    for i in row.get("items") or []:
        kind = i.get("production", "write")
        title = i.get("title")
        if kind == "sign" and not i.get("files"):
            out.append(f"Needs your signature: {title}")
        elif kind == "price" and not i.get("files"):
            out.append(f"Waiting for pricing: {title}")
        elif kind == "provide" and not i.get("files"):
            out.append(f"Needs uploading: {title}")
        elif kind == "write" and not i.get("files"):
            out.append(f"Not written yet: {title}")
        elif i.get("gaps"):
            out.append(f"{len(i['gaps'])} thing(s) to fix in: {title}")
    return out


def _size_warning(row: dict, files: list[tuple[str, bytes]]) -> str | None:
    """The solicitation's own email size limit, if it states one in MB."""
    rule = (row.get("submission") or {}).get("file_rules") or ""
    m = re.search(r"(\d+)\s*MB", rule, re.I)
    total = sum(len(c) for _, c in files) / (1024 * 1024)
    if m and total > int(m.group(1)):
        return (f"These files total {total:.1f} MB and the limit is {m.group(1)} MB per email. "
                "Split them across emails.")
    return None


@router.post("/email")
def build_email(opportunity_id: str, current_user: dict = Depends(get_current_user)) -> dict:
    """Write the submission email from the solicitation's rules and the files on the checklist."""
    org = _scope(opportunity_id, current_user)
    store = get_requirements_store()
    row = _found(store.get(org, opportunity_id))
    from agent.proposal_writer import draft_email
    from utils.organizations import user_display_name

    opp = get_crm_store().get_opportunity(opportunity_id, org)
    names = [f["title"] for i in row.get("items") or [] for f in i.get("files") or []]
    try:
        email = draft_email(org, opp, row, names, user_display_name(current_user.get("email") or ""))
    except Exception as exc:  # noqa: BLE001
        logger.warning("Submission email failed for %s: %s", opportunity_id, exc)
        raise HTTPException(status_code=502, detail="Couldn't write the email. Please try again.")
    return store.set_email(org, opportunity_id, email.model_dump())


class EmailEdit(BaseModel):
    to: list[str]
    subject: str
    body: str


@router.get("/email/check")
def email_check(opportunity_id: str, current_user: dict = Depends(get_current_user)) -> dict:
    """What's still outstanding, shown beside the email before a draft is created."""
    org = _scope(opportunity_id, current_user)
    return {"outstanding": _outstanding(_found(get_requirements_store().get(org, opportunity_id)))}


@router.post("/email/outlook")
def create_outlook_draft(opportunity_id: str, body: EmailEdit,
                         current_user: dict = Depends(get_current_user)) -> dict:
    """Put the email and every file in the user's OWN Outlook Drafts. It is never sent from here."""
    org = _scope(opportunity_id, current_user)
    _need_download(current_user)  # a draft with every file attached is a download by another name
    if not body.subject.strip():
        raise HTTPException(status_code=400, detail="The email needs a subject.")
    store = get_requirements_store()
    row = _found(store.get(org, opportunity_id))
    store.set_email(org, opportunity_id, body.model_dump())
    files = _package_files(org, opportunity_id, row)
    from utils.outlook_draft import OutlookNotConnected, create_draft

    try:
        draft = create_draft((current_user.get("email") or "").lower(), body.to, body.subject,
                             body.body, files)
    except OutlookNotConnected:
        raise HTTPException(status_code=409, detail="Connect Outlook first (Contacts page).")
    except Exception as exc:  # noqa: BLE001
        logger.error("Outlook draft failed for %s: %s", opportunity_id, exc, exc_info=True)
        raise HTTPException(status_code=502, detail="Outlook couldn't create the draft. Please try again.")
    return {"created": True, "web_link": draft.get("web_link"), "attachments": len(files),
            "warning": _size_warning(row, files)}
