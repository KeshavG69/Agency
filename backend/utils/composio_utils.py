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
# SharePoint is TWO chained connections: Graph (structure/ACL/write) + REST (site-group
# member emails — see sharepoint_graph_client module docstring). "sharepoint_rest" is the
# second stage's provider key, used only by the connect/status endpoints during chaining.
_TOOLKIT_SLUG = {"sharepoint": "sharepoint_graph", "sharepoint_rest": "share_point"}


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


def _paginate(tool_slug: str, params: dict, user_id: str, page_size: int) -> list[dict]:
    """Follow Microsoft Graph's `@odata.nextLink` (via `skip`) until fully exhausted.

    Composio's Outlook actions cap each response at `top` and surface the next page
    via `@odata.nextLink`, but don't paginate for you — callers that assume one page
    is the whole result silently undercount on any mailbox bigger than `top`. No
    artificial page cap: keeps going until Graph itself reports no more pages.
    """
    client = get_composio_client()
    out: list[dict] = []
    skip = 0
    while True:
        resp = client.tools.execute(
            tool_slug,
            {**params, "top": page_size, "skip": skip},
            user_id=user_id,
            dangerously_skip_version_check=True,
        )
        data = _resp_data(resp)
        page = data.get("value") or data.get("contacts") or data.get("items") or []
        out.extend(page)
        if not data.get("@odata.nextLink") or len(page) < page_size:
            break
        skip += page_size
    return out


def fetch_outlook_contacts(user_id: str, top: int = 999) -> list[dict]:
    """Pull the user's Outlook contacts via Composio (Microsoft Graph under the hood).

    Paginates through the full address book — a single page silently undercounts on
    any mailbox with more than `top` contacts. Returns normalized dicts:
    { name, email, company, title }. This is the raw node feed for the knowledge
    graph (enrichment via Explorium happens next).
    """
    raw = _paginate(
        "OUTLOOK_LIST_USER_CONTACTS",
        {"user_id": "me", "select": ["displayName", "emailAddresses", "companyName", "jobTitle"]},
        user_id,
        page_size=top,
    )
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

    # Inbound — who emailed you (paginated — a single page silently undercounts on
    # any mailbox with more than `per_folder` messages in the folder)
    inbound = _paginate(
        "OUTLOOK_QUERY_EMAILS",
        {"user_id": "me", "folder": "inbox", "select": ["from", "receivedDateTime"]},
        uid, page_size=per_folder,
    )
    for m in inbound:
        addr, name = _addr(m.get("from"))
        bump(addr, name, m.get("receivedDateTime"))

    # Outbound — who you emailed (also grab your own `from` to learn the org domain)
    sent = _paginate(
        "OUTLOOK_LIST_SENT_ITEMS_MESSAGES",
        {"user_id": "me", "select": ["from", "toRecipients", "sentDateTime"]},
        uid, page_size=per_folder,
    )
    for m in sent:
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


def fetch_outlook_network(user_id: str, per_folder: int = 200) -> list[dict]:
    """The full network = email-history correspondents MERGED with the Outlook address book.

    Correspondents give relationship strength (count / last_seen); the address book adds
    company + title and people you've saved but not yet emailed. Deduped by email. Same
    external-only + human filters as correspondents — colleagues (own domain) and
    role/no-reply inboxes are dropped. Returns dicts: { email, name, count, last_seen,
    domain, external, company?, title? }.
    """
    by_email: dict[str, dict] = {c["email"]: dict(c) for c in fetch_outlook_correspondents(user_id, per_folder)}

    own_domain = user_id.split("@", 1)[1].lower() if "@" in user_id else ""
    added = 0
    try:
        book = fetch_outlook_contacts(user_id)
    except Exception as exc:  # noqa: BLE001 — address book optional; keep correspondents
        logger.warning("Outlook address-book fetch failed for %s: %s", user_id, exc)
        book = []

    for c in book:
        email = (c.get("email") or "").strip().lower()
        if not email or "@" not in email or not _is_human(email):
            continue
        domain = email.split("@", 1)[1]
        if own_domain and domain == own_domain:
            continue  # internal colleague
        if email in by_email:
            e = by_email[email]  # merge: keep correspondence signal, fill richer fields
            if not e.get("name") and c.get("name"):
                e["name"] = c["name"]
            if c.get("company"):
                e["company"] = c["company"]
            if c.get("title"):
                e["title"] = c["title"]
        else:
            by_email[email] = {
                "email": email, "name": c.get("name"),
                "company": c.get("company"), "title": c.get("title"),
                "count": 0, "last_seen": None, "domain": domain, "external": True,
            }
            added += 1

    merged = list(by_email.values())
    merged.sort(key=lambda x: x.get("count", 0), reverse=True)
    logger.info(
        "Outlook network for %s: %d correspondents + %d address-book-only = %d total",
        user_id, len(by_email) - added, added, len(merged),
    )
    return merged


