"""Langfuse tracing — one place that turns LLM observability on for the whole backend.

WHY THIS IS ENOUGH: Agno agents (Analyst, Capture, Relation, Company-research) and LangChain
both call the `openai` SDK under the hood. Instrumenting that ONE SDK with OpenInference
captures every agent/model call and ships it to Langfuse — no per-agent wiring needed. The few
utilities that hit OpenRouter over raw `httpx` (the signature reader, the Excel column mapper,
image generation) don't touch the openai SDK, so they're traced explicitly with the `observe`
decorator re-exported here.

SAFE BY DEFAULT: with no Langfuse keys set, `init_langfuse()` is a no-op and `observe` becomes
the identity decorator, so nothing breaks when tracing isn't configured. Import this module
early (llm_client does) so the openai SDK is instrumented before any model runs.
"""
from __future__ import annotations

import logging
import threading
from typing import Any, Callable, Optional

from app.settings import settings

logger = logging.getLogger(__name__)

_enabled = False
_client: Any = None
_lock = threading.RLock()


def _identity_observe(*dargs: Any, **dkwargs: Any):
    """A stand-in for langfuse's @observe when tracing is off. Supports both bare `@observe`
    and parameterised `@observe(name=...)` usage, so decorated call sites don't care."""
    if len(dargs) == 1 and callable(dargs[0]) and not dkwargs:
        return dargs[0]

    def deco(fn: Callable) -> Callable:
        return fn

    return deco


def init_langfuse() -> Any:
    """Create the Langfuse client and instrument the openai SDK, ONCE. Idempotent and never
    raises — a tracing failure must never take the app down."""
    global _enabled, _client
    if _enabled or _client is not None:
        return _client
    with _lock:
        if _enabled or _client is not None:
            return _client
        if not (settings.LANGFUSE_PUBLIC_KEY and settings.LANGFUSE_SECRET_KEY):
            logger.info("Langfuse keys not set — LLM tracing disabled.")
            return None
        try:
            from langfuse import Langfuse
            from openinference.instrumentation.openai import OpenAIInstrumentor

            _client = Langfuse(
                public_key=settings.LANGFUSE_PUBLIC_KEY,
                secret_key=settings.LANGFUSE_SECRET_KEY,
                host=settings.LANGFUSE_BASE_URL or None,
            )
            # Patches the openai SDK's request methods -> every Agno agent and LangChain call
            # becomes a Langfuse generation on the tracer provider the client just registered.
            OpenAIInstrumentor().instrument()
            _enabled = True
            logger.info("Langfuse tracing enabled (host=%s).", settings.LANGFUSE_BASE_URL)
        except Exception as exc:  # noqa: BLE001 — tracing must never break the app
            logger.warning("Langfuse init failed (%s); tracing disabled.", exc)
            _client = None
    return _client


def get_langfuse() -> Any:
    """The live Langfuse client, or None when tracing is disabled."""
    return _client


def flush() -> None:
    """Flush buffered spans — call on graceful shutdown so nothing is lost."""
    if _client is not None:
        try:
            _client.flush()
        except Exception:  # noqa: BLE001
            pass


# Decide the decorator at IMPORT time: functions decorated with `observe` bind it when their
# module is imported, so init must have run first. Importing this module is therefore enough to
# turn tracing on for the whole process.
init_langfuse()
if _enabled:
    from langfuse import observe  # noqa: E402  (only meaningful once a client exists)
else:
    observe = _identity_observe

__all__ = ["init_langfuse", "get_langfuse", "flush", "observe"]
