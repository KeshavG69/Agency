"""Exa web search — direct REST helper.

A thin, programmatic wrapper over Exa's `/search` endpoint for code paths that
need web results OUTSIDE an Agno agent loop (the agents use `ExaTools` from
`utils.agno_tools` instead). Same key, different entry point.

  POST https://api.exa.ai/search
    headers: x-api-key: <EXA_API_KEY>
    body:    { query, numResults, type, contents: { text: {...} } }
    -> { results: [{ title, url, text, publishedDate, score, ... }] }
"""
from __future__ import annotations

import logging
from typing import Optional

import httpx

from app.settings import settings

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.exa.ai"


def _headers() -> dict:
    return {"x-api-key": settings.EXA_API_KEY, "Content-Type": "application/json"}


def exa_search(
    query: str,
    *,
    num_results: int = 8,
    search_type: str = "auto",
    include_text: bool = True,
    text_length_limit: int = 2000,
    include_domains: Optional[list[str]] = None,
    exclude_domains: Optional[list[str]] = None,
    timeout: float = 30.0,
) -> list[dict]:
    """Run one Exa search and return a list of result dicts.

    Each result: {title, url, text, published_date, score}. Text is included and
    truncated to `text_length_limit` chars unless `include_text=False`.

    Returns [] on any failure (missing key, quota/402, network) — logged, never
    raises, so callers can degrade gracefully the way enrichment does.
    """
    if not settings.EXA_API_KEY:
        logger.warning("exa_search called but EXA_API_KEY is unset")
        return []
    if not query.strip():
        return []

    body: dict = {"query": query, "numResults": num_results, "type": search_type}
    if include_text:
        body["contents"] = {"text": {"maxCharacters": text_length_limit}}
    if include_domains:
        body["includeDomains"] = include_domains
    if exclude_domains:
        body["excludeDomains"] = exclude_domains

    try:
        with httpx.Client(timeout=timeout) as client:
            r = client.post(f"{_BASE_URL}/search", headers=_headers(), json=body)
            r.raise_for_status()
    except httpx.HTTPStatusError as exc:
        # 402 = out of credits; surface it distinctly since that's the common case.
        detail = exc.response.text[:200] if exc.response is not None else ""
        logger.warning("Exa search failed (%s): %s", exc.response.status_code, detail)
        return []
    except httpx.HTTPError as exc:
        logger.warning("Exa search request error: %s", exc)
        return []

    results = r.json().get("results", []) or []
    return [
        {
            "title": item.get("title"),
            "url": item.get("url"),
            "text": item.get("text"),
            "published_date": item.get("publishedDate"),
            "score": item.get("score"),
        }
        for item in results
    ]
