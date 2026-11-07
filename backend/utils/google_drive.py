"""Google Drive as a company's file store: the same jobs as SharePoint, for companies on Google.

An admin connects one Google account for the company. Collecct reads that account's My Drive and
every shared drive it can see, and keeps bids in a "Collecct Bids" folder of My Drive. Calls go
through Composio's proxy, as they do for SharePoint, so no Google token is ever handled here.

Everything this returns has the SharePoint shape (crawl nodes, folder pointers, "webUrl" on an
uploaded file), so the library, the graph and the bid files work the same on either. A `drive_id`
starting with `gdrive:` is how the rest of the code tells a Google file from a SharePoint one.

Access: per-file sharing becomes the same roster SharePoint produces. Google can't list a Google
Group's members without admin rights we don't have, so a group's address is kept but matches no
employee: someone with access only through a group doesn't see that file. That fails closed.
"""
from __future__ import annotations

import base64
import logging
import mimetypes
from typing import Iterator
from urllib.parse import quote, urlencode

import httpx

from utils.composio_utils import get_composio_client, sharepoint_entity
from utils.sharepoint_graph_client import _active_account, _is_excluded
from utils.sharepoint_writer import (
    BID_SUBFOLDERS,
    LIBRARY_FOLDER,
    SharePointNotConnectedError,
    SharePointReadError,
    SharePointTransientError,
    SharePointWriteError,
    _err,
    _int_status,
    _RETRYABLE,
    _sanitize_filename,
    bid_folder_name,
)

logger = logging.getLogger(__name__)

TOOLKIT = "googledrive"
PREFIX = "gdrive:"
MY_DRIVE = f"{PREFIX}root"
SITE_ID = "gdrive"
SITE_NAME = "Google Drive"
BIDS_FOLDER = "Collecct Bids"

_API = "https://www.googleapis.com/drive/v3"
_UPLOAD = "https://www.googleapis.com/upload/drive/v3"
_MY_DRIVE_URL = "https://drive.google.com/drive/my-drive"
_JSON = [{"name": "Content-Type", "value": "application/json", "type": "header"}]
_ALL_DRIVES = "supportsAllDrives=true"
_FOLDER = "application/vnd.google-apps.folder"
_NATIVE = "application/vnd.google-apps."
_ITEM = "id,name,mimeType,size,webViewLink,modifiedTime,md5Checksum"
_PERMS = "permissions(type,emailAddress)"
_EMPTY = {"permitted_emails": [], "org_wide": False, "unique_acl": False}

# Google's own formats have no file to download, so they're exported as the Office equivalent.
_EXPORT = {
    f"{_NATIVE}document": (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "docx"),
    f"{_NATIVE}spreadsheet": (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "xlsx"),
    f"{_NATIVE}presentation": (
        "application/vnd.openxmlformats-officedocument.presentationml.presentation", "pptx"),
}


def is_google(drive_id: str | None) -> bool:
    return (drive_id or "").startswith(PREFIX)


def account(organization_id: str) -> str | None:
    """The company's ACTIVE Google connection. It sits under the same per-org entity as SharePoint."""
    return _active_account(TOOLKIT, sharepoint_entity(organization_id))


def _quota(data) -> bool:
    """Google reports rate limits as 403, which would otherwise look like a permissions failure."""
    return "rate limit" in _err(data).lower() or "ratelimit" in str(data).lower()


def _call(method: str, url: str, account_id: str, body: dict | None = None):
    """One Drive API call. Returns the JSON, or None for a 404 so callers can treat it as absent."""
    pr = get_composio_client().tools.proxy(
        endpoint=url, method=method, body=body, connected_account_id=account_id,
        parameters=_JSON if body is not None else None,
    )
    d = pr if isinstance(pr, dict) else pr.model_dump()
    status, data = _int_status(d), d.get("data")
    if status is None or 200 <= status < 300:
        return data
    if status == 404:
        return None
    if status in _RETRYABLE or (status == 403 and _quota(data)):
        raise SharePointTransientError(f"Google Drive {method} {status}: {_err(data)}")
    raise SharePointWriteError(f"Google Drive {method} {status}: {_err(data)}")


