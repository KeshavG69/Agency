"""SharePoint WRITE layer — create the per-Bid folder tree in the org's document library.

Read access (crawl/search) lives in sharepoint_graph_client.py; this is the *write* side.
Transport is the same Composio `tools.proxy` (proxy-execute), method=POST, so the raw Graph
token is never handled here. The Graph auth config already carries the write scopes
(Sites.ReadWrite.All / Files.ReadWrite.All).

On Bid we create, inside the org's document library, ONE folder named
"{solicitation_number} - {title}" with 4 subfolders: Solicitation, Capture Docs,
Resources, Response. Idempotent: existing folders are reused, never duplicated.
"""
from __future__ import annotations

import logging
import re
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

import base64

import httpx

from client.sharepoint_graph import find_document_library
from utils.composio_utils import get_composio_client, sharepoint_entity
from utils.sharepoint_graph_client import _GRAPH, graph_account, graph_get

logger = logging.getLogger(__name__)

# The subfolders created inside each Bid folder (order preserved). "Shared Documents" is
# the CONTAINER library, not a subfolder — see the sharepoint-bid-folder-taxonomy memory.
BID_SUBFOLDERS = ["Solicitation", "Capture Docs", "Resources", "Response"]

_JSON_HEADER = [{"name": "Content-Type", "value": "application/json", "type": "header"}]
_ILLEGAL = re.compile(r'[\\/:*?"<>|#%]')
# Graph error codes meaning "the item is gone" (a stale pointer) — vs a retryable throttle/5xx.
_NOT_FOUND_CODE = re.compile(r"notfound", re.I)  # itemNotFound / resourceNotFound / notFound
# HTTP statuses worth retrying (throttling / transient server errors); everything else 4xx
# is a deterministic client error we surface rather than retry-storm.
_RETRYABLE = {408, 429, 500, 502, 503, 504}


class SharePointWriteError(Exception):
    """A deterministic Graph write failure (bad request, 403 no scope, …) — surface, don't retry."""


class SharePointTransientError(Exception):
    """A transient Graph failure (throttle / 5xx) — the caller should retry."""


class SharePointNotConnectedError(Exception):
    """The org has no ACTIVE sharepoint_graph connection — distinct from 'folder is empty'."""


class SharePointReadError(Exception):
    """The Bid folder couldn't be read (e.g. a stale pointer to a folder deleted/moved in
    SharePoint) — distinct from 'folder is empty' so the UI can prompt a re-provision."""


def _sanitize(name: str) -> str:
    """A SharePoint-safe folder name. Truncate FIRST, then strip trailing space/dot (which
    SharePoint rejects) — so a long title can never leave an illegal trailing character."""
    name = _ILLEGAL.sub("-", name or "")
    name = re.sub(r"\s+", " ", name).strip()
    name = name[:120].strip(" .")
    return name or "Opportunity"


def bid_folder_name(opp: dict) -> str:
    """"{solicitation_number} - {title}". Without a solicitation number, append a short id
    suffix so two distinct opportunities that share a title never collide into one folder."""
    sol = (opp.get("solicitation_number") or "").strip()
    title = (opp.get("title") or "Opportunity").strip()
    if sol:
        return _sanitize(f"{sol} - {title}")
    oid = str(opp.get("id") or opp.get("_id") or "")[-6:]
    return _sanitize(f"{title} ({oid})" if oid else title)


def _int_status(d: dict) -> int | None:
    """The proxied HTTP status as an int. The Composio proxy returns it as a FLOAT (e.g.
    404.0) — so a naive `isinstance(status, int)` check silently misses every status."""
    s = d.get("status")
    if s is None:
        s = d.get("status_code")
    if isinstance(s, float):
        return int(s)
    return s if isinstance(s, int) else None


def _err(data) -> str:
    if isinstance(data, dict):
        e = data.get("error")
        if isinstance(e, dict):
            return e.get("message") or str(e)
        return str(e or data)
    return str(data)


