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
    return tuple(cfg["share_point"]["agent"]["actions"])


@lru_cache(maxsize=1)
def sharepoint_tool_instructions() -> str:
    """Per-action usage text appended to the agent prompt so it knows when to call each tool."""
    instr = json.loads((_CFG / "sharepoint_tool_instructions.json").read_text())
    lines = [f"- {slug}: {v['instruction']}" for slug, v in instr.items()]
    return "SHAREPOINT TOOLS (Composio) — when/how to use each:\n" + "\n".join(lines)


def load_sharepoint_tools(user_id: Optional[str] = None) -> list:
    """Return the Composio SharePoint tools as Agno toolkits for the agent."""
    return get_tools(list(sharepoint_action_slugs()), user_id=user_id)
