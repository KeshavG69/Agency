"""Load the SharePoint Composio tools + their instructions for the Mail/Proposal agent.

Reads two configs (mirrors the Kroolo enterprise-fastapi pattern):
  agent_config/sharepoint_tools.json             -> which Composio actions the agent gets
  agent_config/sharepoint_tool_instructions.json -> per-action when/how-to-use text (for the prompt)
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Optional

from utils.composio_utils import get_tools

_CFG = Path(__file__).resolve().parent.parent / "agent_config"


@lru_cache(maxsize=1)
def sharepoint_action_slugs() -> tuple[str, ...]:
    cfg = json.loads((_CFG / "sharepoint_tools.json").read_text())
    return tuple(cfg["sharepoint_graph"]["agent"]["actions"])


@lru_cache(maxsize=1)
def sharepoint_tool_instructions() -> str:
    """Per-action usage text appended to the agent prompt so it knows when to call each tool."""
    instr = json.loads((_CFG / "sharepoint_tool_instructions.json").read_text())
    lines = [f"- {slug}: {v['instruction']}" for slug, v in instr.items()]
    return "SHAREPOINT TOOLS (Composio) — when/how to use each:\n" + "\n".join(lines)


def load_sharepoint_tools(user_id: Optional[str] = None) -> list:
    """Return the Composio SharePoint tools as Agno toolkits for the agent."""
    return get_tools(list(sharepoint_action_slugs()), user_id=user_id)


# Open a file by id, and nothing that searches: the connection is the admin's, so `search_sharepoint`
# stays the only way to find a file, and it applies each employee's access.
_GOOGLE_ACTIONS = ["GOOGLEDRIVE_GET_FILE_METADATA", "GOOGLEDRIVE_DOWNLOAD_FILE"]
_GOOGLE_INSTRUCTIONS = """GOOGLE DRIVE: this company's files are in Google Drive, not SharePoint. \
Ignore any SharePoint tool named above. `search_sharepoint` still finds its files. To open one, pass \
the item id it returns as the file id to GOOGLEDRIVE_GET_FILE_METADATA or GOOGLEDRIVE_DOWNLOAD_FILE."""


def load_storage_tools(organization_id: str) -> list:
    """The file tools for whichever storage this company has connected."""
    from utils.composio_utils import sharepoint_entity
    from utils.file_store import GOOGLE_DRIVE, provider

    entity = sharepoint_entity(organization_id)
    if provider(organization_id) == GOOGLE_DRIVE:
        return get_tools(_GOOGLE_ACTIONS, user_id=entity)
    return load_sharepoint_tools(entity)


def storage_tool_instructions(organization_id: str) -> str:
    from utils.file_store import GOOGLE_DRIVE, provider

    if provider(organization_id) == GOOGLE_DRIVE:
        return _GOOGLE_INSTRUCTIONS
    return sharepoint_tool_instructions()