# Consumer / free mailbox providers — an address on one of these reads as a PERSONAL
# contact. Anything else (corporate, .gov, .mil, .edu, an org's own domain) is WORK.
FREE_EMAIL_DOMAINS = {
    "gmail.com", "googlemail.com", "yahoo.com", "ymail.com", "rocketmail.com",
    "hotmail.com", "outlook.com", "live.com", "msn.com", "hotmail.co.uk",
    "icloud.com", "me.com", "mac.com", "aol.com", "aim.com",
    "proton.me", "protonmail.com", "pm.me", "gmx.com", "gmx.net", "mail.com",
    "zoho.com", "yandex.com", "hey.com", "fastmail.com", "tutanota.com",
    "comcast.net", "verizon.net", "att.net", "sbcglobal.net", "cox.net",
    "bellsouth.net", "charter.net", "ymail.co.uk", "yahoo.co.uk", "yahoo.co.in",
}


def classify_contact(contact: dict) -> str:
    """"work" | "personal" for one contact, by its email domain (free provider -> personal)."""
    domain = (contact.get("domain") or (contact.get("email", "").split("@", 1)[-1])).lower()
    return "personal" if domain in FREE_EMAIL_DOMAINS else "work"


def classify_network(contacts: list[dict]) -> list[dict]:
    """Annotate each contact with a `category` ("work"/"personal") for the review dialog."""
    return [{**c, "category": classify_contact(c)} for c in contacts]


# --- mail triage: trigger lifecycle + message read/reply ---------------------

MAIL_TRIAGE_TRIGGER_SLUG = "OUTLOOK_MESSAGE_TRIGGER"


def ensure_outlook_message_trigger(user_id: str, connected_account_id: str) -> Optional[str]:
    """Idempotently ensure an OUTLOOK_MESSAGE_TRIGGER exists for this connected Outlook
    account — a real-time webhook Composio fires on every new inbound message, powering
    mail triage. Best-effort: returns the trigger id, or None if it couldn't be set up.
    NEVER raises — connecting Outlook must never fail just because trigger setup hiccups.

    `user_id` (the employee's email — the Composio entity that owns the connection) is
    REQUIRED on the create call: with 2FA enabled on the Composio project, trigger
    create/update rejects a bare `connected_account_id` with "user_id is required"
    (TriggerInstance_UserIdRequired) — Composio needs both, not just the account id.

    This is called on every manual Refresh (not just fresh connect), so it also SELF-HEALS
    duplicates: the list-then-create check isn't atomic (two overlapping calls can both see
    zero active triggers and both create one), so if more than one active trigger is ever
    found, all but one are deleted here rather than left to accumulate indefinitely."""
    client = get_composio_client()
    try:
        active = client.triggers.list_active(
            trigger_names=[MAIL_TRIAGE_TRIGGER_SLUG],
            connected_account_ids=[connected_account_id],
            show_disabled=False,
        )
        live = [item for item in active.items if getattr(item, "disabled_at", None) is None]
        if live:
            keep, *dupes = live
            for dupe in dupes:
                try:
                    client.triggers.delete(dupe.id)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Failed deleting duplicate trigger %s: %s", dupe.id, exc)
            return keep.id
        # The high-level `triggers.create(user_id=...)` does NOT forward user_id into the
        # real API call — it only uses it client-side to re-derive connected_account_id
        # (via a most-recently-created lookup that could pick the WRONG account), then
        # calls the same raw upsert with no user_id field at all. The raw upsert has no
        # user_id parameter either. So with 2FA on, `user_id` can only reach Composio
        # through `extra_body` on the raw call — the actual fix, not the high-level one.
        created = client.client.trigger_instances.upsert(
            slug=MAIL_TRIAGE_TRIGGER_SLUG,
            connected_account_id=connected_account_id,
            body_trigger_config_1={},
            extra_body={"user_id": user_id},
        )
        return created.trigger_id
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "ensure_outlook_message_trigger failed for account %s: %s", connected_account_id, exc
        )
        return None


