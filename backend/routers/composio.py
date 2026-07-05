"""Composio integration — connect / status / disconnect for any provider.

Powers the "Connect" buttons. The user hits /connect with a provider (outlook,
gmail, ...); we hand back a Composio-hosted OAuth URL; they authorize; Composio
stores the tokens. After that, agents call that provider's tools for this
user_id with no token management here. Provider-generic, like the Kroolo
enterprise-fastapi composio router.
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.settings import settings
from auth.dependencies import get_current_user
from utils.composio_utils import (
    account_entity,
    auth_config_id_for,
    classify_network,
    connect_provider,
    connection_status,
    delete_outlook_message_triggers,
    disconnect_account,
    ensure_outlook_message_trigger,
    fetch_outlook_network,
    sharepoint_entity,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/composio", tags=["composio"])

# Personal integrations are connected per-employee (their OWN mailbox); org
# integrations are connected once by an admin and shared tenant-wide.
PERSONAL_PROVIDERS = {"outlook", "gmail"}
# "sharepoint" (Graph) + "sharepoint_rest" (SharePoint REST, for exact site-group emails)
# are TWO Composio connections chained under one "Connect Library" click — see
# SHAREPOINT_STAGES below and the sharepoint_graph_client module docstring for why both
# are needed (Graph alone can't resolve native SharePoint site-group membership).
ORG_PROVIDERS = {"sharepoint", "sharepoint_rest"}
SHAREPOINT_STAGES = ["sharepoint", "sharepoint_rest"]


class ConnectRequest(BaseModel):
    provider: str = "outlook"  # outlook | gmail | sharepoint
    callback_url: Optional[str] = None  # where Composio redirects after auth


class DisconnectRequest(BaseModel):
    connected_account_id: str


class IngestContactsRequest(BaseModel):
    # The candidate contact objects the user ticked in the review dialog (each carries
    # email/name/company/title/count/… as returned by the preview endpoint).
    contacts: list[dict]


def _require_config(provider: str) -> None:
    if not settings.COMPOSIO_API_KEY:
        raise HTTPException(status_code=500, detail="COMPOSIO_API_KEY is not configured.")
    if not auth_config_id_for(provider):
        raise HTTPException(
            status_code=400,
            detail=f"No auth config for provider '{provider}'. "
            f"Set COMPOSIO_{provider.upper()}_AUTH_CONFIG_ID.",
        )


def _composio_entity(provider: str, current_user: dict, *, write: bool) -> str:
    """The Composio entity (user_id) a connection / tool call runs under.

    Outlook (personal) is keyed to the ACTING EMPLOYEE'S EMAIL — each person
    connects and uses only their own mailbox; the client can never choose the
    identity. SharePoint (org-wide) runs under one shared org entity and may only
    be CONNECTED by an admin (the tenant-wide crawl reads that single connection).
    """
    p = provider.lower()
    if p in ORG_PROVIDERS:
        if write and current_user.get("role") != "admin":
            raise HTTPException(status_code=403, detail="Only admins can connect SharePoint.")
        # Per-ORG entity: one SharePoint connection per organization (admins connect it,
        # the org's employees read it). Orgs never share a connection.
        return sharepoint_entity(str(current_user.get("organization_id") or ""))
    email = (current_user.get("email") or "").lower()
    if not email:
        raise HTTPException(status_code=401, detail="No authenticated employee email.")
    return email


@router.post("/connect")
def connect(req: ConnectRequest, current_user: dict = Depends(get_current_user)) -> dict:
    """Start a provider's OAuth flow → returns { provider, auth_url, connected_account_id }.

    Outlook connects the acting employee's own mailbox; SharePoint is admin-only.
    """
    _require_config(req.provider)
    entity = _composio_entity(req.provider, current_user, write=True)
    try:
        return connect_provider(req.provider, user_id=entity, callback_url=req.callback_url)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:  # noqa: BLE001
        logger.error("Composio connect failed (%s): %s", req.provider, e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to start connect: {e}")


@router.get("/status")
def status(provider: str = "outlook", current_user: dict = Depends(get_current_user)) -> dict:
    """Whether the acting employee has an active connection for `provider`
    (Outlook = their own; SharePoint = the shared org connection). For button state."""
    _require_config(provider)
    entity = _composio_entity(provider, current_user, write=False)
    try:
        return connection_status(provider, user_id=entity)
    except Exception as e:  # noqa: BLE001
        logger.error("Composio status check failed (%s): %s", provider, e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to read status: {e}")


@router.post("/outlook/sync-contacts")
def sync_contacts(current_user: dict = Depends(get_current_user)) -> dict:
    """Download the acting employee's own Outlook contacts (the callback calls this)."""
    if not settings.COMPOSIO_API_KEY:
        raise HTTPException(status_code=500, detail="COMPOSIO_API_KEY is not configured.")
    from tasks.contacts_tasks import sync_outlook_contacts_task

    task = sync_outlook_contacts_task.delay(
        current_user["email"].lower(), str(current_user["organization_id"])
    )
    return {"sync_started": True, "task_id": task.id}


