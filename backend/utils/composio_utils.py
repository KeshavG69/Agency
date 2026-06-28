"""Composio integration — managed auth + tools for agents.

Minimal mirror of the Kroolo setup: a singleton Composio v3 client bound to the
custom AgnoProvider, plus helpers to (a) fetch Agno tools for a set of action
slugs and (b) connect a user's account via Composio's hosted OAuth (so we never
manage tokens ourselves).
"""
from __future__ import annotations

import logging
import threading
from typing import List, Optional

from composio import Composio

from app.settings import settings
from utils.composio_agno_provider import AgnoProvider

logger = logging.getLogger(__name__)

# --- Outlook action slugs (from Composio's OUTLOOK toolkit) ------------------
# What the Relation Agent needs: read conversations + read/write calendar.
OUTLOOK_READ_MAIL = ["OUTLOOK_LIST_MESSAGES", "OUTLOOK_GET_MESSAGE", "OUTLOOK_SEARCH_MESSAGES"]
OUTLOOK_READ_CALENDAR = ["OUTLOOK_LIST_EVENTS", "OUTLOOK_GET_SCHEDULE"]
OUTLOOK_WRITE_CALENDAR = ["OUTLOOK_CALENDAR_CREATE_EVENT"]
OUTLOOK_CONTACTS = ["OUTLOOK_LIST_USER_CONTACTS"]

# The Relation Agent's toolset.
RELATION_OUTLOOK_ACTIONS = (
    OUTLOOK_READ_MAIL + OUTLOOK_READ_CALENDAR + OUTLOOK_WRITE_CALENDAR + OUTLOOK_CONTACTS
)

_client: Optional[Composio] = None
_lock = threading.Lock()


def get_composio_client() -> Composio:
    """Singleton Composio v3 client bound to the custom AgnoProvider."""
    global _client
    if _client is None:
        with _lock:
            if _client is None:
                _client = Composio(api_key=settings.COMPOSIO_API_KEY, provider=AgnoProvider())
                logger.info("Initialized Composio client with AgnoProvider")
    return _client


def get_tools(actions: List[str], user_id: str) -> list:
    """Return Agno Toolkit objects for the given Composio action slugs."""
    client = get_composio_client()
    return client.tools.get(
        user_id=user_id,
        tools=list(actions),
    )


# Some providers' Composio toolkit slug differs from the name we use in the app.
_TOOLKIT_SLUG = {"sharepoint": "share_point"}


def _toolkit_slug(provider: str) -> str:
    return _TOOLKIT_SLUG.get(provider.lower(), provider.lower())


def sharepoint_entity(organization_id: str) -> str:
    """The Composio entity for an ORG's SharePoint connection.

    SharePoint is org-wide: an admin connects the company's tenant ONCE and the org's
    employees read it (RBAC-filtered by the stored ACL roster). So the entity is keyed
    to the ORGANIZATION — one connection per org — NOT to an individual user and NOT a
    single global constant (which would make every org share one connection).
    """
    org = (organization_id or "").strip()
    return f"sp-org-{org}" if org else "sp-org-default"


def auth_config_id_for(provider: str) -> str:
    """Resolve a provider's Composio auth_config_id from settings.

    Convention: COMPOSIO_{PROVIDER}_AUTH_CONFIG_ID — e.g. provider="outlook" ->
    settings.COMPOSIO_OUTLOOK_AUTH_CONFIG_ID. Add a new env var to support a new
    provider; no code change needed here.
    """
    return getattr(settings, f"COMPOSIO_{provider.upper()}_AUTH_CONFIG_ID", "") or ""


def connect_provider(
    provider: str, user_id: str, callback_url: Optional[str] = None
) -> dict:
    """Start the Composio-hosted OAuth flow for any provider (outlook, gmail, ...).

    Returns { provider, connected_account_id, auth_url } — send the user to auth_url
    once; after that, tools work for this user_id with no token management here.
    """
    auth_config_id = auth_config_id_for(provider)
    if not auth_config_id:
        raise ValueError(
            f"No auth_config_id configured for provider '{provider}'. "
            f"Set COMPOSIO_{provider.upper()}_AUTH_CONFIG_ID."
        )
    client = get_composio_client()
    req = client.connected_accounts.link(
        user_id=user_id,
        auth_config_id=auth_config_id,
        callback_url=callback_url,
    )
    return {"provider": provider, "connected_account_id": req.id, "auth_url": req.redirect_url}


