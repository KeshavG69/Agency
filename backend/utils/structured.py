"""Coerce an agent's output into a Pydantic model.

Agno returns a parsed model when structured-output parsing fires, but with some
model/tool combinations the output comes back as a dict or a JSON string. This
normalizes all three so callers always get a typed object.
"""
from __future__ import annotations

import re
from typing import Type, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


def coerce_output(content, model_cls: Type[T]) -> T:
    if isinstance(content, model_cls):
        return content
    if isinstance(content, dict):
        return model_cls.model_validate(content)
    text = str(content).strip()
    # drop any <reasoning>/<think> blocks some models emit before the answer
    text = re.sub(r"<(reasoning|think)>.*?</\1>", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = text.strip()
    # strip ```json ... ``` fences
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text).strip()
    try:
        return model_cls.model_validate_json(text)
    except Exception:
        match = re.search(r"\{.*\}", text, re.DOTALL)  # find the JSON object in the text
        if match:
            return model_cls.model_validate_json(match.group(0))
        raise


# ---------------------------------------------------------------------------------------
# Retrying a structured agent run
# ---------------------------------------------------------------------------------------
# WHY. A model call can come back unusable for reasons that have nothing to do with the
# prompt: a dropped connection, a provider blip, a reasoning model that spent its budget
# thinking and returned empty content. Agno does not always raise for these — on a network
# drop it can hand back the literal string "Connection error." AS THE CONTENT, which then
# surfaces as a baffling JSON parse error. An end-to-end run lost a whole Call Brief that way.
#
# The Analyst already retried; the Brief, CRM and Mail agents did not, so one transient blip
# failed the whole request. A fresh run almost always succeeds.
#
# WHAT IS RETRIED. Both shapes of failure: the run raising, and the run "succeeding" with
# content that will not parse. A FRESH agent is built per attempt (agents hold per-run state).
#
# WHAT MUST NOT USE THIS. Anything with side effects per run. The Capture agent uploads the
# documents it generates, so retrying it would re-generate and re-upload them, leaving
# duplicate files behind. Retry is for idempotent, read-only drafting/judging calls.
import asyncio as _asyncio
import logging as _logging
import time as _time
from typing import Callable

_log = _logging.getLogger(__name__)

# Seconds to wait before attempt 2, 3, … — short, because the common failure is a transient
# blip and a rep is waiting on the result.
_BACKOFF = (2.0, 5.0)


def run_structured(
    build_agent: Callable[[], object], message: str, model_cls: Type[T], *,
    label: str, max_attempts: int = 3,
) -> T:
    """Run `build_agent().run(message)` and coerce to `model_cls`, retrying transient failures."""
    last: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            result = build_agent().run(message)
            return coerce_output(result.content, model_cls)
        except Exception as exc:  # noqa: BLE001 — raised OR unparseable: both are retryable
            last = exc
            _log.warning("%s: unusable output (attempt %d/%d): %s",
                         label, attempt, max_attempts, str(exc)[:200])
            if attempt < max_attempts:
                _time.sleep(_BACKOFF[min(attempt - 1, len(_BACKOFF) - 1)])
    raise last  # type: ignore[misc]


async def arun_structured(
    build_agent: Callable[[], object], message: str, model_cls: Type[T], *,
    label: str, max_attempts: int = 3,
) -> T:
    """Async twin of `run_structured`, for agents whose tools are async (`agent.arun`)."""
    last: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            result = await build_agent().arun(message)  # type: ignore[attr-defined]
            return coerce_output(result.content, model_cls)
        except Exception as exc:  # noqa: BLE001
            last = exc
            _log.warning("%s: unusable output (attempt %d/%d): %s",
                         label, attempt, max_attempts, str(exc)[:200])
            if attempt < max_attempts:
                await _asyncio.sleep(_BACKOFF[min(attempt - 1, len(_BACKOFF) - 1)])
    raise last  # type: ignore[misc]