@router.get("/outlook/contacts/preview")
def preview_contacts(current_user: dict = Depends(get_current_user)) -> dict:
    """List the acting employee's candidate contacts, classified work/personal —
    for the review dialog. Fetches + classifies ONLY; nothing is enriched or graphed
    until the user confirms their selection via /outlook/contacts/ingest.

    Also (idempotently) ensures the mail-triage webhook trigger exists for this
    connection — this endpoint is the one place we already know Outlook is actively
    connected (fresh connect, or a manual Refresh), so it's the natural, low-frequency
    hook for trigger setup. Best-effort: never blocks the contact preview.
    """
    if not settings.COMPOSIO_API_KEY:
        raise HTTPException(status_code=500, detail="COMPOSIO_API_KEY is not configured.")
    email = (current_user.get("email") or "").lower()
    if not email:
        raise HTTPException(status_code=401, detail="No authenticated employee email.")
    try:
        st = connection_status("outlook", user_id=email)
        if st.get("connected_account_id"):
            ensure_outlook_message_trigger(st["connected_account_id"])
    except Exception as e:  # noqa: BLE001 — mail triage setup must never block contacts
        logger.warning("Mail-triage trigger setup failed for %s: %s", email, e)
    try:
        contacts = classify_network(fetch_outlook_network(user_id=email))
    except Exception as e:  # noqa: BLE001
        logger.error("Contact preview failed for %s: %s", email, e, exc_info=True)
        raise HTTPException(status_code=502, detail=f"Couldn't read your Outlook contacts: {e}")
    work = sum(1 for c in contacts if c.get("category") == "work")
    return {"contacts": contacts, "count": len(contacts), "work": work, "personal": len(contacts) - work}


@router.post("/outlook/contacts/ingest")
def ingest_contacts(
    req: IngestContactsRequest, current_user: dict = Depends(get_current_user)
) -> dict:
    """Enrich + graph ONLY the contacts the user picked in the review dialog."""
    if not settings.COMPOSIO_API_KEY:
        raise HTTPException(status_code=500, detail="COMPOSIO_API_KEY is not configured.")
    from tasks.contacts_tasks import ingest_selected_contacts_task

    task = ingest_selected_contacts_task.delay(
        current_user["email"].lower(), str(current_user["organization_id"]), req.contacts
    )
    return {"ingest_started": True, "selected": len(req.contacts), "task_id": task.id}


@router.post("/sharepoint/sync-structure")
def sync_structure(current_user: dict = Depends(get_current_user)) -> dict:
    """Kick off the SharePoint structure crawl → knowledge graph (admin only).

    Uses the one shared admin Graph connection; the crawl is tenant-wide.
    """
    if not settings.COMPOSIO_API_KEY:
        raise HTTPException(status_code=500, detail="COMPOSIO_API_KEY is not configured.")
    if current_user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Only admins can sync SharePoint.")
    from tasks.sharepoint_tasks import sync_sharepoint_structure_task

    task = sync_sharepoint_structure_task.delay(str(current_user["organization_id"]))
    return {"sync_started": True, "task_id": task.id}