def _list(url: str, key: str, account_id: str) -> Iterator[dict]:
    """Every row of a paged list. A 404 (the parent is gone) raises rather than looking empty."""
    token = ""
    while True:
        data = _call("GET", f"{url}&pageToken={quote(token)}" if token else url, account_id)
        if data is None:
            raise SharePointReadError("That Google Drive folder could not be read.")
        yield from data.get(key) or []
        token = data.get("nextPageToken") or ""
        if not token:
            return


def _children(parent: str, drive_id: str, account_id: str, extra: str = "", fields: str = _ITEM) -> Iterator[dict]:
    query = f"'{parent}' in parents and trashed=false{extra}"
    params = {"q": query, "pageSize": 1000, "fields": f"nextPageToken,files({fields})",
              "includeItemsFromAllDrives": "true", "supportsAllDrives": "true"}
    if drive_id != MY_DRIVE:
        params.update(corpora="drive", driveId=drive_id[len(PREFIX):])
    return _list(f"{_API}/files?{urlencode(params)}", "files", account_id)


def _literal(name: str) -> str:
    return name.replace("\\", "\\\\").replace("'", "\\'")


def _child(parent: str, name: str, drive_id: str, account_id: str, folder: bool) -> dict | None:
    kind = "=" if folder else "!="
    extra = f" and name='{_literal(name)}' and mimeType{kind}'{_FOLDER}'"
    return next(_children(parent, drive_id, account_id, extra), None)


def ensure_folder(parent: str, name: str, account_id: str, drive_id: str = MY_DRIVE) -> dict:
    """The child folder `name`, created if missing. Drive allows duplicate names, so look first."""
    found = _child(parent, name, drive_id, account_id, folder=True)
    if found:
        return found
    body = {"name": name, "mimeType": _FOLDER, "parents": [parent]}
    made = _call("POST", f"{_API}/files?{_ALL_DRIVES}&fields={_ITEM}", account_id, body)
    if not made or not made.get("id"):
        raise SharePointWriteError(f"Could not create the Google Drive folder '{name}'.")
    return made


def upload(parent: str, filename: str, content: bytes, account_id: str, drive_id: str = MY_DRIVE) -> dict:
    """Put a file in a folder. The same name replaces its content, keeping the file's id and links,
    and Drive keeps the old copy in its version history. Never converted to a Google format: the
    file stays .docx or .xlsx so the package can be read back exactly as people left it."""
    safe = _sanitize_filename(filename)
    kind = mimetypes.guess_type(safe)[0] or "application/octet-stream"
    found = _child(parent, safe, drive_id, account_id, folder=False)
    item = found or _call("POST", f"{_API}/files?{_ALL_DRIVES}&fields=id", account_id,
                          {"name": safe, "parents": [parent], "mimeType": kind})
    if not item or not item.get("id"):
        raise SharePointWriteError(f"Could not create '{safe}' in Google Drive.")
    url = f"{_UPLOAD}/files/{item['id']}?uploadType=media&{_ALL_DRIVES}&fields={_ITEM}"
    if len(content) > _CHUNK:
        status, data = _resumable(item["id"], content, kind, safe, account_id)
    else:
        status, data = _send(url, "PATCH", content, kind, safe, account_id)
    if isinstance(data, dict) and data.get("id") and (status is None or 200 <= status < 300):
        return {"id": data["id"], "name": data.get("name") or safe, "webUrl": data.get("webViewLink"),
                "size": len(content), "parentReference": {"driveId": drive_id}}
    if status in _RETRYABLE:
        raise SharePointTransientError(f"Google Drive upload of '{safe}' -> {status}: {_err(data)}")
    raise SharePointWriteError(f"Google Drive upload of '{safe}' failed (status={status}): {_err(data)}")


# Composio's proxy refuses a request body much over 4 MB, and a solicitation PDF is often bigger,
# so a large file goes up in pieces. Google wants each piece to be a multiple of 256 KB.
_CHUNK = 8 * 256 * 1024


