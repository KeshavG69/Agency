"""The Mail / Outreach Agent.

Drafts the pre-pricing outreach email the company sends a contact about an opportunity
— relationship-aware, usually ONE short paragraph that
(a) summarizes the company's relevant EXPERIENCE and (b) names the SPECIFIC requirement
the client needs, tuned to the solicitation type (BAA = long-term R&D, CSO =
immediate Commercial Solutions). DRAFT-ONLY — a human always approves before sending.

Context it works from:
  - the company profile (who we are, built per-org from the UEI)
  - the opportunity / proposal (the specific requirement)
  - the SharePoint document structure (where our real experience lives)

Tools:
  - search_sharepoint  -> our graph: FIND relevant docs/lists (structure + semantic)
  - Composio SharePoint tools -> READ the actual file/list content on demand
  - reasoning
"""
from __future__ import annotations

import asyncio
import logging

from agno.agent import Agent

from agent.company_profile import company_context
from client.graph_store import get_contact_relationship
from client.llm_client import get_chat_llm_agno
from utils.doc_parse import document_context
from client.sharepoint_graph import search_sharepoint
from models.mail import MailDraft, ReplyDraft
from utils.agno_tools import create_reasoning_tool
from utils.composio_utils import sharepoint_entity
from utils.sharepoint_tools import load_sharepoint_tools, sharepoint_tool_instructions
from utils.structured import coerce_output

logger = logging.getLogger(__name__)

def _instructions(company: str, profile: str) -> str:
    return f"""\
You are the business-development outreach writer for {company}. You draft the
short email we send a contact BEFORE the pricing proposal — to open or warm the deal.

COMPANY PROFILE (who we are):
{profile}

HOW TO WRITE IT (the house style):
1. RELATIONSHIP-AWARE — tune tone/length to the RELATIONSHIP line in the recipient block
   (how many times we've corresponded + when we last spoke). A warm, frequent contact gets a
   short familiar note that picks up where we left off; a developing contact gets a light
   reminder of who we are; a brand-new/no-history contact gets a proper but brief introduction.
   Never imply a closer relationship than the data shows.
2. USUALLY ONE SHORT PARAGRAPH (at most two). It must contain:
   - a crisp summary of the company's RELEVANT EXPERIENCE for this work, AND
   - the SPECIFIC REQUIREMENT this client/opportunity needs.
3. TUNE TO THE SOLICITATION TYPE: if it's a BAA (Broad Agency Announcement) lead with the
   long-term R&D angle; if it's a CSO (Commercial Solutions Opening) lead with fast,
   immediate commercial delivery. Otherwise keep it neutral.
4. GROUND IT — never invent experience. Use the `search_sharepoint` tool to find the company's
   real past-performance / capability material, and the SharePoint read tools to confirm or
   read a specific document if needed. Only claim experience you find in SharePoint or that
   is in the COMPANY PROFILE above. If you can't substantiate a claim, leave it out.
5. DRAFT ONLY. Do NOT send anything. Produce the draft for a human to review and send.
6. SENDER — you do NOT know who is sending this. NEVER invent a sender name, title, or phone
   (no made-up signatory). Do not open with a personal name.
   Sign off with bracketed PLACEHOLDERS the user fills in before sending, exactly:
       Best regards,
       [Your Name]
       [Your Title], {company}
   The company name is real and may be used; everything personal stays a [placeholder].

{sharepoint_tool_instructions()}

Also available: `search_sharepoint(query)` — searches the company's SharePoint structure graph
(keyword + semantic) and follows the graph to return relevant docs/folders/lists with context.
Prefer it FIRST to locate relevant material, then use the Composio read tools only to read a
specific file/list you decided you need.

Your FINAL message must be ONLY this JSON object — no prose, no markdown fences, no <reasoning> tags:
{{"to": "<recipient email or null>", "to_name": "<recipient display name or null>", "cc": [], "subject": "<subject line>", "body": "<the email body>", "is_html": false, "grounded_on": ["<each real experience/doc the email draws on>"]}}

Notes on the fields:
- "body" is the email content as ONE string. Write plain text (greeting, the paragraph, sign-off)
  and set "is_html" to false. Do not include the subject inside the body.
- "to"/"to_name" come from the RECIPIENT block. "cc" is usually [] unless clearly warranted.
- "grounded_on" is for our records (shown in our UI, not emailed) — list the real company
  experience/documents each claim rests on.
"""


