"""The small-model fallback for signatures the regex parser could not read.

The regex parser (utils/signature.py) is free and instant and handles the common,
well-formed signature block. But plenty of real signatures defeat it: multi-line role
descriptions, titles wrapped across lines, non-English layouts, "Jane — Director, Capture,
Acme (she/her)". Rather than grow the regex forever, we do what trycompai/crm does — let a
model read the block — but only as a FALLBACK, and with a small, cheap model
(`SIGNATURE_MODEL` = google/gemma-4-26b-a4b-it, a 26B MoE with ~4B active params, served
through OpenRouter). Regex first means we pay for the model only on the messages regex missed.

ONE call, JSON out, temperature 0 — the exact shape as utils/excel_ingest.py. The caller
decides WHEN to spend it (inbound only, no title already known, under a per-sweep budget);
this module only does the extraction.

The result is deliberately weaker evidence than a regex match (`llm.signature-extraction`
vs `outlook.signature-block`): the model inferred the title, it did not match a literal
block, so it should land as a SUGGESTION unless something corroborates it.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Optional

import httpx

from app.settings import settings
from client.langfuse_client import observe
from utils.signature import (
    derive_function,
    derive_seniority,
    html_to_text,
    is_automated_address,
    strip_quoted,
)

logger = logging.getLogger(__name__)

# A signature is at the BOTTOM of a message and short. Cap the text we send so a long quoted
# thread that slipped past strip_quoted cannot run up token cost — the tail is what matters.
_MAX_CHARS = 2000

_PROMPT = (
    "The text below is the tail of an email sent FROM {sender}.\n"
    "If it contains the SENDER'S OWN signature block, extract their details. If there is no "
    "clear personal signature, return nulls — do NOT guess a title from the email address or "
    "the company domain, and do NOT invent anything.\n\n"
    'Return ONLY JSON: {{"title": <job title or null>, "phone": <phone or null>, '
    '"company": <employer name or null>}}.\n\n'
    "--- email tail ---\n{body}\n--- end ---"
)


@dataclass(frozen=True)
class LLMSignature:
    title: Optional[str] = None
    phone: Optional[str] = None
    company: Optional[str] = None
    seniority: Optional[str] = None
    function: Optional[str] = None

    def is_useful(self) -> bool:
        return bool(self.title or self.phone)


def _clean(value) -> Optional[str]:
    if not isinstance(value, str):
        return None
    v = value.strip()
    # Models like to say "null"/"none"/"n/a" as text; treat those as no answer.
    if not v or v.lower() in {"null", "none", "n/a", "na", "unknown", "-"}:
        return None
    return v[:200]


@observe(name="signature-llm", as_type="generation")
def extract_signature_llm(
    body: Optional[str],
    sender_email: str,
    is_html: Optional[bool] = None,
    timeout: float = 30.0,
) -> Optional[LLMSignature]:
    """Read a signature the regex parser missed. Returns None on no-signature, an empty
    result, a machine sender, a missing API key, or any transport error — the caller treats
    all of those the same: no fact this time."""
    if not body or not body.strip():
        return None
    if is_automated_address(sender_email):
        return None
    if not settings.OPENROUTER_API_KEY:
        return None

    text = body
    if is_html or (is_html is None and "<" in body and ">" in body):
        text = html_to_text(body)
    text = strip_quoted(text).strip()
    if not text:
        return None
    # The signature is at the end; keep the tail, which is also the cheapest thing to send.
    text = text[-_MAX_CHARS:]

    try:
        resp = httpx.post(
            f"{settings.OPENROUTER_BASE_URL}/chat/completions",
            headers={
                "Authorization": f"Bearer {settings.OPENROUTER_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": settings.SIGNATURE_MODEL,
                "messages": [{
                    "role": "user",
                    "content": _PROMPT.format(sender=sender_email, body=text),
                }],
                "response_format": {"type": "json_object"},
                "temperature": 0,
            },
            timeout=timeout,
        )
        resp.raise_for_status()
        data = json.loads(resp.json()["choices"][0]["message"]["content"])
    except Exception as exc:  # noqa: BLE001 — a fallback that fails is just "no fact", never fatal
        logger.info("LLM signature extraction failed for %s: %s", sender_email, exc)
        return None

    if not isinstance(data, dict):
        return None
    title = _clean(data.get("title"))
    sig = LLMSignature(
        title=title,
        phone=_clean(data.get("phone")),
        company=_clean(data.get("company")),
        seniority=derive_seniority(title),
        function=derive_function(title),
    )
    return sig if sig.is_useful() else None
