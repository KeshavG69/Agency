"""Where a bid's files live: its folder in the company's storage, and nowhere else.

That storage (SharePoint or Google Drive) holds the master copy. People edit it there online, so
the zip and the Outlook draft read every file back from it at the moment they're asked for: that's
what keeps a person's edits in the package. If it isn't usable we say so (NoSharePoint) rather
than quietly saving a second copy somewhere nobody is editing.
"""
from __future__ import annotations

import logging
import re

from client.crm_store import get_crm_store
from utils import file_store
from utils.sharepoint_writer import SharePointWriteError, edit_url

logger = logging.getLogger(__name__)

RESPONSE, PPQS = "Response", "PPQs"
_PPQ = re.compile(r"\bppqs?\b|questionnaire", re.I)


class NoSharePoint(Exception):
    """This bid can't be saved to the company's storage. The message is written for the user."""


def subfolder_for(title: str | None) -> str:
    return PPQS if _PPQ.search(title or "") else RESPONSE


def bid_folder(organization_id: str, opportunity_id: str) -> dict:
    """The opportunity's folder, created on first use, and again if the company changed storage."""
    crm = get_crm_store()
    opp = crm.get_opportunity(opportunity_id, organization_id)
    if opp is None:
        raise NoSharePoint("Opportunity not found.")
    if file_store.is_current(organization_id, opp.get("sharepoint_folder")):
        return opp["sharepoint_folder"]
    try:
        pointer = file_store.provision_bid_folders(organization_id, opp)
    except RuntimeError as exc:  # not connected, or never synced
        raise NoSharePoint("Connect SharePoint or Google Drive on the Library page first, so this "
                           "bid has a folder to save into.") from exc
    except SharePointWriteError as exc:
        raise NoSharePoint(_refused(exc)) from exc
    crm.set_sharepoint_folder(opportunity_id, organization_id, pointer)
    return pointer


def _refused(exc: Exception) -> str:
    if "403" in str(exc):
        return "Your file storage refused the write. An admin needs to reconnect it with write access."
    return f"Your file storage couldn't save this: {exc}"


def save(organization_id: str, opportunity_id: str, name: str, content: bytes,
         subfolder: str = RESPONSE) -> dict:
    """Put a file in the bid's folder. The same name replaces the file, and SharePoint keeps the
    old one in its version history."""
    folder = bid_folder(organization_id, opportunity_id)
    # A folder made before PPQs existed has no such subfolder.
    target = subfolder if subfolder in (folder.get("subfolders") or {}) else RESPONSE
    try:
        sp = file_store.file_to_bid_subfolder(organization_id, folder, target, name, content)
    except SharePointWriteError as exc:
        raise NoSharePoint(_refused(exc)) from exc
    if not sp:
        raise NoSharePoint("Connect SharePoint or Google Drive on the Library page first.")
    return {**sp, "drive_id": folder["drive_id"], "edit_url": edit_url(sp.get("sharepoint_url"))}


def record(opportunity_id: str, agent_id: str, doc_type: str, name: str, saved: dict,
           document_id: str | None = None) -> str:
    """Point a document row at the SharePoint file, reusing the row when the file was replaced."""
    crm = get_crm_store()
    doc_id = document_id or crm.create_document(opportunity_id, agent_id=agent_id, doc_type=doc_type,
                                                title=name, url=saved["sharepoint_url"])
    crm.update_document(doc_id, title=name, url=saved["sharepoint_url"], status="filed",
                        sharepoint_url=saved["sharepoint_url"],
                        sharepoint_item_id=saved["sharepoint_item_id"], drive_id=saved["drive_id"])
    return doc_id


def read(organization_id: str, doc: dict) -> bytes:
    """A file's current bytes, including whatever people have edited."""
    return file_store.download(organization_id, doc["drive_id"], doc["sharepoint_item_id"])