def build_mail_agent(
    user_id: str | None = None, employee_email: str | None = None,
    organization_id: str | None = None,
) -> Agent:
    """Build the Mail Agent (graph search + Composio SharePoint read tools + reasoning).

    `employee_email` (the acting BD rep) + `organization_id` are bound into the
    SharePoint search so it queries the right org's graph and RBAC-prefilters to
    documents that employee may read — the LLM never sets them.
    """
    def search_sharepoint_tool(query: str) -> str:
        """Search the company's SharePoint documents/lists for material relevant to `query`
        (topic / capability / document-type, e.g. "past performance cybersecurity").
        Returns a JSON list of relevant items with paths + links — only documents the
        acting employee is permitted to read. Returns [] if nothing relevant/accessible."""
        return search_sharepoint(
            query, employee_email=employee_email, organization_id=organization_id or ""
        )

    company, profile = company_context(organization_id or "")
    return Agent(
        name="Mail",
        model=get_chat_llm_agno(max_tokens=12000),
        tools=[
            search_sharepoint_tool,
            create_reasoning_tool(),
            # SharePoint READ tools run under THIS ORG's SharePoint connection.
            *load_sharepoint_tools(sharepoint_entity(organization_id or "")),
        ],
        instructions=_instructions(company, profile),
        debug_mode=True,
    )


def _relationship_signal(
    email: str | None, employee_email: str | None, organization_id: str | None
) -> str:
    """A plain-language relationship descriptor (frequency + recency) from the graph.

    This is the data behind 'relationship-aware' — the Mail Agent tunes tone to it.
    Scoped to the ACTING employee's own correspondence history (within their org graph).
    """
    rel = (
        get_contact_relationship(email, employee_email or "", organization_id or "")
        if email else None
    )
    if not rel:
        return "no prior correspondence on record — treat as a brand-new contact (introduce who we are)"
    n = rel["corr_count"]
    last = rel.get("last_contact")
    last = str(last).split("T")[0] if last else None  # ISO timestamp -> just the date
    seen = f", last contact {last}" if last else ""
    if n >= 10:
        tier = "a warm, frequent contact — write a short familiar note"
    elif n >= 3:
        tier = "a developing contact — a light reminder of who we are helps"
    elif n >= 1:
        tier = "only lightly in touch — keep it brief and re-introduce"
    else:
        tier = "no real history — treat as a brand-new contact"
    return f"we've corresponded {n}× with this person{seen}; {tier}"


def _build_message(opp: dict, contact: dict, proposal: str | None, employee_email: str | None) -> str:
    """The per-contact prompt: opportunity + recipient (with relationship signal) + proposal."""
    opp_lines = [
        f"- {k}: {v}" for k, v in opp.items()
        if v not in (None, "", {}) and k in {
            "title", "agency", "naics", "set_aside", "opp_type", "description",
            "place_of_performance",
        }
    ]
    c_lines = [
        f"- {k}: {v}" for k, v in contact.items()
        if v not in (None, "", {}) and k in {
            "name", "email", "company", "title", "relevance_score", "reason",
        }
    ]
    org_id = str(opp.get("organization_id") or "")
    c_lines.append(
        f"- relationship: {_relationship_signal(contact.get('email'), employee_email, org_id)}"
    )
    message = (
        "OPPORTUNITY:\n" + "\n".join(opp_lines) + "\n\n"
        "RECIPIENT (the contact to email):\n" + "\n".join(c_lines)
    )
    if proposal:
        message += f"\n\nPROPOSAL CONTEXT:\n{proposal}"
    message += document_context(opp, max_chars=30000)
    message += "\n\nFind our relevant experience in SharePoint, then draft the outreach email as JSON."
    return message


