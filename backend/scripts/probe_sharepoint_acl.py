"""Probe the REAL SharePoint ACL data shapes for the Collecct tenant.

Pulls the live OAuth connection from Composio (never printing any token), picks a
team site, and hits the SharePoint REST ACL primitives + Microsoft Graph
permissions. Reports JSON FIELD SHAPES (key names + masked sample values) so the
graph ACL schema can match reality, and records which transport/scope permits
which reads (401/403 are key findings).

Transport: the raw OAuth bearer is NOT retrievable from Composio (the API returns
it redacted to "eyJ..."), so all REST calls go through Composio's authenticated
proxy (`tools.proxy`), which injects the server-held token. A direct-token attempt
is run once to document that the client-visible token is unusable.

Run:  uv run python scripts/probe_sharepoint_acl.py
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from utils.composio_utils import connection_status, get_composio_client, sharepoint_entity  # noqa: E402
from utils.sharepoint import _execute, _items, _first, _site_scope  # noqa: E402

# Per-org SharePoint entity to probe — set PROBE_ORG to your organization id.
_ENTITY = sharepoint_entity(os.getenv("PROBE_ORG", ""))


def _conn_meta() -> tuple[str, str, list[str], str]:
    """Return (redacted_token, subdomain, scopes, connected_account_id)."""
    st = connection_status("sharepoint", _ENTITY)
    caid = st["connected_account_id"]
    client = get_composio_client()
    val = client.connected_accounts.get(caid).state.val
    d = val.model_dump() if hasattr(val, "model_dump") else dict(val)
    return (d.get("access_token") or "", d.get("subdomain") or "",
            (d.get("scope") or "").split(), caid)


def _mask(v: Any) -> Any:
    """Replace leaf scalars with type+shape; keep dict keys + booleans intact."""
    if isinstance(v, dict):
        return {k: _mask(val) for k, val in v.items()}
    if isinstance(v, list):
        return [_mask(v[0]), f"...(+{len(v) - 1} more)"] if v else []
    if v is None or isinstance(v, bool):
        return v  # booleans (HasUniqueRoleAssignments) are the whole point
    if isinstance(v, (int, float)):
        return f"<{type(v).__name__}>"
    return f"<str len={len(str(v))}>"


def _field_names(v: Any) -> Any:
    if isinstance(v, dict):
        return {k: _field_names(val) for k, val in sorted(v.items())}
    if isinstance(v, list):
        return [_field_names(v[0])] if v else []
    return type(v).__name__


def _direct_get(url: str, token: str) -> dict:
    """GET with the client-visible (redacted) bearer — documents that it fails."""
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {token}",
        "Accept": "application/json;odata=nometadata"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return {"ok": True, "status": r.status,
                    "json": json.loads(r.read().decode("utf-8", "replace"))}
    except urllib.error.HTTPError as e:
        return {"ok": False, "status": e.code,
                "error": e.read().decode("utf-8", "replace")[:300]}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "status": None, "error": f"{type(e).__name__}: {e}"[:300]}


def _proxy_get(url: str, caid: str) -> dict:
    """GET via Composio's authenticated proxy (uses the SERVER-held real token)."""
    client = get_composio_client()
    try:
        resp = client.tools.proxy(
            endpoint=url, method="GET", connected_account_id=caid,
            parameters=[{"name": "Accept",
                         "value": "application/json;odata=nometadata", "in": "header"}],
        )
        d = resp.to_dict() if hasattr(resp, "to_dict") else dict(resp)
        status = d.get("status") or d.get("status_code")
        body = d.get("data")
        if isinstance(body, str):
            try:
                body = json.loads(body)
            except json.JSONDecodeError:
                pass
        if status and 200 <= int(status) < 300:
            return {"ok": True, "status": status, "json": body}
        return {"ok": False, "status": status, "error": json.dumps(body)[:600]}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "status": None, "error": f"{type(e).__name__}: {e}"[:400]}


def _report(label: str, res: dict) -> dict:
    print(f"\n{'='*78}\n[{label}]  HTTP {res.get('status')}  ok={res.get('ok')}")
    rec = {"label": label, "status": res.get("status"), "ok": res.get("ok")}
    if res.get("ok") and "json" in res:
        j = res["json"]
        print("FIELD SKELETON:")
        print(json.dumps(_field_names(j), indent=2)[:2200])
        print("SAMPLE (masked values):")
        print(json.dumps(_mask(j), indent=2)[:2200])
        rec["skeleton"] = _field_names(j)
    else:
        print("ERROR BODY (truncated):")
        print((res.get("error") or "")[:600])
        rec["error"] = (res.get("error") or "")[:300]
    return rec


