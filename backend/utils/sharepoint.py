"""SharePoint structure crawler (via Composio).

Walks the document structure — Sites → Document Libraries (drives) → Folders → Files
— and returns a flat list of nodes the graph layer turns into a tree. We capture the
STRUCTURE (names, paths, types, ids), not file contents — content is fetched on demand
later when the proposal agent actually needs a specific file.

Tools used (Composio `share_point` toolkit):
  SHARE_POINT_LIST_SITES            -> sites
  SHARE_POINT_LIST_DRIVES_REST_API  -> document libraries for a site
  SHARE_POINT_LIST_DRIVE_CHILDREN   -> children (folders/files) of a drive/folder (recursive)
"""
from __future__ import annotations

import logging
import os
from typing import Optional

from app.settings import settings
from utils.composio_utils import _resp_data, get_composio_client

logger = logging.getLogger(__name__)

_MAX_DEPTH = 8       # folder nesting safety cap
_MAX_NODES = 5000    # total-node safety cap


def _execute(slug: str, args: dict, user_id: str) -> dict:
    client = get_composio_client()
    resp = client.tools.execute(slug, args, user_id=user_id, dangerously_skip_version_check=True)
    return _resp_data(resp)


def _items(data: dict) -> list[dict]:
    """Pull the list of records out of a Composio/Graph response, whatever the key."""
    for k in ("value", "items", "drives", "sites", "results", "lists"):
        v = data.get(k)
        if isinstance(v, list):
            return v
    return []


def _first(d: dict, *keys, default=None):
    for k in keys:
        if d.get(k) not in (None, ""):
            return d[k]
    return default


def _site_scope(web_url: str) -> Optional[str]:
    """'https://tenant.sharepoint.com/sites/kroolo.com/...' -> 'kroolo.com'.
    Returns None for personal/root sites (no team document libraries to crawl)."""
    if not web_url or "/sites/" not in web_url:
        return None
    return web_url.split("/sites/", 1)[1].split("/", 1)[0]


def crawl_structure(user_id: str) -> list[dict]:
    """Return a flat list of structure nodes:
    { type: site|library|folder|file, id, name, path, parent_id,
      site_id, drive_id, web_url, ext, size }
    `id`/`parent_id` are prefixed so they're unique across types.
    """
    uid = user_id
    nodes: list[dict] = []

    sites = _items(_execute("SHARE_POINT_LIST_SITES", {"search": "*", "top": 200}, uid))
    for s in sites:
        if len(nodes) >= _MAX_NODES:
            break
        site_id = _first(s, "id", "Id", "SiteId")
        disp = _first(s, "displayName", "Title", "name", default="(site)")
        web_url = _first(s, "webUrl", "name", "url", default="")
        scope = _site_scope(web_url)
        if not scope:
            continue  # skip personal / root sites (only crawl team sites with libraries)

        site_node_id = f"site:{site_id}"
        nodes.append({
            "type": "site", "id": site_node_id, "name": disp, "path": scope,
            "parent_id": None, "site_id": site_id, "drive_id": None, "web_url": web_url,
        })

        drives = _items(_execute(
            "SHARE_POINT_LIST_DRIVES_REST_API",
            {"site_name": scope, "select": "id,name,webUrl,driveType", "top": 100}, uid,
        ))
        for d in drives:
            drive_id = _first(d, "id", "Id")
            lib_name = _first(d, "name", "Name", default="(library)")
            lib_node_id = f"lib:{drive_id}"
            lib_path = f"{scope}/{lib_name}"
            nodes.append({
                "type": "library", "id": lib_node_id, "name": lib_name,
                "path": lib_path, "parent_id": site_node_id,
                "site_id": site_id, "drive_id": drive_id, "web_url": _first(d, "webUrl"),
            })
            _crawl_folder(uid, drive_id, None, lib_node_id, lib_path, site_id, nodes, depth=0)

        # SharePoint LISTS (the other data type) — custom data lists (tasks, trackers,
        # registers, etc.). Skip document libraries (BaseType 1, captured above as drives)
        # and hidden system lists (galleries, app data — pure infrastructure noise).
        lists = _items(_execute("SHARE_POINT_LIST_ALL_LISTS", {"site_name": scope}, uid))
        for L in lists:
            if L.get("Hidden") or L.get("BaseType") == 1:
                continue
            list_id = _first(L, "Id", "id")
            list_title = _first(L, "Title", "DisplayName", "name", default="(list)")
            nodes.append({
                "type": "list", "id": f"list:{site_id}:{list_id}", "name": list_title,
                "path": f"{scope}/lists/{list_title}", "parent_id": site_node_id,
                "site_id": site_id, "drive_id": None,
                "list_template": L.get("BaseTemplate"), "item_count": L.get("ItemCount"),
                "web_url": _first(L, "DefaultViewUrl", "webUrl"),
            })

    logger.info("SharePoint crawl produced %d structure nodes", len(nodes))
    return nodes


def _crawl_folder(uid, drive_id, folder_id, parent_node_id, parent_path,
                  site_id, nodes: list[dict], depth: int) -> None:
    if depth > _MAX_DEPTH or len(nodes) >= _MAX_NODES:
        return
    args = {"drive_id": drive_id, "top": 200,
            "select": "id,name,size,folder,file,webUrl"}
    if folder_id:
        args["folder_id"] = folder_id
    children = _items(_execute("SHARE_POINT_LIST_DRIVE_CHILDREN", args, uid))

    for it in children:
        if len(nodes) >= _MAX_NODES:
            return
        item_id = _first(it, "id", "Id")
        name = _first(it, "name", "Name", default="(item)")
        path = f"{parent_path}/{name}"
        is_folder = it.get("folder") is not None
        node_id = f"item:{drive_id}:{item_id}"
        if is_folder:
            nodes.append({
                "type": "folder", "id": node_id, "name": name, "path": path,
                "parent_id": parent_node_id, "site_id": site_id, "drive_id": drive_id,
                "web_url": _first(it, "webUrl"),
            })
            _crawl_folder(uid, drive_id, item_id, node_id, path, site_id, nodes, depth + 1)
        else:
            nodes.append({
                "type": "file", "id": node_id, "name": name, "path": path,
                "parent_id": parent_node_id, "site_id": site_id, "drive_id": drive_id,
                "web_url": _first(it, "webUrl"), "size": it.get("size"),
                "ext": os.path.splitext(name)[1].lstrip(".").lower() or None,
            })
