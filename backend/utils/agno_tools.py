"""Agno tool factories for the agency's agents.

Mirrors the Exa + reasoning tool configuration used in the PriceIQ / Kroolo
backends so behavior is consistent across projects.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable, Dict, List, Optional

from agno.tools.exa import ExaTools
from agno.tools.reasoning import ReasoningTools

from app.settings import settings

logger = logging.getLogger(__name__)


def create_exa_web_search_tool(
    api_key: str = None,
    num_results: int = 15,
    include_domains: List = None,
    exclude_domains: List = None,
    get_contents: bool = True,
    find_similar: bool = True,
    answer: bool = False,
    text: bool = True,
    text_length_limit: int = 2000,
    summary: bool = False,
    livecrawl: str = "always",
    search_type: Optional[str] = None,
) -> ExaTools:
    """Create an Exa web search tool with customizable parameters.

    Args:
        api_key: Exa API key (defaults to settings.EXA_API_KEY)
        num_results: Maximum number of search results to return
        include_domains: Domains to specifically include
        exclude_domains: Domains to specifically exclude
        get_contents: Retrieve the full content of results
        find_similar: Enable find-similar
        answer: Generate an answer from results
        text: Return text content
        text_length_limit: Max length of text content per result
        summary: Generate a summary of results
        livecrawl: Crawling strategy ("always", "never", "fallback")
        search_type: Optional Exa search type hint (`type` parameter)

    Returns:
        Configured ExaTools instance.
    """
    return ExaTools(
        api_key=api_key or settings.EXA_API_KEY,
        num_results=num_results,
        include_domains=include_domains,
        exclude_domains=exclude_domains,
        enable_get_contents=get_contents,
        enable_find_similar=find_similar,
        enable_answer=answer,
        text=text,
        text_length_limit=text_length_limit,
        summary=summary,
        livecrawl=livecrawl,
        type=search_type,
    )


REASONING_CALL_LIMIT = 5
_REASONING_FUNCTION_NAMES = {"think", "analyze"}


async def limit_reasoning_hook(
    run_context: Any,
    function_name: str,
    function_call: Callable,
    arguments: Dict[str, Any],
):
    """tool_hooks-compatible hook that caps reasoning calls per response.

    Uses run_context, which Agno creates fresh for every run — guaranteed
    per-response isolation with no manual reset.
    """
    if function_name in _REASONING_FUNCTION_NAMES:
        count = getattr(run_context, "_reasoning_call_count", 0)
        if count >= REASONING_CALL_LIMIT:
            raise ValueError(
                f"Reasoning tool '{function_name}' has exceeded the limit of "
                f"{REASONING_CALL_LIMIT} calls for this response."
            )
        run_context._reasoning_call_count = count + 1
    result = function_call(**arguments)
    if asyncio.iscoroutine(result):
        return await result
    return result


def create_reasoning_tool(
    instructions: str = "only show reasoning no need for action confidence",
    add_instructions: bool = True,
    think: bool = True,
    analyze: bool = True,
    add_few_shot: bool = False,
    few_shot_examples: Optional[List[Dict[str, str]]] = None,
) -> ReasoningTools:
    """Create a reasoning tool with customizable parameters.

    Returns:
        Configured ReasoningTools instance.
    """
    try:
        reasoning_tool = ReasoningTools(
            instructions=instructions,
            add_instructions=add_instructions,
            enable_think=think,
            enable_analyze=analyze,
            add_few_shot=add_few_shot,
            few_shot_examples=few_shot_examples,
        )
        logger.info("Successfully created reasoning tool")
        return reasoning_tool
    except Exception as e:
        logger.error(f"Error creating reasoning tool: {e}")
        raise RuntimeError(f"Failed to create reasoning tool: {e}")