def main() -> None:
    token, subdomain, scopes, caid = _conn_meta()
    print(f"subdomain={subdomain!r}  token_present={bool(token)}  "
          f"token_redacted={(len(token) < 40)}  n_scopes={len(scopes)}")
    has_graph = any("graph.microsoft.com" in s for s in scopes)
    print(f"graph_scopes_present={has_graph}  "
          f"sharepoint_rest_scopes_present={any(subdomain in s for s in scopes)}")

    rest_root = f"https://{subdomain}.sharepoint.com"
    uid = _ENTITY

    # 0) Transport check: direct (redacted) token vs Composio proxy.
    direct = _direct_get(f"{rest_root}/_api/web?$select=Title", token)
    proxied = _proxy_get(f"{rest_root}/_api/web?$select=Title", caid)
    print(f"\n[transport] direct-token _api/web -> HTTP {direct.get('status')} "
          f"({'OK' if direct.get('ok') else 'FAIL'}: "
          f"{(direct.get('error') or '')[:60]})  |  "
          f"composio-proxy _api/web -> HTTP {proxied.get('status')} "
          f"({'OK' if proxied.get('ok') else 'FAIL'})")

    def fetch(url: str) -> dict:
        return _proxy_get(url, caid)

    # 1) Pick a team site (with /sites/<scope>) from the crawl tool.
    sites = _items(_execute("SHARE_POINT_LIST_SITES", {"search": "*", "top": 50}, uid))
    chosen = (rest_root, None, None)
    for s in sites:
        web_url = _first(s, "webUrl", "name", "url", default="")
        if _site_scope(web_url):
            chosen = (web_url.rstrip("/"), _site_scope(web_url), _first(s, "id", "Id"))
            break
    site_base, scope, graph_site_id = chosen
    print(f"\nchosen_site web_url={site_base!r} scope={scope!r} graph_id={bool(graph_site_id)}")
    api = f"{site_base}/_api"

    records: list[dict] = []

    # 2) Web-level ACL primitives.
    for label, url in [
        ("web.HasUniqueRoleAssignments", f"{api}/web/HasUniqueRoleAssignments"),
        ("web.roleAssignments($expand=Member,RoleDefinitionBindings)",
         f"{api}/web/roleAssignments?$expand=Member,RoleDefinitionBindings"),
        ("web.siteusers", f"{api}/web/siteusers?$top=5"),
        ("web.sitegroups", f"{api}/web/sitegroups?$top=10"),
        ("web.roledefinitions", f"{api}/web/roledefinitions"),
        ("web.lists", f"{api}/web/lists?"
         f"$select=Id,Title,BaseType,Hidden,HasUniqueRoleAssignments&$top=30"),
    ]:
        records.append(_report(label, fetch(url)))

    # 2b) Drill into the first non-hidden list/library: list + item ACL.
    lists_res = fetch(f"{api}/web/lists?"
                      f"$select=Id,Title,BaseType,Hidden,HasUniqueRoleAssignments&$top=50")
    list_id = None
    if lists_res.get("ok"):
        for L in (lists_res["json"] or {}).get("value", []):
            if not L.get("Hidden") and L.get("BaseType") in (0, 1):
                list_id = L.get("Id")
                print(f"\nchosen_list Id={list_id} Title={L.get('Title')!r} "
                      f"BaseType={L.get('BaseType')} HasUnique={L.get('HasUniqueRoleAssignments')}")
                break
    if list_id:
        records.append(_report("list.HasUniqueRoleAssignments",
                               fetch(f"{api}/web/lists(guid'{list_id}')/HasUniqueRoleAssignments")))
        records.append(_report(
            "list.roleAssignments($expand=Member,RoleDefinitionBindings)",
            fetch(f"{api}/web/lists(guid'{list_id}')/roleAssignments"
                  f"?$expand=Member,RoleDefinitionBindings")))
        items_res = fetch(f"{api}/web/lists(guid'{list_id}')/items"
                          f"?$select=Id,HasUniqueRoleAssignments&$top=5")
        records.append(_report("list.items[].HasUniqueRoleAssignments", items_res))
        item_id = None
        if items_res.get("ok"):
            vals = (items_res["json"] or {}).get("value", [])
            if vals:
                item_id = vals[0].get("Id")
        if item_id is not None:
            records.append(_report(
                "list.item.roleAssignments($expand=Member,RoleDefinitionBindings)",
                fetch(f"{api}/web/lists(guid'{list_id}')/items({item_id})/roleAssignments"
                      f"?$expand=Member,RoleDefinitionBindings")))

    # 2c) A site group's membership (group -> users edge for the graph).
    grp_res = fetch(f"{api}/web/sitegroups?$select=Id,Title,LoginName&$top=30")
    group_id = None
    if grp_res.get("ok"):
        for g in (grp_res["json"] or {}).get("value", []):
            if g.get("Id"):
                group_id = g.get("Id")
                break
    if group_id is not None:
        records.append(_report(f"sitegroups({group_id})/users",
                               fetch(f"{api}/web/sitegroups({group_id})/users")))

    # 3) Microsoft Graph attempt via proxy (SharePoint-audience token -> expect 401).
    if graph_site_id:
        records.append(_report("GRAPH sites/{id} (cross-audience)",
                               fetch(f"https://graph.microsoft.com/v1.0/sites/{graph_site_id}")))
        records.append(_report(
            "GRAPH sites/{id}/permissions (cross-audience)",
            fetch(f"https://graph.microsoft.com/v1.0/sites/{graph_site_id}/permissions")))

    # 4) Summary.
    print(f"\n{'#'*78}\nSUMMARY (HTTP status -> endpoint)\n{'#'*78}")
    for r in records:
        print(f"  {r['status']!s:>5}  {'OK ' if r['ok'] else 'ERR'}  {r['label']}")


if __name__ == "__main__":
    main()