def _graph_request(method: str, url: str, account_id: str, body: dict | None = None):
    """Call Microsoft Graph via the Composio proxy and interpret the proxied HTTP status.

    Returns (status, data). Raises SharePointTransientError on retryable statuses and
    SharePointWriteError on deterministic client errors; 404 is returned (not raised) so
    callers can treat it as 'absent'.
    """
    if not account_id:
        raise RuntimeError("No ACTIVE sharepoint_graph connection — connect it first.")
    if not url.startswith("http"):
        url = f"{_GRAPH}/{url.lstrip('/')}"
    c = get_composio_client()
    pr = c.tools.proxy(
        endpoint=url, method=method, body=body,
        connected_account_id=account_id,
        parameters=_JSON_HEADER if body is not None else None,
    )
    d = pr if isinstance(pr, dict) else pr.model_dump()
    status = _int_status(d)
    data = d.get("data")

    if isinstance(status, int):
        if 200 <= status < 300:
            return status, data
        if status in _RETRYABLE:
            raise SharePointTransientError(f"Graph {method} {status}: {_err(data)}")
        if status == 404:
            return status, data  # caller decides (absent)
        raise SharePointWriteError(f"Graph {method} {status}: {_err(data)}")

    # Unknown envelope (no status field): infer from the body.
    if isinstance(data, dict) and data.get("error") and not data.get("id"):
        raise SharePointWriteError(f"Graph {method} error: {_err(data)}")
    return status, data


def _item_by_path(drive_id: str, path: str, account_id: str) -> dict | None:
    """The drive item at `path` from the library root, or None if it genuinely doesn't exist.
    Raises on auth/throttle errors so a transient failure is never mistaken for 'absent'."""
    if not path:
        return None
    status, data = _graph_request("GET", f"drives/{drive_id}/root:/{quote(path)}", account_id)
    if isinstance(data, dict) and data.get("id"):
        return data
    return None  # 404 / not an item


def ensure_folder(drive_id: str, parent_id: str, parent_path: str, name: str, account_id: str) -> dict:
    """Return the child folder `name` under `parent_id`, creating it if missing (idempotent).

    `parent_path` is the parent's path from the library root ("" for root) — used to look up
    an existing child by path. Creation addresses the parent by ITEM ID so names never need
    URL-encoding. Reuses an existing folder on conflict/race.
    """
    full = f"{parent_path}/{name}".strip("/") if parent_path else name
    existing = _item_by_path(drive_id, full, account_id)
    if existing:
        return existing

    body = {"name": name, "folder": {}, "@microsoft.graph.conflictBehavior": "fail"}
    try:
        status, data = _graph_request("POST", f"drives/{drive_id}/items/{parent_id}/children", account_id, body=body)
    except SharePointWriteError:
        # e.g. 409 nameAlreadyExists from a concurrent create — fall through to the re-check.
        status, data = None, None
    if isinstance(data, dict) and data.get("id"):
        return data

    # Lost a race (created between check and POST) or a soft conflict — re-read by path.
    existing = _item_by_path(drive_id, full, account_id)
    if existing:
        return existing
    raise SharePointWriteError(f"Could not create SharePoint folder '{name}' (status={status}): {_err(data)}")


def _sanitize_filename(name: str) -> str:
    """A SharePoint-safe file name — strips illegal chars but keeps the extension dot."""
    name = _ILLEGAL.sub("-", name or "")
    name = re.sub(r"\s+", " ", name).strip().strip(".")  # no leading/trailing dot
    return name[:200] or "document"


def upload_file_to_folder(drive_id: str, parent_folder_id: str, filename: str,
                          content: bytes, account_id: str) -> dict:
    """Upload file `content` (bytes) into a SharePoint folder (addressed by its item id).

    Returns the created/updated DriveItem ({id, name, webUrl, size, ...}). Replaces an
    existing same-named file (PUT /content semantics). Uses the RAW Composio client's
    `binary_body` (base64) — the high-level `tools.proxy` wrapper doesn't expose it. Graph
    infers the content type from the filename extension.
    """
    if not account_id:
        raise RuntimeError("No ACTIVE sharepoint_graph connection — connect it first.")
    safe = _sanitize_filename(filename)
    b64 = base64.b64encode(content).decode()
    endpoint = f"{_GRAPH}/drives/{drive_id}/items/{parent_folder_id}:/{quote(safe)}:/content"
    try:
        pr = get_composio_client().client.tools.proxy(
            endpoint=endpoint, method="PUT",
            binary_body={"base64": b64},
            connected_account_id=account_id,
        )
    except Exception as exc:  # noqa: BLE001 — a Composio-level failure; treat as transient
        raise SharePointTransientError(f"SharePoint upload of '{safe}' errored: {exc}")
    d = pr if isinstance(pr, dict) else pr.model_dump()
    status = _int_status(d)
    data = d.get("data")
    if isinstance(data, dict) and data.get("id") and (status is None or 200 <= status < 300):
        return data
    if isinstance(status, int) and status in _RETRYABLE:
        raise SharePointTransientError(f"SharePoint upload of '{safe}' -> {status}: {_err(data)}")
    raise SharePointWriteError(f"SharePoint upload of '{safe}' failed (status={status}): {_err(data)}")


