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
    connect_provider,
    connection_status,
    disconnect_account,
    sharepoint_entity,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/composio", tags=["composio"])

# Personal integrations are connected per-employee (their OWN mailbox); org
# integrations are connected once by an admin and shared tenant-wide.
PERSONAL_PROVIDERS = {"outlook", "gmail"}
ORG_PROVIDERS = {"sharepoint"}


class ConnectRequest(BaseModel):
    provider: str = "outlook"  # outlook | gmail | sharepoint
    callback_url: Optional[str] = None  # where Composio redirects after auth


class DisconnectRequest(BaseModel):
    connected_account_id: str


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
    try:
        return disconnect_account(req.connected_account_id)
    except Exception as e:  # noqa: BLE001
        logger.error("Composio disconnect failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to disconnect: {e}")
