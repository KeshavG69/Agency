"""Runtime patches for the agno framework.

Apply by importing this module once at process startup, before any
``agno`` agent or model is constructed (done in app/worker.py and app/server.py).

Patch: skip Anthropic ``ThinkingBlock`` replay when signature is missing.

agno's ``format_messages`` reconstructs an Anthropic ``ThinkingBlock`` from any
assistant message where ``reasoning_content`` and ``provider_data`` are both
non-None, pulling ``signature`` via ``provider_data.get("signature")``.
``provider_data`` also holds ``file_ids`` / ``context_management``, so
``.get("signature")`` can return None. The Anthropic SDK declares
``ThinkingBlock.signature: str`` (required), so Pydantic raises before the API
call. This patch nulls ``reasoning_content`` on any assistant message lacking a
valid string signature so the upstream replay path skips the thinking block.

Copied from the Kroolo enterprise backend (logger adapted to stdlib).
"""
import logging

logger = logging.getLogger(__name__)

try:
    from agno.utils.models import claude as _claude_utils
    from agno.models.anthropic import claude as _anthropic_claude
except ImportError:
    logger.warning("agno not importable; skipping Claude ThinkingBlock patch")
else:
    _orig_format_messages = _claude_utils.format_messages

    def _has_valid_signature(message) -> bool:
        pd = getattr(message, "provider_data", None)
        if not pd:
            return False
        sig = pd.get("signature")
        return isinstance(sig, str) and bool(sig)

    def _safe_format_messages(messages, *args, **kwargs):
        for m in messages:
            if (
                getattr(m, "role", None) == "assistant"
                and getattr(m, "reasoning_content", None) is not None
                and not _has_valid_signature(m)
            ):
                m.reasoning_content = None
        return _orig_format_messages(messages, *args, **kwargs)

    _claude_utils.format_messages = _safe_format_messages
    _anthropic_claude.format_messages = _safe_format_messages
    logger.info("Applied agno Claude ThinkingBlock signature patch")
