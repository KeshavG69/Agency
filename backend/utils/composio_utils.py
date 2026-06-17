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
OUTLOOK_CONTACTS = ["OUTLOOK_LIST_CONTACTS"]

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


def get_tools(actions: List[str], user_id: Optional[str] = None) -> list:
    """Return Agno Toolkit objects for the given Composio action slugs."""
    client = get_composio_client()
    return client.tools.get(
        user_id=user_id or settings.COMPOSIO_USER_ID,
        tools=list(actions),
    )


def connect_outlook(user_id: Optional[str] = None, callback_url: Optional[str] = None) -> dict:
    """Start the Composio-hosted OAuth flow for Outlook.

    Returns { connected_account_id, auth_url } — send the user to auth_url once;
    after that, tools work for this user_id with no token management on our side.
    """
    client = get_composio_client()
    req = client.connected_accounts.link(
        user_id=user_id or settings.COMPOSIO_USER_ID,
        auth_config_id=settings.COMPOSIO_OUTLOOK_AUTH_CONFIG_ID,
        callback_url=callback_url,
    )
    return {"connected_account_id": req.id, "auth_url": req.redirect_url}