async def adraft_outreach(
    opp: dict, contact: dict, proposal: str | None = None, user_id: str | None = None,
    employee_email: str | None = None,
) -> MailDraft:
    """Draft one outreach email (async). Each call gets its OWN agent — agents hold
    per-run state, so a fresh one per contact keeps concurrent drafts isolated.
    `employee_email` (the acting rep, from the payload) RBAC-scopes the SharePoint search."""
    agent = build_mail_agent(
        user_id, employee_email=employee_email,
        organization_id=str(opp.get("organization_id") or ""),
    )
    result = await agent.arun(_build_message(opp, contact, proposal, employee_email))
    draft = coerce_output(result.content, MailDraft)
    # Guarantee the draft is addressed even if the model omitted it.
    if not draft.to and contact.get("email"):
        draft.to = contact["email"]
    if not draft.to_name and contact.get("name"):
        draft.to_name = contact["name"]
    return draft


def draft_outreach(
    opp: dict, contact: dict, proposal: str | None = None, user_id: str | None = None,
    employee_email: str | None = None,
) -> MailDraft:
    """Draft the pre-pricing outreach email for one contact + opportunity (draft-only)."""
    return asyncio.run(adraft_outreach(opp, contact, proposal, user_id, employee_email))


async def adraft_outreach_batch(
    opp: dict,
    contacts: list[dict],
    proposal: str | None = None,
    user_id: str | None = None,
    limit: int = 15,
    employee_email: str | None = None,
) -> list[MailDraft | None]:
    """Draft outreach for MANY contacts in parallel — one agent per email address,
    capped at `limit` concurrent by a semaphore (e.g. 10 contacts all run at once;
    30 contacts run 15 at a time). Result is aligned to `contacts`; a contact whose
    draft errors becomes None so one failure never sinks the batch."""
    sem = asyncio.Semaphore(max(1, limit))

    async def one(contact: dict) -> MailDraft | None:
        async with sem:
            try:
                return await adraft_outreach(opp, contact, proposal, user_id, employee_email)
            except Exception:  # noqa: BLE001 — keep the rest of the batch alive
                logger.exception("draft_outreach failed for %s", contact.get("email"))
                return None

    return await asyncio.gather(*(one(c) for c in contacts))


def draft_outreach_batch(
    opp: dict,
    contacts: list[dict],
    proposal: str | None = None,
    user_id: str | None = None,
    limit: int = 15,
    employee_email: str | None = None,
) -> list[MailDraft | None]:
    """Sync entry point for the parallel batch (call from a Celery task / sync code)."""
    return asyncio.run(
        adraft_outreach_batch(opp, contacts, proposal, user_id, limit, employee_email)
    )


# --- reply drafting (mail triage) -----------------------------------------------------
# A relevant incoming mail (from a known contact on an active Bid) gets a SUGGESTED
# reply. Unlike outreach, this fills in one existing thread, not a fresh introduction —
# no subject/recipient needed (Graph fills those in for a threaded reply). Draft-only:
# the text is only ever turned into a real Outlook draft on an explicit human click, and
# even then it's a DRAFT sitting in their mailbox, never sent by us.