def delete_outlook_message_triggers(connected_account_id: str) -> bool:
    """Remove any OUTLOOK_MESSAGE_TRIGGER instances for a connected account (Outlook
    disconnect) so a stale trigger doesn't keep firing webhooks for a revoked connection.

    Never raises (disconnect must succeed regardless of trigger cleanup), but DOES return
    whether cleanup actually succeeded — call this BEFORE revoking the connected account
    (while it still definitely exists) and surface a False result to the caller rather than
    swallowing it, or a failed cleanup silently leaves an orphaned trigger with no signal."""
    client = get_composio_client()
    try:
        active = client.triggers.list_active(
            trigger_names=[MAIL_TRIAGE_TRIGGER_SLUG],
            connected_account_ids=[connected_account_id],
            show_disabled=True,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "delete_outlook_message_triggers: listing failed for account %s: %s",
            connected_account_id, exc,
        )
        return False
    ok = True
    for item in active.items:
        try:
            client.triggers.delete(item.id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed deleting trigger %s: %s", item.id, exc)
            ok = False
    return ok


def fetch_outlook_message(message_id: str, user_id: str) -> dict:
    """One Outlook message's essentials for triage (sender, subject, snippet, ...).

    Raises on failure — the triage task decides how to handle it (retry / drop).
    """
    client = get_composio_client()
    resp = client.tools.execute(
        "OUTLOOK_GET_MESSAGE",
        {
            "message_id": message_id, "user_id": "me",
            "select": ["subject", "from", "bodyPreview", "receivedDateTime", "conversationId", "webLink"],
        },
        user_id=user_id, dangerously_skip_version_check=True,
    )
    ok, err = _resp_ok(resp)
    if not ok:
        raise RuntimeError(f"OUTLOOK_GET_MESSAGE failed: {err}")
    data = _resp_data(resp)
    sender_email, sender_name = _addr(data.get("from"))
    return {
        "message_id": message_id,
        "sender_email": (sender_email or "").strip().lower(),
        "sender_name": sender_name,
        "subject": data.get("subject") or "",
        "snippet": (data.get("bodyPreview") or "")[:280],
        "received_at": data.get("receivedDateTime"),
        "conversation_id": data.get("conversationId"),
        "web_link": data.get("webLink"),
    }


def create_outlook_draft_reply(message_id: str, comment: str, user_id: str) -> dict:
    """Create a threaded DRAFT reply in the user's OWN Outlook mailbox — it sits in their
    Drafts folder for THEM to review and send. We never send it ourselves; this mirrors
    the outward-action discipline of `send_outlook_email` (human-approved only), just one
    notch more cautious: not even a send call exists on this path, only a draft."""
    client = get_composio_client()
    resp = client.tools.execute(
        "OUTLOOK_CREATE_DRAFT_REPLY",
        {"message_id": message_id, "comment": comment, "user_id": "me"},
        user_id=user_id, dangerously_skip_version_check=True,
    )
    ok, err = _resp_ok(resp)
    if not ok:
        raise RuntimeError(f"OUTLOOK_CREATE_DRAFT_REPLY failed: {err}")
    data = _resp_data(resp)
    return {"id": data.get("id"), "web_link": data.get("webLink")}
