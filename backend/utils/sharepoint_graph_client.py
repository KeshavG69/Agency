"""Microsoft Graph client for SharePoint — crawl structure + read ACL rosters.

One Graph connection (Composio `sharepoint_graph` toolkit, scopes Sites/Files/
GroupMember/Group/Directory.Read.All) crawls the structure + reads per-item
permissions + expands Entra/M365 groups to the ACTUAL people (transitiveMembers).
SharePoint *site* groups (Owners/Members/Visitors) aren't expandable via Graph, so
those few are expanded via SharePoint REST (the existing share_point connection) +
Graph for any M365 group nested inside. See the `sharepoint-acl-transport` memory.

Transport is Composio `tools.proxy` (needs a proxy-execute-enabled Composio key);
the raw token is never handled here.
"""
from __future__ import annotations

import logging
import re
from functools import lru_cache

from utils.composio_utils import get_composio_client

logger = logging.getLogger(__name__)

_GRAPH = "https://graph.microsoft.com/v1.0"
_ORG_WIDE_LINK_SCOPES = {"organization", "anonymous", "users"}
_EVERYONE = {"Everyone", "Everyone except external users"}
_GUID = re.compile(r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})", re.I)
_SP_ACCEPT = [{"name": "Accept", "value": "application/json;odata=nometadata", "type": "header"}]


def graph_account(user_id: str | None = None) -> str | None:
    """ACTIVE `sharepoint_graph` connected-account id for an org's entity (any active if None)."""
    return _active_account("sharepoint_graph", user_id)


def sp_rest_account(user_id: str | None = None) -> str | None:
    """ACTIVE `share_point` (REST) connected-account id for an org's entity — site-group expansion."""
    return _active_account("share_point", user_id)


@lru_cache(maxsize=64)
def _active_account(toolkit: str, user_id: str | None = None) -> str | None:
    """The ACTIVE connected-account id for a toolkit, scoped to `user_id` (the org's
    SharePoint entity). With no user_id, returns any active account (legacy/dev)."""
    c = get_composio_client()
    kwargs: dict = {"toolkit_slugs": [toolkit]}
    if user_id:
        kwargs["user_ids"] = [user_id]
    accs = c.connected_accounts.list(**kwargs)
    items = accs.items if hasattr(accs, "items") else accs
    for a in items:
        d = a if isinstance(a, dict) else a.model_dump()
        if d.get("status") == "ACTIVE":
            return d.get("id")
    return None


def graph_get(url: str, account_id: str | None = None) -> dict | None:
    account_id = account_id or graph_account()
    if not account_id:
        raise RuntimeError("No ACTIVE sharepoint_graph connection — connect it first.")
    if not url.startswith("http"):
        url = f"{_GRAPH}/{url.lstrip('/')}"
    c = get_composio_client()
    pr = c.tools.proxy(endpoint=url, method="GET", connected_account_id=account_id)
    d = pr if isinstance(pr, dict) else pr.model_dump()
    return d.get("data")


def _sp_rest_get(site_base: str, path: str, sp_account: str) -> dict | None:
    c = get_composio_client()
    pr = c.tools.proxy(endpoint=f"{site_base}/_api/{path}", method="GET",
                       connected_account_id=sp_account, parameters=_SP_ACCEPT)
    d = pr if isinstance(pr, dict) else pr.model_dump()
    return d.get("data")


def _paged(url: str, account_id: str, cap: int = 5000):
    seen = 0
    while url and seen < cap:
        data = graph_get(url, account_id) or {}
        for v in data.get("value", []) or []:
            yield v
            seen += 1
        url = data.get("@odata.nextLink")


@lru_cache(maxsize=8192)
def expand_group_emails(group_id: str, account_id: str) -> tuple[str, ...]:
    """All real member emails of an Entra/M365 group (flattens nested groups)."""
    out: set[str] = set()
    url = (f"{_GRAPH}/groups/{group_id}/transitiveMembers/microsoft.graph.user"
           "?$select=mail,userPrincipalName&$top=999")
    try:
        for u in _paged(url, account_id):
            email = (u.get("mail") or u.get("userPrincipalName") or "").lower()
            if email:
                out.add(email)
    except Exception as exc:  # noqa: BLE001
        logger.warning("expand_group_emails(%s) failed: %s", group_id, exc)
    return tuple(out)