def _send(url: str, method: str, content: bytes, kind: str, name: str, account_id: str,
          headers: dict | None = None) -> tuple[int | None, object]:
    params = [{"name": k, "value": v, "type": "header"} for k, v in (headers or {}).items()]
    try:
        pr = get_composio_client().client.tools.proxy(
            endpoint=url, method=method, connected_account_id=account_id,
            binary_body={"base64": base64.b64encode(content).decode(), "content_type": kind},
            **({"parameters": params} if params else {}),
        )
    except Exception as exc:  # noqa: BLE001 — a Composio-level failure; treat as transient
        raise SharePointTransientError(f"Google Drive upload of '{name}' errored: {exc}")
    d = pr if isinstance(pr, dict) else pr.model_dump()
    return _int_status(d), d.get("data") if d.get("data") is not None else d.get("headers")


def _resumable(item_id: str, content: bytes, kind: str, name: str, account_id: str) -> tuple[int | None, object]:
    """Google's resumable upload: open a session, then send the file a piece at a time. Every piece
    but the last is answered 308; the last returns the file."""
    start = (f"{_UPLOAD}/files/{item_id}?uploadType=resumable&{_ALL_DRIVES}&fields={_ITEM}")
    pr = get_composio_client().client.tools.proxy(
        endpoint=start, method="PATCH", connected_account_id=account_id, body={},
        parameters=_JSON + [
            {"name": "X-Upload-Content-Type", "value": kind, "type": "header"},
            {"name": "X-Upload-Content-Length", "value": str(len(content)), "type": "header"},
        ],
    )
    d = pr if isinstance(pr, dict) else pr.model_dump()
    session = {k.lower(): v for k, v in (d.get("headers") or {}).items()}.get("location")
    if not session:
        raise SharePointWriteError(f"Google Drive wouldn't start the upload of '{name}' "
                                   f"(status={_int_status(d)}): {_err(d.get('data'))}")
    total = len(content)
    status, data = None, None
    for at in range(0, total, _CHUNK):
        piece = content[at:at + _CHUNK]
        rng = f"bytes {at}-{at + len(piece) - 1}/{total}"
        status, data = _send(session, "PUT", piece, kind, name, account_id, {"Content-Range": rng})
        if status == 308:
            continue
        if status in _RETRYABLE:
            raise SharePointTransientError(f"Google Drive upload of '{name}' -> {status}: {_err(data)}")
        break
    return status, data


def _bytes(url: str, account_id: str) -> bytes:
    """A binary response comes back from the proxy as a short-lived link to the content."""
    pr = get_composio_client().client.tools.proxy(endpoint=url, method="GET", connected_account_id=account_id)
    d = pr if isinstance(pr, dict) else pr.model_dump()
    status, link = _int_status(d), (d.get("binary_data") or {}).get("url")
    if status is not None and not 200 <= status < 300:
        raise SharePointWriteError(f"Google Drive download failed (status={status}): {_err(d.get('data'))}")
    if link:
        with httpx.Client(timeout=120, follow_redirects=True) as client:
            resp = client.get(link)
            resp.raise_for_status()
            return resp.content
    data = d.get("data")
    if isinstance(data, str):  # small text files come back inline
        return data.encode()
    raise SharePointWriteError("Google Drive sent nothing back for that file.")


def download(organization_id: str, item_id: str) -> bytes:
    """A file's current bytes. Someone may have chosen "Save as Google Docs", so look at what it
    is now: Google's own formats are exported as Office files."""
    account_id = account(organization_id)
    if not account_id:
        raise RuntimeError("Google Drive is not connected for this organization.")
    meta = _call("GET", f"{_API}/files/{item_id}?{_ALL_DRIVES}&fields=mimeType", account_id)
    if not meta:
        raise SharePointWriteError(f"Google Drive has no file {item_id}: it may have been deleted.")
    kind = meta.get("mimeType") or ""
    if kind in _EXPORT:
        return _bytes(f"{_API}/files/{item_id}/export?mimeType={quote(_EXPORT[kind][0], safe='')}", account_id)
    if kind.startswith(_NATIVE):
        raise SharePointWriteError("That Google file type can't be downloaded.")
    return _bytes(f"{_API}/files/{item_id}?alt=media&{_ALL_DRIVES}", account_id)


def _roster(permissions: list[dict]) -> dict:
    emails: set[str] = set()
    org_wide = False
    for p in permissions:
        if p.get("type") in ("anyone", "domain"):
            org_wide = True
        elif p.get("emailAddress"):
            emails.add(p["emailAddress"].lower())
    return {"permitted_emails": sorted(emails), "org_wide": org_wide}


