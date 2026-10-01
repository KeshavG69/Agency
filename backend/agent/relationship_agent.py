"""The Relationship Agent — drafts the short outreach that keeps a relationship warm.

This is the drafting half of the relationship engine. It is NOT the opportunity flow's
Relation agent (crm_agent.py), which finds contacts FOR a solicitation. This one is handed a
single contact the cadence pass or a personal hook has already selected, and writes the note
a rep will approve with one tap:

  * a going-cold TOUCH — a light "it's been a while" re-engagement, when a warm contact has
    gone quiet; no specific hook required.
  * a PERSONAL nudge — leads with a real, stored personal fact ("up for golf this weekend?").

TWO HARD RULES, both leaned on in the prompt and BACKED BY CODE downstream (the proof gate in
utils/nudge_proof.py, which drops any draft that breaks them — the model is not trusted to
police itself):

  1. NEVER INVENT A PERSONAL FACT. The agent may use ONLY the facts in the PERSONAL FACTS
     block. No hobby, family detail, or date that isn't there. Everything it uses goes into
     `drew_on` verbatim, so the gate can confirm each against what we actually hold.
  2. DRAFT ONLY. It never sends. It returns JSON for a human to approve.

Per-run agent, prompt-JSON + coerce_output — the same shape as the Mail agent.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

from agno.agent import Agent

from agent.company_profile import company_context
from app.settings import settings
from client.llm_client import get_chat_llm_agno
from models.nudge import RelationshipDraft
from utils.structured import coerce_output
from utils.dates import date_context

logger = logging.getLogger(__name__)


def _instructions(company: str, profile: str, sender_name: str) -> str:
    # The sign-off: use the rep's REAL name when we have it. This engine, unlike the Mail
    # agent, always knows the sender (the mailbox owner), so leaving a "[Your Name]"
    # placeholder in a message the rep sends with one tap would mail a template to a
    # customer. Only fall back to the placeholder when the name is genuinely unknown.
    if sender_name:
        signoff = f"""Sign off EXACTLY like this, with the real name — no placeholders:
       Best,
       {sender_name}"""
    else:
        signoff = """Sign off with a bracketed placeholder the rep fills in, exactly:
       Best,
       [Your Name]"""
    return f"""\
You write short, warm relationship messages for a business-development rep at {company}.
These are NOT sales pitches — they keep a professional relationship alive between deals.

COMPANY (who we are, for light context only — do NOT pitch it):
{company}

HOW TO WRITE IT:
1. KEEP IT SHORT AND HUMAN — one short paragraph, the kind of note a busy person actually
   sends. Warm, honest, no marketing voice, no hard sell.
2. MATCH THE NUDGE TYPE (given in the input):
   - "relationship_touch": a light re-engagement — acknowledge it's been a while and open the
     door ("how are things at <their company>?"). No invented reason for reaching out.
   - "relationship_personal": lead with the PERSONAL HOOK from the facts below ("up for golf
     this weekend?"). The hook is the reason for the message.
3. USE ONLY THE PERSONAL FACTS PROVIDED. Never invent a hobby, family detail, trip, or date.
   If the facts are thin, write a lighter, more general note rather than making something up.
   List EVERY personal fact you actually used, verbatim, in "drew_on".
4. DRAFT ONLY — never send. Do not fabricate news about our company or the contact.
5. NEVER leave a fill-in-the-blank in the message. No "[Your Name]", "[Company]", "[date]"
   or any other bracketed placeholder anywhere in the subject or body — the rep sends this
   with one tap, so anything unfilled goes to the customer as-is.
6. SIGN-OFF. {signoff}

Your FINAL message must be ONLY this JSON object — no prose, no markdown fences:
{{"subject": "<short subject>", "body": "<the message body, plain text>", "drew_on": ["<each personal fact you used, verbatim>"]}}
"""


def build_relationship_agent(
    organization_id: str | None = None, sender_name: str = ""
) -> Agent:
    """A fresh agent per draft — agents hold per-run state, so isolation matters for the
    concurrent nightly sweep. `sender_name` is the mailbox owner, signed into the message."""
    company, profile = company_context(organization_id or "")
    return Agent(
        name="Relationship",
        model=get_chat_llm_agno(model=settings.MAIL_MODEL, max_tokens=4000),
        instructions=_instructions(company, profile, sender_name),
        debug_mode=False,
    )


def _facts_block(personal_facts: dict[str, list[str]]) -> str:
    """Render the stored personal facts the draft is allowed to use. Empty -> a clear
    'none on record' so the model writes a general note rather than inventing a hook."""
    if not personal_facts:
        return "PERSONAL FACTS ON RECORD: none — write a light, general note; invent nothing."
    lines = []
    for field, values in personal_facts.items():
        for v in values:
            lines.append(f"- {field}: {v}")
    return "PERSONAL FACTS ON RECORD (use only these; list any you use in drew_on):\n" + "\n".join(lines)


def _build_message(
    kind: str, contact: dict, relationship_line: str, personal_facts: dict[str, list[str]]
) -> str:
    c_lines = [
        f"- {k}: {v}" for k, v in contact.items()
        if v not in (None, "", {}) and k in {"name", "email", "company", "title"}
    ]
    return (
        date_context() + "\n\n"
        f"NUDGE TYPE: {kind}\n\n"
        "CONTACT:\n" + "\n".join(c_lines) + "\n"
        f"- relationship: {relationship_line}\n\n"
        + _facts_block(personal_facts)
        + "\n\nWrite the message as JSON."
    )


async def adraft_relationship_note(
    kind: str,
    contact: dict,
    relationship_line: str,
    personal_facts: dict[str, list[str]],
    organization_id: str | None = None,
    sender_name: str = "",
) -> RelationshipDraft:
    """Draft one relationship note (async). The proof gate (utils/nudge_proof) verifies the
    result — including that no bracketed placeholder survived — before it is ever persisted."""
    agent = build_relationship_agent(organization_id, sender_name=sender_name)
    result = await agent.arun(
        _build_message(kind, contact, relationship_line, personal_facts)
    )
    return coerce_output(result.content, RelationshipDraft)


def draft_relationship_note(
    kind: str,
    contact: dict,
    relationship_line: str,
    personal_facts: Optional[dict[str, list[str]]] = None,
    organization_id: str | None = None,
    sender_name: str = "",
) -> RelationshipDraft:
    """Sync wrapper."""
    return asyncio.run(
        adraft_relationship_note(
            kind, contact, relationship_line, personal_facts or {}, organization_id,
            sender_name=sender_name,
        )
    )
