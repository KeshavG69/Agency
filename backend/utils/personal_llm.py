"""Read the PERSONAL thread out of a message body with a small model.

The mail sweep already reads each contact's mail for their signature (job title, phone).
This does the second, softer read on the SAME message: the human details a relationship is
kept alive by — an interest they mentioned, something you did together, family, a date that
matters. "We played golf Saturday" is exactly the signal the relationship engine pulls on
and exactly the signal that lives nowhere structured.

SAME SHAPE AS signature_llm.py: one call, JSON out, temperature 0, small model, fail-soft
(any error / no key / empty = no facts this time). Two rules the prompt leans on hard:

  * Extract ONLY the sender's own personal facts, never the owner's, and never a work fact
    (those are the signature pass's job).
  * Ground every item in an explicit statement in the text. Return empty rather than infer.
    Over-extraction here becomes a wrong "hook" on a rep's card, so the model is told to
    prefer silence — and the output lands as a SUGGESTION anyway (non-primary evidence), so
    a human still confirms it before it is ever a fact.

The fields match the personal lane in facts_store (interests, shared_activity, family,
key_date). Each is a short list; the caller records each value as its own PROPOSED fact.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Optional

import httpx

from app.settings import settings
from client.langfuse_client import observe
from utils.signature import html_to_text, is_automated_address, strip_quoted

logger = logging.getLogger(__name__)

# Personal mentions can be anywhere in the body (not just the tail like a signature), but a
# long quoted thread is still noise — send a generous head after quote-stripping.
_MAX_CHARS = 4000

# Per field, a small ceiling so one chatty message cannot dump twenty "interests".
_MAX_PER_FIELD = 5

_FIELDS = ("interests", "shared_activity", "family", "key_date")

_PROMPT = (
    "Below is an email involving {sender} (the CONTACT). You are building a light personal "
    "profile of the CONTACT to help a colleague keep the relationship warm.\n\n"
    "Extract ONLY personal (non-work) facts the text states about the CONTACT — never about "
    "the sender's employer, never about the reader. Ground every item in an explicit "
    "statement; if the text does not clearly say it, leave it out. Prefer returning empty "
    "lists over guessing. Do NOT extract job titles, companies, or phone numbers.\n\n"
    "Return ONLY JSON with these keys, each a list of short strings (empty if none):\n"
    '  "interests":       hobbies / things they enjoy (e.g. "golf", "cycling")\n'
    '  "shared_activity": something they did WITH the reader, dated if stated '
    '(e.g. "played golf 2026-08-03")\n'
    '  "family":          family mentions (e.g. "daughter started at Purdue")\n'
    '  "key_date":        a personal date that matters (e.g. "birthday June 12")\n\n'
    "--- email ---\n{body}\n--- end ---"
)


@dataclass(frozen=True)
class PersonalFacts:
    interests: list[str] = field(default_factory=list)
    shared_activity: list[str] = field(default_factory=list)
    family: list[str] = field(default_factory=list)
    key_date: list[str] = field(default_factory=list)

    def is_empty(self) -> bool:
        return not (self.interests or self.shared_activity or self.family or self.key_date)

    def as_items(self) -> list[tuple[str, str]]:
        """(field, value) pairs, ready to record — one row per value (multi-valued lane)."""
        out: list[tuple[str, str]] = []
        for f in _FIELDS:
            for v in getattr(self, f):
                out.append((f, v))
        return out


def _clean_list(value) -> list[str]:
    """Coerce a model field into a de-duplicated list of short, real strings."""
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, str):
            continue
        v = item.strip()
        if not v or v.lower() in {"null", "none", "n/a", "na", "unknown", "-"}:
            continue
        key = v.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(v[:200])
        if len(out) >= _MAX_PER_FIELD:
            break
    return out


@observe(name="personal-llm", as_type="generation")
def extract_personal_facts_llm(
    body: Optional[str],
    sender_email: str,
    is_html: Optional[bool] = None,
    timeout: float = 30.0,
) -> Optional[PersonalFacts]:
    """Read personal facts about the sender from one message. Returns None on anything that
    means "no facts this time": empty body, a machine sender, no API key, a transport error,
    or an empty extraction — the caller treats them all the same."""
    if not body or not body.strip():
        return None
    if is_automated_address(sender_email):
        return None
    if not settings.llm_ready:
        return None

    text = body
    if is_html or (is_html is None and "<" in body and ">" in body):
        text = html_to_text(body)
    text = strip_quoted(text).strip()
    if not text:
        return None
    text = text[:_MAX_CHARS]

    try:
        resp = httpx.post(
            f"{settings.llm_base_url}/chat/completions",
            headers={
                "Authorization": f"Bearer {settings.llm_api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": settings.llm_model(settings.SIGNATURE_MODEL),
                "messages": [{
                    "role": "user",
                    "content": _PROMPT.format(sender=sender_email, body=text),
                }],
                "response_format": {"type": "json_object"},
                "temperature": 0,
                # Disables the model's chain-of-thought on a self-hosted server (no-op on a
                # hosted one). This is extraction, not reasoning: thinking only spends the
                # token budget, and exhausting it returns an empty body with HTTP 200.
                **settings.llm_extra_body,
            },
            timeout=timeout,
        )
        resp.raise_for_status()
        data = json.loads(resp.json()["choices"][0]["message"]["content"])
    except Exception as exc:  # noqa: BLE001 — a soft read that fails is just "no facts"
        logger.info("LLM personal extraction failed for %s: %s", sender_email, exc)
        return None

    if not isinstance(data, dict):
        return None
    facts = PersonalFacts(
        interests=_clean_list(data.get("interests")),
        shared_activity=_clean_list(data.get("shared_activity")),
        family=_clean_list(data.get("family")),
        key_date=_clean_list(data.get("key_date")),
    )
    return None if facts.is_empty() else facts