def download_drive_item(organization_id: str, drive_id: str, item_id: str) -> bytes:
    """Download a SharePoint drive item's raw bytes BY ID — used to pull an image (or any file)
    into the doc-generation workspace so a generated document can embed it.

    Fetches the item's short-lived, pre-authenticated `@microsoft.graph.downloadUrl` via the
    Composio Graph proxy, then GETs the bytes from that URL directly. Raises on failure."""
    account_id = graph_account(sharepoint_entity(organization_id))
    if not account_id:
        raise RuntimeError("SharePoint is not connected for this organization.")
    if not drive_id or not item_id:
        raise SharePointWriteError("download_drive_item needs both drive_id and item_id.")
    _status, data = _graph_request("GET", f"drives/{drive_id}/items/{item_id}", account_id)
    dl = data.get("@microsoft.graph.downloadUrl") if isinstance(data, dict) else None
    if not dl:
        raise SharePointWriteError(f"No download URL for SharePoint item {item_id} (status={_status}).")
    with httpx.Client(timeout=60, follow_redirects=True) as client:
        resp = client.get(dl)
        resp.raise_for_status()
        return resp.content


def edit_url(web_url: str | None) -> str | None:
    """Turn a driveItem webUrl into an 'open in Office-for-the-web, edit mode' link.

    We only set web=1 & action=edit on the QUERY string — never string-build the (undocumented)
    Doc.aspx path — so it stays correct if the file is renamed/moved. Clicking it opens the file
    in the browser editor, which autosaves straight back into SharePoint. Editing requires the
    HUMAN to have their own M365 session in that tenant (our app token can't sign them in)."""
    if not web_url:
        return None
    p = urlsplit(web_url)
    q = dict(parse_qsl(p.query, keep_blank_values=True))
    q["web"] = "1"
    q["action"] = "edit"
    return urlunsplit((p.scheme, p.netloc, p.path, urlencode(q), p.fragment))


def list_bid_documents(organization_id: str, folder: dict) -> list[dict]:
    """List the files/folders currently in each subfolder of a Bid folder — LIVE from
    SharePoint (so a file a human dropped in shows up too). Returns dicts:
    {subfolder, id, name, web_url, size, is_folder, modified}.

    Follows Graph pagination so a subfolder with more than one page of children (~200) is
    listed in full. Raises SharePointNotConnectedError when SharePoint isn't connected, and
    SharePointReadError when the pointer is stale (folder deleted/moved) and nothing could be
    read — both distinct from a genuinely empty folder so the caller can react accordingly."""
    account_id = graph_account(sharepoint_entity(organization_id))
    if not account_id:
        raise SharePointNotConnectedError("SharePoint is not connected for this organization.")
    if not folder or not folder.get("drive_id"):
        return []
    drive_id = folder["drive_id"]
    select = "$select=id,name,size,folder,file,webUrl,lastModifiedDateTime"
    out: list[dict] = []
    saw_stale = False       # a genuine 404 itemNotFound — the pointer is stale
    saw_transient = False   # a 429/5xx throttle — retryable, NOT a deleted folder
    for sub_name, sub in (folder.get("subfolders") or {}).items():
        sid = (sub or {}).get("id")
        if not sid:
            continue
        # Page through @odata.nextLink; cap at 50 pages (~10k items) as a runaway guard.
        url = f"drives/{drive_id}/items/{sid}/children?{select}&$top=200"
        pages = 0
        while url and pages < 50:
            data = graph_get(url, account_id) or {}
            err = data.get("error") if isinstance(data, dict) else None
            if err:
                # graph_get flattens all non-2xx into an error body (no status). Read the Graph
                # error CODE to tell a stale pointer (itemNotFound) from a retryable throttle —
                # otherwise a 429 storm would wrongly tell the user the folder was deleted.
                code = (err.get("code") if isinstance(err, dict) else "") or ""
                if _NOT_FOUND_CODE.search(code):
                    saw_stale = True
                else:
                    saw_transient = True
                break
            for it in data.get("value", []) or []:
                is_folder = "folder" in it
                out.append({
                    "subfolder": sub_name,
                    "id": it.get("id"),
                    "name": it.get("name"),
                    "web_url": it.get("webUrl"),
                    # Edit-in-browser link (files only — a folder just opens in SharePoint).
                    "edit_url": None if is_folder else edit_url(it.get("webUrl")),
                    "size": it.get("size"),
                    "is_folder": is_folder,
                    "modified": it.get("lastModifiedDateTime"),
                })
            url = data.get("@odata.nextLink") if isinstance(data, dict) else None
            pages += 1
    # Only claim "moved/deleted" when EVERY read failed with a genuine not-found and we listed
    # nothing. A transient throttle raises the generic (retryable) message instead.
    if not out:
        if saw_stale and not saw_transient:
            raise SharePointReadError("The SharePoint Bid folder could not be read (it may have "
                                      "been moved or deleted). Re-mark the opportunity as a Bid to "
                                      "recreate it.")
        if saw_transient:
            raise SharePointTransientError("Couldn't read the SharePoint folder just now.")
    return out