@lru_cache(maxsize=2048)
def expand_site_group(site_base: str, sp_group_id: str, sp_account: str,
                      graph_account_id: str) -> tuple[tuple[str, ...], bool]:
    """Expand a SharePoint site group (via REST) → (member emails, org_wide).

    Members can be real users, nested Entra/M365 groups (federated-directory claim →
    expand via Graph), or the tenant-wide "Everyone …" claim (→ org_wide).
    """
    emails: set[str] = set()
    org_wide = False
    try:
        data = _sp_rest_get(
            site_base, f"web/sitegroups({sp_group_id})/users?$select=Email,LoginName,PrincipalType,Title",
            sp_account,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("expand_site_group(%s) failed: %s", sp_group_id, exc)
        return (), False
    for m in (data or {}).get("value", []) or []:
        pt = m.get("PrincipalType")
        title = m.get("Title") or ""
        email = (m.get("Email") or "").lower()
        login = m.get("LoginName") or ""
        if title in _EVERYONE:
            org_wide = True
        elif pt == 1 and email:  # user
            emails.add(email)
        elif pt in (4, 8):  # security / M365 group claim → expand via Graph
            gm = _GUID.search(login)
            if gm:
                emails.update(expand_group_emails(gm.group(1), graph_account_id))
    return tuple(emails), org_wide


def roster_for_item(drive_id: str, item_id: str, account_id: str,
                    site_base: str | None = None, sp_account: str | None = None) -> dict:
    """{permitted_emails:set, org_wide:bool, unique:bool} for a drive item.

    `/permissions` returns EFFECTIVE perms (inherited + unique). Grantee priority:
    Entra group (id → transitiveMembers) > SharePoint site group (REST expand) >
    real user (email). Org/anyone links + "Everyone" → org_wide.
    """
    perms = graph_get(f"drives/{drive_id}/items/{item_id}/permissions", account_id) or {}
    emails: set[str] = set()
    org_wide = False
    unique = False
    for p in perms.get("value", []) or []:
        if not p.get("inheritedFrom"):
            unique = True
        link = p.get("link")
        if link and link.get("scope") in _ORG_WIDE_LINK_SCOPES:
            org_wide = True
            continue
        grants = list(p.get("grantedToIdentitiesV2") or [])
        if p.get("grantedToV2"):
            grants.append(p["grantedToV2"])
        for gt in grants:
            if gt.get("group") and gt["group"].get("id"):
                emails.update(expand_group_emails(gt["group"]["id"], account_id))
            elif gt.get("siteGroup") and site_base and sp_account:
                e, o = expand_site_group(site_base, gt["siteGroup"]["id"], sp_account, account_id)
                emails.update(e)
                org_wide = org_wide or o
            elif gt.get("user") or gt.get("siteUser"):
                u = gt.get("user") or gt.get("siteUser")
                em = (u.get("email") or u.get("userPrincipalName") or "").lower()
                if em:
                    emails.add(em)
                elif u.get("displayName") in _EVERYONE:
                    org_wide = True
    return {"permitted_emails": emails, "org_wide": org_wide, "unique": unique}


def list_graph_sites(account_id: str | None = None) -> list[dict]:
    """Tenant sites (id, name, web_url) via Graph search."""
    account_id = account_id or graph_account()
    return [
        {"id": s.get("id"), "name": s.get("displayName") or s.get("name"), "web_url": s.get("webUrl")}
        for s in _paged(f"{_GRAPH}/sites?search=*&$select=id,displayName,name,webUrl", account_id)
    ]


def _site_base(web_url: str | None) -> str | None:
    if not web_url or "/sites/" not in web_url:
        return None
    host, scope = web_url.split("/sites/")[0], web_url.split("/sites/")[1].split("/")[0]
    return f"{host}/sites/{scope}"


def crawl_all_sites_graph(with_acl: bool = True, account_id: str | None = None,
                          sp_account: str | None = None) -> list[dict]:
    """Crawl EVERY team site (/sites/<scope>) in the tenant via Graph → flat nodes + ACL.

    `account_id` (Graph) and `sp_account` (REST) are the ORG's connected accounts — the
    caller resolves them from the org's SharePoint entity. Skips personal/OneDrive sites.
    """
    account_id = account_id or graph_account()
    if not account_id:
        raise RuntimeError("No ACTIVE sharepoint_graph connection — connect it first.")
    if sp_account is None and with_acl:
        sp_account = sp_rest_account()
    nodes: list[dict] = []
    for s in list_graph_sites(account_id):
        web = s.get("web_url") or ""
        if "/sites/" not in web:  # skip personal / root sites
            continue
        try:
            nodes.extend(
                crawl_site_graph(s["id"], s["name"] or web.split("/sites/")[1].split("/")[0],
                                 web, with_acl=with_acl, account_id=account_id, sp_account=sp_account)
            )
        except Exception as exc:  # noqa: BLE001 — one bad site must not sink the whole crawl
            logger.warning("crawl of site %s failed: %s", s.get("name"), exc)
    return nodes


def _drive_unique_map(drive_id: str, site_base: str, sp_account: str, account_id: str) -> dict:
    """{list_item_id(str): bool} HasUniqueRoleAssignments for every item in a drive —
    ONE bulk SharePoint REST call, so we only read /permissions for the few break-points."""
    out: dict[str, bool] = {}
    try:
        list_id = (graph_get(f"drives/{drive_id}/list?$select=id", account_id) or {}).get("id")
        if not list_id:
            return out
        url = f"web/lists(guid'{list_id}')/items?$select=Id,HasUniqueRoleAssignments&$top=5000"
        while url:
            data = _sp_rest_get(site_base, url, sp_account) or {}
            for x in data.get("value", []) or []:
                out[str(x.get("Id"))] = bool(x.get("HasUniqueRoleAssignments"))
            nxt = data.get("odata.nextLink") or data.get("@odata.nextLink")
            url = nxt.split("/_api/", 1)[1] if nxt and "/_api/" in nxt else None
    except Exception as exc:  # noqa: BLE001 — fall back to per-item reads if bulk fails
        logger.warning("_drive_unique_map(%s) failed: %s", drive_id, exc)
    return out


def crawl_site_graph(site_id: str, site_name: str, site_web_url: str | None = None,
                     with_acl: bool = True, account_id: str | None = None,
                     sp_account: str | None = None, max_items: int = 8000) -> list[dict]:
    """Crawl one site's libraries/folders/files via Graph → flat nodes (+ ACL roster).

    OPTIMIZED: a bulk HasUniqueRoleAssignments map per library means we read
    /permissions only for the library root + items that BREAK inheritance; everything
    else inherits its parent's roster. Turns N permission calls into ~(unique items).
    """
    account_id = account_id or graph_account()
    if sp_account is None and with_acl:
        sp_account = sp_rest_account()
    site_base = _site_base(site_web_url)
    can_acl = with_acl and bool(site_base) and bool(sp_account)
    nodes: list[dict] = []
    EMPTY = {"permitted_emails": [], "org_wide": False, "unique_acl": False}

    def _roster(drive_id, item_id, unique):
        r = roster_for_item(drive_id, item_id, account_id, site_base, sp_account)
        return {"permitted_emails": sorted(r["permitted_emails"]),
                "org_wide": r["org_wide"], "unique_acl": unique}

    nodes.append({"id": site_id, "type": "site", "name": site_name, "path": site_name,
                  "web_url": site_web_url, "parent_id": None, "site_id": site_id,
                  "drive_id": None, **EMPTY})

    drives = graph_get(f"sites/{site_id}/drives?$select=id,name,webUrl", account_id) or {}
    for dr in drives.get("value", []) or []:
        drive_id = dr["id"]
        unique_map = _drive_unique_map(drive_id, site_base, sp_account, account_id) if can_acl else {}
        # library base roster = the drive root's effective permissions
        lib_roster = EMPTY
        if can_acl:
            root = graph_get(f"drives/{drive_id}/root?$select=id", account_id) or {}
            if root.get("id"):
                lib_roster = _roster(drive_id, root["id"], False)
        lib_id = f"{site_id}|{drive_id}"
        nodes.append({"id": lib_id, "type": "library", "name": dr.get("name"),
                      "path": f"{site_name}/{dr.get('name')}", "web_url": dr.get("webUrl"),
                      "parent_id": site_id, "site_id": site_id, "drive_id": drive_id, **lib_roster})

        stack = [("root", lib_id, f"{site_name}/{dr.get('name')}", lib_roster)]
        while stack and len(nodes) < max_items:
            folder_ref, parent_id, parent_path, parent_roster = stack.pop()
            ref = "root/children" if folder_ref == "root" else f"items/{folder_ref}/children"
            kids = graph_get(
                f"drives/{drive_id}/{ref}?$select=id,name,size,folder,file,webUrl,sharepointIds",
                account_id) or {}
            for it in kids.get("value", []) or []:
                is_folder = "folder" in it
                name = it.get("name", "")
                path = f"{parent_path}/{name}"
                if can_acl:
                    lii = str((it.get("sharepointIds") or {}).get("listItemId"))
                    roster = _roster(drive_id, it["id"], True) if unique_map.get(lii) else {**parent_roster, "unique_acl": False}
                else:
                    roster = EMPTY
                nodes.append({
                    "id": it["id"], "type": "folder" if is_folder else "file",
                    "name": name, "path": path, "web_url": it.get("webUrl"),
                    "parent_id": parent_id, "site_id": site_id, "drive_id": drive_id,
                    "size": it.get("size"),
                    "ext": (name.rsplit(".", 1)[-1].lower() if not is_folder and "." in name else None),
                    **roster,
                })
                if is_folder:
                    stack.append((it["id"], it["id"], path, roster))
    return nodes