def _item_roster(item_id: str, account_id: str, owner: dict) -> dict:
    """Shared-drive items don't list their permissions inline, so ask. If Google won't say, only
    the connected account gets the file: never guess wider."""
    url = f"{_API}/files/{item_id}/permissions?{_ALL_DRIVES}&pageSize=100&fields=nextPageToken,{_PERMS}"
    try:
        return _roster(list(_list(url, "permissions", account_id)))
    except (SharePointWriteError, SharePointReadError) as exc:
        logger.warning("Google Drive: no permissions for %s: %s", item_id, exc)
        return owner


def _node(item: dict, parent_id: str, path: str, drive_id: str, roster: dict, name: str, folder: bool) -> dict:
    return {
        "id": item["id"], "type": "folder" if folder else "file", "name": name, "path": path,
        "web_url": item.get("webViewLink"), "parent_id": parent_id, "site_id": SITE_ID,
        "drive_id": drive_id, "size": int(item["size"]) if item.get("size") else None,
        "ext": name.rsplit(".", 1)[-1].lower() if not folder and "." in name else None,
        # Google's own formats have no checksum. Their modified time also moves on a rename, which
        # only costs one extra reading.
        "version": None if folder else (item.get("md5Checksum") or item.get("modifiedTime")),
        "modified": item.get("modifiedTime"), **roster,
    }


def _shown_name(item: dict) -> str | None:
    """The name a file is listed under, or None for what can't be read (forms, shortcuts, maps).
    A Google Doc gets the extension of the Office file it's exported as, so it's read like one."""
    kind, name = item.get("mimeType") or "", item.get("name") or ""
    if kind in _EXPORT:
        ext = f".{_EXPORT[kind][1]}"
        return name if name.lower().endswith(ext) else f"{name}{ext}"
    return None if kind.startswith(_NATIVE) else name


def _walk(drive_id: str, root: str, lib_id: str, lib_path: str, lib_roster: dict, account_id: str,
          owner: dict, with_acl: bool, excluded: set[str] | None, max_depth: int | None,
          room: int) -> list[dict]:
    nodes: list[dict] = []
    fields = f"{_ITEM},{_PERMS}" if with_acl else _ITEM
    stack = [(root, lib_id, lib_path, lib_roster, 0)]
    while stack and len(nodes) < room:
        ref, parent_id, parent_path, parent_roster, depth = stack.pop()
        for it in _children(ref, drive_id, account_id, fields=fields):
            folder = it.get("mimeType") == _FOLDER
            name = it.get("name") if folder else _shown_name(it)
            path = f"{parent_path}/{name}"
            if not name or _is_excluded(path, excluded):
                continue
            if not with_acl:
                roster = _EMPTY
            else:
                found = _roster(it["permissions"]) if it.get("permissions") else _item_roster(it["id"], account_id, owner)
                roster = {**found, "unique_acl": found["permitted_emails"] != parent_roster["permitted_emails"]}
            nodes.append(_node(it, parent_id, path, drive_id, roster, name, folder))
            if folder and (max_depth is None or depth < max_depth):
                stack.append((it["id"], it["id"], path, roster, depth + 1))
    return nodes