def file_to_bid_subfolder(organization_id: str, folder: dict | None, subfolder: str,
                          filename: str, content: bytes) -> dict | None:
    """Put `content` (bytes) into `subfolder` (e.g. 'Solicitation', 'Capture Docs') of a Bid
    folder and return {sharepoint_url, sharepoint_item_id}. Returns None (no raise) when
    SharePoint isn't connected or that subfolder pointer is missing; RAISES on an actual upload
    failure so the caller can log."""
    sub = ((folder or {}).get("subfolders") or {}).get(subfolder) if folder else None
    if not folder or not folder.get("drive_id") or not sub or not sub.get("id"):
        return None
    account_id = graph_account(sharepoint_entity(organization_id))
    if not account_id:
        return None
    item = upload_file_to_folder(folder["drive_id"], sub["id"], _sanitize_filename(filename),
                                 content, account_id)
    return {"sharepoint_url": item.get("webUrl"), "sharepoint_item_id": item.get("id")}


def file_to_capture_docs(organization_id: str, opp: dict, filename: str, content: bytes) -> dict | None:
    """Put `content` (bytes) straight into the opp's 'Capture Docs' Bid subfolder and return
    {sharepoint_url, sharepoint_item_id}. Returns None (no raise) when SharePoint isn't connected
    or the Bid folder pointer is missing; RAISES on an actual upload failure so the caller can log.

    Used by the capture agent's upload tool to file a freshly-generated deliverable in the SAME
    pass that saved it to iDrive — no download-then-reupload. The Bid folder already exists (it's
    created the moment the opp is marked Bid), so no provisioning happens here."""
    return file_to_bid_subfolder(
        organization_id, (opp or {}).get("sharepoint_folder"), "Capture Docs", filename, content
    )


def provision_bid_folders(organization_id: str, opp: dict) -> dict:
    """Create the Bid folder tree in the org's document library; return a pointer to it.

    Raises RuntimeError (non-retryable precondition) if SharePoint isn't connected or no
    crawled library is available; SharePointWriteError / SharePointTransientError propagate
    from the folder writes. Idempotent — reruns reuse existing folders.
    """
    account_id = graph_account(sharepoint_entity(organization_id))
    if not account_id:
        raise RuntimeError("SharePoint is not connected for this organization.")
    lib = find_document_library(organization_id)
    if not lib or not lib.get("drive_id"):
        raise RuntimeError("No SharePoint library found — run a SharePoint sync first.")

    drive_id = lib["drive_id"]
    name = bid_folder_name(opp)
    top = ensure_folder(drive_id, "root", "", name, account_id)
    top_id = top.get("id")

    subfolders: dict[str, dict] = {}
    for sf in BID_SUBFOLDERS:
        child = ensure_folder(drive_id, top_id, name, sf, account_id)
        subfolders[sf] = {"id": child.get("id"), "web_url": child.get("webUrl")}

    pointer = {
        "drive_id": drive_id,
        "folder_id": top_id,
        "name": name,
        "web_url": top.get("webUrl"),
        "library": lib.get("name"),
        "subfolders": subfolders,
    }
    logger.info("Provisioned Bid folder '%s' (%d subfolders) in library '%s'",
                name, len(subfolders), lib.get("name"))
    return pointer