def _reply_instructions(company: str, profile: str) -> str:
    return f"""\
You are the business-development rep for {company}, replying to an inbound email from a
contact already engaged on one of our active pursuits.

COMPANY PROFILE (who we are):
{profile}

HOW TO WRITE IT (the house style):
1. RELATIONSHIP-AWARE — tune tone to the RELATIONSHIP line (how often we've corresponded).
2. ANSWER WHAT THEY ACTUALLY ASKED. Read the incoming mail's subject + snippet closely; a
   reply that ignores their question reads as careless. If the snippet doesn't fully show
   what they need, write a helpful, appropriately brief reply that keeps the conversation
   moving rather than guessing at unstated specifics.
3. SHORT — one or two short paragraphs. This is a reply, not a new pitch.
4. GROUND ANY CAPABILITY CLAIM in real experience — use `search_sharepoint` to find our
   actual past-performance / capability material if the reply needs to substantiate
   something. Never invent experience; if you can't substantiate a claim, leave it out.
5. DRAFT ONLY. Do NOT send anything. Produce the reply text for a human to review.
6. SENDER — you do NOT know who is sending this. Never invent a sender name, title, or
   phone. Sign off with bracketed PLACEHOLDERS the user fills in before sending, exactly:
       Best regards,
       [Your Name]
       [Your Title], {company}

{sharepoint_tool_instructions()}

Also available: `search_sharepoint(query)` — searches the company's SharePoint structure
graph (keyword + semantic) and follows the graph to relevant docs/folders/lists.

Your FINAL message must be ONLY this JSON object — no prose, no markdown fences, no
<reasoning> tags:
{{"comment": "<the reply body>", "grounded_on": ["<each real experience/doc the reply draws on>"]}}
"""


def build_reply_agent(
    user_id: str | None = None, employee_email: str | None = None,
    organization_id: str | None = None,
) -> Agent:
    """Build the reply-drafting agent — same grounding/tools as outreach, reply-tuned
    instructions and a smaller (comment-only) output shape."""

    def search_sharepoint_tool(query: str) -> str:
        """Search the company's SharePoint documents/lists for material relevant to `query`.
        Returns a JSON list of relevant items with paths + links — only documents the
        acting employee is permitted to read. Returns [] if nothing relevant/accessible."""
        return search_sharepoint(
            query, employee_email=employee_email, organization_id=organization_id or ""
        )

    company, profile = company_context(organization_id or "")
    return Agent(
        name="MailReply",
        model=get_chat_llm_agno(max_tokens=8000),
        tools=[
            search_sharepoint_tool,
            create_reasoning_tool(),
            *load_sharepoint_tools(sharepoint_entity(organization_id or "")),
        ],
        instructions=_reply_instructions(company, profile),
        debug_mode=True,
    )


def _build_reply_message(
    opp: dict, incoming: dict, employee_email: str | None,
) -> str:
    """The reply prompt: opportunity context + the incoming mail + relationship signal."""
    opp_lines = [
        f"- {k}: {v}" for k, v in opp.items()
        if v not in (None, "", {}) and k in {"title", "agency", "naics", "set_aside", "opp_type"}
    ]
    org_id = str(opp.get("organization_id") or "")
    sender_email = incoming.get("sender_email")
    relationship = _relationship_signal(sender_email, employee_email, org_id)
    message = (
        "OPPORTUNITY (context for this thread):\n" + "\n".join(opp_lines) + "\n\n"
        "INCOMING EMAIL (the one you're replying to):\n"
        f"- from: {incoming.get('sender_name') or sender_email} <{sender_email}>\n"
        f"- subject: {incoming.get('subject') or ''}\n"
        f"- snippet: {incoming.get('snippet') or ''}\n"
        f"- relationship: {relationship}\n\n"
        "Write the reply body as JSON."
    )
    return message


async def adraft_reply(
    opp: dict, incoming: dict, user_id: str | None = None, employee_email: str | None = None,
) -> ReplyDraft:
    """Draft one suggested reply (async) to an incoming mail-triage message."""
    agent = build_reply_agent(
        user_id, employee_email=employee_email,
        organization_id=str(opp.get("organization_id") or ""),
    )
    result = await agent.arun(_build_reply_message(opp, incoming, employee_email))
    return coerce_output(result.content, ReplyDraft)


def draft_reply(
    opp: dict, incoming: dict, user_id: str | None = None, employee_email: str | None = None,
) -> ReplyDraft:
    """Sync entry point (call from a Celery task)."""
    return asyncio.run(adraft_reply(opp, incoming, user_id, employee_email))
