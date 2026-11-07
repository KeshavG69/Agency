"""Which storage a company's files are in: SharePoint or Google Drive, one at a time.

Anything that already has a file or a folder goes by its own `drive_id`, because that says where
it really is. Only something new (a bid's first folder, a library upload, a crawl) asks which
storage the company has connected.
"""
from __future__ import annotations

from utils import google_drive, sharepoint_writer
from utils.composio_utils import sharepoint_entity
from utils.sharepoint_graph_client import graph_account

SHAREPOINT, GOOGLE_DRIVE = "sharepoint", "googledrive"
NAMES = {SHAREPOINT: "SharePoint", GOOGLE_DRIVE: "Google Drive"}


def provider(organization_id: str) -> str | None:
    if graph_account(sharepoint_entity(organization_id)):
        return SHAREPOINT
    return GOOGLE_DRIVE if google_drive.account(organization_id) else None


def _writer(organization_id: str):
    return google_drive if provider(organization_id) == GOOGLE_DRIVE else sharepoint_writer


def _holding(folder: dict | None):
    return google_drive if google_drive.is_google((folder or {}).get("drive_id")) else sharepoint_writer


def is_current(organization_id: str, folder: dict | None) -> bool:
    """False when a bid's folder is in a storage the company has since moved away from."""
    if not folder:
        return False
    return _holding(folder) is _writer(organization_id)


def download(organization_id: str, drive_id: str, item_id: str) -> bytes:
    if google_drive.is_google(drive_id):
        return google_drive.download(organization_id, item_id)
    return sharepoint_writer.download_drive_item(organization_id, drive_id, item_id)


def list_bid_documents(organization_id: str, folder: dict) -> list[dict]:
    return _holding(folder).list_bid_documents(organization_id, folder)


def file_to_bid_subfolder(organization_id: str, folder: dict | None, subfolder: str,
                          filename: str, content: bytes) -> dict | None:
    return _holding(folder).file_to_bid_subfolder(organization_id, folder, subfolder, filename, content)


def file_to_capture_docs(organization_id: str, opp: dict, filename: str, content: bytes) -> dict | None:
    folder = (opp or {}).get("sharepoint_folder")
    return file_to_bid_subfolder(organization_id, folder, "Capture Docs", filename, content)


def file_to_library(organization_id: str, filename: str, content: bytes) -> dict | None:
    return _writer(organization_id).file_to_library(organization_id, filename, content)


def provision_bid_folders(organization_id: str, opp: dict) -> dict:
    return _writer(organization_id).provision_bid_folders(organization_id, opp)