@router.post("/sharepoint/disconnect")
def disconnect_sharepoint(current_user: dict = Depends(get_current_user)) -> dict:
    """Disconnect BOTH SharePoint connections (Graph + REST) in one action, admin-only.

    The "Connect Library" flow chains two Composio connections under one click (see
    SHAREPOINT_STAGES); disconnecting should undo both symmetrically, or a stale REST
    connection would silently linger (and reconnecting would think REST is already done).
    """
    if current_user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Only admins can disconnect SharePoint.")
    if not settings.COMPOSIO_API_KEY:
        raise HTTPException(status_code=500, detail="COMPOSIO_API_KEY is not configured.")
    org = str(current_user.get("organization_id") or "")
    entity = sharepoint_entity(org)
    disconnected: list[str] = []
    failed: list[str] = []
    for stage in SHAREPOINT_STAGES:
        try:
            st = connection_status(stage, entity)
        except Exception as e:  # noqa: BLE001 — still try the other stage + graph purge
            logger.warning("SharePoint disconnect (%s) status check failed for org %s: %s", stage, org, e)
            failed.append(stage)
            continue
        caid = st.get("connected_account_id")
        if not caid:
            continue  # this stage was never connected — nothing to undo
        try:
            disconnect_account(caid)
            disconnected.append(stage)
        except Exception as e:  # noqa: BLE001
            logger.warning("SharePoint disconnect (%s) failed for org %s: %s", stage, org, e)
            failed.append(stage)

    try:
        from client.sharepoint_graph import clear_structure

        clear_structure(org)
        library_cleared = True
    except Exception as e:  # noqa: BLE001
        logger.warning("Graph purge after SharePoint disconnect failed for org %s: %s", org, e)
        library_cleared = False

    # `failed` is non-empty only when a stage was ACTIVE and its delete call itself errored —
    # the caller (frontend) should surface that rather than silently reporting full success.
    return {"disconnected": disconnected, "failed": failed, "library_cleared": library_cleared}


@router.post("/disconnect")
def disconnect(req: DisconnectRequest, current_user: dict = Depends(get_current_user)) -> dict:
    """Disconnect a connected account by id.

    You may only disconnect your OWN connection (an Outlook account keyed to your
    email); the shared org connection (SharePoint) may only be disconnected by an
    admin. This prevents anyone from revoking another employee's — or the org's —
    integration using an account id leaked by /status.
    """
    if not settings.COMPOSIO_API_KEY:
        raise HTTPException(status_code=500, detail="COMPOSIO_API_KEY is not configured.")
    owner = account_entity(req.connected_account_id)
    if owner is None:
        raise HTTPException(status_code=404, detail="Connected account not found.")
    email = (current_user.get("email") or "").lower()
    is_admin = current_user.get("role") == "admin"
    is_own = owner == email
    is_org = owner == sharepoint_entity(str(current_user.get("organization_id") or ""))
    if not (is_own or (is_org and is_admin)):
        raise HTTPException(status_code=403, detail="You can only disconnect your own connection.")

    # Remove the mail-triage trigger BEFORE revoking the connected account — while the
    # account still definitely exists, so Composio can actually resolve/delete its triggers.
    # (A stale trigger on a revoked connection would keep firing webhooks against nothing.)
    # Track — don't swallow — whether this actually succeeded, unlike a plain best-effort
    # warning log, so a real cleanup failure is visible rather than silently orphaning a
    # trigger with no signal to the caller.
    trigger_removed = delete_outlook_message_triggers(req.connected_account_id) if is_own else None

    try:
        result = disconnect_account(req.connected_account_id)
    except Exception as e:  # noqa: BLE001
        logger.error("Composio disconnect failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to disconnect: {e}")
    if trigger_removed is not None:
        result["trigger_removed"] = trigger_removed
        if not trigger_removed:
            logger.warning(
                "Mail-triage trigger cleanup failed for %s — a stale trigger may remain.", owner
            )

    # Disconnecting removes the source, so purge the data it produced:
    #  - Outlook  -> this employee's contact subgraph
    #  - SharePoint -> the org's document-structure graph
    org = str(current_user.get("organization_id") or "")
    try:
        if is_own:
            from client.graph_store import clear_owner_graph

            clear_owner_graph(owner_email=email, organization_id=org)
            result["contacts_cleared"] = True
        elif is_org:
            from client.sharepoint_graph import clear_structure

            clear_structure(org)
            result["library_cleared"] = True
    except Exception as e:  # noqa: BLE001 — the disconnect itself succeeded; graph purge is best-effort
        logger.warning("Graph purge after disconnect failed for %s: %s", owner, e)
    return result