def connection_status(provider: str, user_id: str) -> dict:
    """Whether this user has an active connection for `provider`.

    Returns { provider, connected, status, connected_account_id }. `connected` is
    True only when an ACTIVE account exists; otherwise reports the latest known
    status (e.g. INITIALIZING mid-OAuth), or None if never started.
    """
    client = get_composio_client()
    uid = user_id
    resp = client.connected_accounts.list(user_ids=[uid], toolkit_slugs=[_toolkit_slug(provider)])
    items = getattr(resp, "items", None) or []
    for acc in items:
        if getattr(acc, "status", None) == "ACTIVE":
            return {
                "provider": provider, "connected": True, "status": "ACTIVE",
                "connected_account_id": getattr(acc, "id", None),
            }
    if items:
        acc = items[0]
        return {
            "provider": provider, "connected": False,
            "status": getattr(acc, "status", None),
            "connected_account_id": getattr(acc, "id", None),
        }
    return {"provider": provider, "connected": False, "status": None, "connected_account_id": None}


def account_entity(connected_account_id: str) -> Optional[str]:
    """The Composio entity (user_id) that owns a connected account, or None.

    Used to authorize disconnects — you may only disconnect your OWN connection.
    """
    client = get_composio_client()
    try:
        acc = client.connected_accounts.get(connected_account_id)
        d = acc if isinstance(acc, dict) else acc.model_dump()
        return d.get("user_id") or d.get("entity_id")
    except Exception:  # noqa: BLE001
        return None


def disconnect_account(connected_account_id: str) -> dict:
    """Delete a connected account (revokes Composio's stored tokens)."""
    client = get_composio_client()
    client.connected_accounts.delete(connected_account_id)
    return {"disconnected": True, "connected_account_id": connected_account_id}


def _resp_data(resp) -> dict:
    """Composio's tools.execute returns a dict {'data': {...}}; be tolerant of objects too."""
    if isinstance(resp, dict):
        return resp.get("data") or {}
    return getattr(resp, "data", None) or {}


def _resp_ok(resp) -> tuple[bool, str | None]:
    """Pull (successful, error) out of a Composio execute response (dict or object)."""
    if isinstance(resp, dict):
        return bool(resp.get("successful", True)), resp.get("error")
    return bool(getattr(resp, "successful", True)), getattr(resp, "error", None)


def send_outlook_email(args: dict, user_id: str) -> dict:
    """Send one email via Composio Outlook (OUTLOOK_SEND_EMAIL).

    `args` already maps to the tool's params (see MailDraft.outlook_send_args).
    This is an OUTWARD action — only ever call it in response to an explicit
    human 'Send' click, never automatically. Returns {successful, error, data}.
    """
    client = get_composio_client()
    payload = dict(args)
    payload.setdefault("user_id", "me")  # the Graph mailbox ('me' = the connected user)
    resp = client.tools.execute(
        "OUTLOOK_SEND_EMAIL",
        payload,
        user_id=user_id,  # the Composio connected account
        dangerously_skip_version_check=True,
    )
    ok, err = _resp_ok(resp)
    return {"successful": ok, "error": err, "data": _resp_data(resp)}


def fetch_outlook_contacts(user_id: str, top: int = 999) -> list[dict]:
    """Pull the user's Outlook contacts via Composio (Microsoft Graph under the hood).

    Returns normalized dicts: { name, email, company, title }. This is the raw
    node feed for the knowledge graph (enrichment via Explorium happens next).
    """
    client = get_composio_client()
    resp = client.tools.execute(
        "OUTLOOK_LIST_USER_CONTACTS",
        {
            "user_id": "me",
            "top": top,
            "select": ["displayName", "emailAddresses", "companyName", "jobTitle"],
        },
        user_id=user_id,
        dangerously_skip_version_check=True,
    )
    data = _resp_data(resp)
    raw = data.get("value") or data.get("contacts") or data.get("items") or []
    out: list[dict] = []
    for c in raw:
        emails = c.get("emailAddresses") or []
        email = None
        if emails:
            first = emails[0]
            email = first.get("address") if isinstance(first, dict) else first
        out.append({
            "name": c.get("displayName") or c.get("name"),
            "email": email,
            "company": c.get("companyName"),
            "title": c.get("jobTitle"),
        })
    return out