def crawl(account_id: str, with_acl: bool = True, excluded_paths: set[str] | None = None,
          max_depth: int | None = None, max_items: int = 8000) -> list[dict]:
    """My Drive and every shared drive, as the flat nodes the SharePoint crawl produces: the whole
    Drive is one "site" and each drive is a "library". Files shared with this account but kept in
    someone else's Drive aren't included; they belong to that person."""
    about = _call("GET", f"{_API}/about?fields=user(emailAddress)", account_id) or {}
    email = ((about.get("user") or {}).get("emailAddress") or "").lower()
    owner = {"permitted_emails": [email] if email else [], "org_wide": False}
    nodes: list[dict] = [{"id": SITE_ID, "type": "site", "name": SITE_NAME, "path": SITE_NAME,
                          "web_url": _MY_DRIVE_URL, "parent_id": None, "site_id": SITE_ID,
                          "drive_id": None, **_EMPTY}]

    shared = _list(f"{_API}/drives?pageSize=100&fields=nextPageToken,drives(id,name)", "drives", account_id)
    drives = [(MY_DRIVE, "root", "My Drive", _MY_DRIVE_URL)] + [
        (f"{PREFIX}{d['id']}", d["id"], d.get("name") or "Shared drive",
         f"https://drive.google.com/drive/folders/{d['id']}") for d in shared]
    for drive_id, root, name, web_url in drives:
        path = f"{SITE_NAME}/{name}"
        if _is_excluded(path, excluded_paths):
            continue
        roster = {**owner, "unique_acl": False}
        if with_acl and root != "root":
            roster = {**_item_roster(root, account_id, owner), "unique_acl": False}
        lib_id = f"{SITE_ID}|{drive_id}"
        nodes.append({"id": lib_id, "type": "library", "name": name, "path": path, "web_url": web_url,
                      "parent_id": SITE_ID, "site_id": SITE_ID, "drive_id": drive_id,
                      **(roster if with_acl else _EMPTY)})
        try:
            nodes.extend(_walk(drive_id, root, lib_id, path, roster, account_id, owner, with_acl,
                               excluded_paths, max_depth, max_items - len(nodes)))
        except (SharePointWriteError, SharePointReadError) as exc:  # one bad drive mustn't sink the rest
            logger.warning("Google Drive: crawl of %s failed: %s", name, exc)
    return nodes


def list_bid_documents(organization_id: str, folder: dict) -> list[dict]:
    """What's in each subfolder of a bid right now, so a file someone dropped in shows up too."""
    account_id = account(organization_id)
    if not account_id:
        raise SharePointNotConnectedError("Google Drive is not connected for this organization.")
    out: list[dict] = []
    for name, sub in (folder.get("subfolders") or {}).items():
        if not (sub or {}).get("id"):
            continue
        try:
            rows = list(_children(sub["id"], folder["drive_id"], account_id))
        except SharePointReadError:
            raise SharePointReadError("The bid's Google Drive folder could not be read (it may have "
                                      "been moved or deleted). Re-mark the opportunity as a Bid to "
                                      "recreate it.") from None
        for it in rows:
            is_folder = it.get("mimeType") == _FOLDER
            out.append({
                "subfolder": name, "id": it.get("id"), "name": it.get("name"),
                "web_url": it.get("webViewLink"),
                "edit_url": None if is_folder else it.get("webViewLink"),
                "size": int(it["size"]) if it.get("size") else None, "is_folder": is_folder,
                "modified": it.get("modifiedTime"),
            })
    return out


def file_to_bid_subfolder(organization_id: str, folder: dict | None, subfolder: str,
                          filename: str, content: bytes) -> dict | None:
    sub = ((folder or {}).get("subfolders") or {}).get(subfolder) or {}
    account_id = account(organization_id) if sub.get("id") else None
    if not account_id:
        return None
    item = upload(sub["id"], filename, content, account_id, folder["drive_id"])
    return {"sharepoint_url": item.get("webUrl"), "sharepoint_item_id": item.get("id")}


def file_to_library(organization_id: str, filename: str, content: bytes) -> dict | None:
    account_id = account(organization_id)
    if not account_id:
        return None
    folder = ensure_folder("root", LIBRARY_FOLDER, account_id)
    return upload(folder["id"], filename, content, account_id)


def provision_bid_folders(organization_id: str, opp: dict) -> dict:
    """The bid's folder tree, in the same pointer shape as SharePoint's. Reruns reuse the folders."""
    account_id = account(organization_id)
    if not account_id:
        raise RuntimeError("Google Drive is not connected for this organization.")
    bids = ensure_folder("root", BIDS_FOLDER, account_id)
    name = bid_folder_name(opp)
    top = ensure_folder(bids["id"], name, account_id)

    def sub(parent: str, title: str) -> dict:
        child = ensure_folder(parent, title, account_id)
        return {"id": child.get("id"), "web_url": child.get("webViewLink")}

    subfolders = {title: sub(top["id"], title) for title in BID_SUBFOLDERS}
    subfolders["PPQs"] = sub(subfolders["Response"]["id"], "PPQs")
    return {"drive_id": MY_DRIVE, "folder_id": top["id"], "name": name, "web_url": top.get("webViewLink"),
            "library": "My Drive", "subfolders": subfolders}