def _addr(obj: dict) -> tuple[Optional[str], Optional[str]]:
    """Pull (email, name) out of a Graph recipient: {emailAddress: {address, name}}."""
    ea = (obj or {}).get("emailAddress") or {}
    return ea.get("address"), ea.get("name")


# Local-parts that are machines/role inboxes, not people.
_ROLE_LOCALPARTS = {
    "no-reply", "noreply", "no_reply", "donotreply", "do-not-reply", "notifications",
    "notification", "notify", "mailer", "mailer-daemon", "postmaster", "bounce", "bounces",
    "alert", "alerts", "info", "support", "team", "hello", "contact", "news", "newsletter",
    "updates", "update", "marketing", "sales", "billing", "help", "admin", "automated",
}


def _is_human(email: str) -> bool:
    """Heuristic: keep real people, drop no-reply / notification / role inboxes."""
    local = email.split("@", 1)[0].lower()
    if any(tok in local for tok in ("no-reply", "noreply", "no_reply", "donotreply", "do-not-reply")):
        return False
    if local in _ROLE_LOCALPARTS:
        return False
    return True


def fetch_outlook_correspondents(user_id: str, per_folder: int = 200) -> list[dict]:
    """Derive the real network from EMAIL HISTORY (not the address book).

    Aggregates inbound senders + outbound recipients into people, with how often
    (count = relationship strength) and how recently (last_seen). This is the node
    + edge-weight feed for the graph. Returns dicts: { name, email, count, last_seen }.
    """
    client = get_composio_client()
    uid = user_id
    agg: dict[str, dict] = {}
    own_domains: set[str] = set()  # the user's own domain(s) — auto-detected from sent mail

    def bump(email: Optional[str], name: Optional[str], when: Optional[str]) -> None:
        if not email:
            return
        email = email.lower()
        e = agg.setdefault(email, {"email": email, "name": name, "count": 0, "last_seen": None})
        e["count"] += 1
        if name and not e.get("name"):
            e["name"] = name
        if when and (e["last_seen"] is None or when > e["last_seen"]):
            e["last_seen"] = when

    # Inbound — who emailed you
    inbound = client.tools.execute(
        "OUTLOOK_QUERY_EMAILS",
        {"user_id": "me", "folder": "inbox", "top": per_folder,
         "select": ["from", "receivedDateTime"]},
        user_id=uid, dangerously_skip_version_check=True,
    )
    for m in _resp_data(inbound).get("value", []) or []:
        addr, name = _addr(m.get("from"))
        bump(addr, name, m.get("receivedDateTime"))

    # Outbound — who you emailed (also grab your own `from` to learn the org domain)
    sent = client.tools.execute(
        "OUTLOOK_LIST_SENT_ITEMS_MESSAGES",
        {"user_id": "me", "top": per_folder, "select": ["from", "toRecipients", "sentDateTime"]},
        user_id=uid, dangerously_skip_version_check=True,
    )
    for m in _resp_data(sent).get("value", []) or []:
        own_addr, _ = _addr(m.get("from"))
        if own_addr and "@" in own_addr:
            own_domains.add(own_addr.split("@", 1)[1].lower())
        for rec in m.get("toRecipients", []) or []:
            addr, name = _addr(rec)
            bump(addr, name, m.get("sentDateTime"))

    # Internal = our own domain(s), auto-detected from the mailbox's sent `from`.
    # We store EXTERNAL contacts only — colleagues aren't part of the BD network.
    out = []
    for e in agg.values():
        email = e["email"]
        if not _is_human(email):
            continue  # drop no-reply / notification / role inboxes
        domain = email.split("@", 1)[1] if "@" in email else ""
        if domain in own_domains:
            continue  # skip internal colleagues
        e["domain"] = domain
        e["external"] = True
        out.append(e)

    out.sort(key=lambda x: x["count"], reverse=True)
    return out
