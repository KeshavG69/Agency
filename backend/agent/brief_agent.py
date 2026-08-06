"""The Call-Brief Agent.

Preps ONE person on a pursuit — but grounded in their WHOLE ORGANISATION. It is given every
email the rep's mailbox holds with anyone at that person's domain (so prepping Jane @
cbp.dhs.gov uses the full CBP correspondence, not just Jane's thread), plus the pursuit and
the solicitation, and writes: where we stand with them, what their org has in flight, and
HOW TO TALK TO THEM.

One agent run per contact — the Call Plan dialog has a tab per person, and each tab's brief
is a separate run, dispatched only when the rep opens that tab.

No tools by design: the retrieval is deterministic (search the mailbox by domain, in Python),
so the model does the one thing it is good at — reading a pile of correspondence and turning
it into call guidance. Prompt-JSON + coerce_output, mirroring crm_agent.
"""
from __future__ import annotations

from agno.agent import Agent

from agent.company_profile import company_context
from app.settings import settings
from client.llm_client import get_chat_llm_agno
from models.call_brief import CallBrief
from utils.doc_parse import document_context
from utils.structured import coerce_output


def _instructions(company: str, profile: str, contact: dict, org_domain: str) -> str:
    who = contact.get("name") or contact.get("email") or "this person"
    role = ", ".join(
        str(contact[k]) for k in ("title", "company") if contact.get(k)
    ) or "role unknown"
    return f"""\
You are prepping a business-development rep at {company} for a CALL with {who} ({role}) at
`{org_domain}`.

The brief is about THIS PERSON, but you are given every email the rep's mailbox holds with
ANYONE at {org_domain} — treat that whole-organisation correspondence as your primary source.
It is what lets you say something useful even when the rep has never emailed {who} directly:
a colleague of theirs may have told us plenty.

{company} PROFILE (so you know who "we" are):
{profile}

HOW TO WORK:
1. Find what the mail says about {who} specifically — have we spoken, who last spoke, when,
   about what. If we have never corresponded with them, SAY SO plainly in `relationship`.
2. Read the wider {org_domain} correspondence for what their organisation has in flight —
   other threads, other people, other business. That is `org_context`.
3. Judge how to approach THIS person given their role and our standing — a contracting
   officer, a program manager and a teaming partner each get a different opening. That is
   `approach`, and it is the line the rep reads right before dialling. Be specific and
   practical, not generic ("open by confirming the set-aside intent, they're the decision
   authority and we've never spoken" — NOT "be professional and build rapport").
4. Tie it to the pursuit (opportunity + solicitation below) and name the one concrete thing
   to get from this call.

Ground EVERY claim in the mail or the pursuit context. If the mail is thin, say so rather
than inventing a relationship. NEVER invent people, dates, commitments or job titles.
At most 4 talking points.

Your FINAL message must be ONLY this JSON — no prose, no markdown fences, no <reasoning> tags:
{{"org_name": "<org>", "summary": "<2-3 sentences>", "relationship": "<our history with this person, or that we have none>", "org_context": "<what their org has in flight, or null>", "approach": "<how to talk to them — 1-2 lines>", "talking_points": ["<point>"], "open_threads": ["<item>"], "suggested_ask": "<one line>"}}
"""


def build_call_brief_agent(organization_id: str, contact: dict, org_domain: str) -> Agent:
    """Build the Call-Brief Agent for one contact, scoped to the org's company profile."""
    company, profile = company_context(organization_id or "")
    return Agent(
        name="CallBrief",
        model=get_chat_llm_agno(model=settings.BRIEF_MODEL),
        instructions=_instructions(company, profile, contact, org_domain),
        debug_mode=True,
    )


def _readable(body: str, is_html: bool | None) -> str:
    """One message's body as plain, readable text.

    Outlook hands back full HTML — `<style>` blocks, @font-face rules, nested tables. Feeding
    that raw spends the whole per-message budget on CSS and gives the model no signal, so:
    strip the markup (html_to_text skips style/script), drop the quoted reply chain (every
    reply otherwise repeats the entire thread back at us — we already have those messages
    separately), and collapse whitespace.
    """
    from utils.signature import html_to_text, strip_quoted

    text = body or ""
    # Outlook sometimes omits contentType; sniff for markup rather than trusting the flag alone.
    if is_html or ("<html" in text[:400].lower() or "<div" in text[:400].lower()):
        text = html_to_text(text)
    try:
        text = strip_quoted(text) or text
    except Exception:  # noqa: BLE001 — a body we can't split is still worth showing
        pass
    return " ".join(text.split())


def _format_mail(mail: list[dict], contact_email: str, max_chars: int = 60000) -> str:
    """Render the org's correspondence for the prompt: newest first, each trimmed to a
    readable slice, capped so the whole block fits the context.

    Messages involving THIS contact are marked, so the model can tell "our history with them"
    apart from "what their colleagues told us" without us pre-splitting the list.
    """
    if not mail:
        return "(no email with anyone at this organisation in the rep's mailbox)"
    target = (contact_email or "").strip().lower()
    rows = sorted(mail, key=lambda m: m.get("received_at") or "", reverse=True)
    lines: list[str] = []
    used = 0
    for m in rows:
        who = m.get("sender_name") or m.get("sender_email") or "unknown"
        subj = (m.get("subject") or "").strip()
        when = (m.get("received_at") or "")[:10]
        snippet = _readable(m.get("body") or "", m.get("body_is_html"))[:700]
        if not snippet:
            continue  # nothing readable left once the markup and quoting are gone
        involved = target and (
            m.get("sender_email") == target or target in (m.get("recipients") or [])
        )
        tag = " [THIS CONTACT]" if involved else ""
        block = f"- [{when}]{tag} from {who} <{m.get('sender_email')}> — {subj}\n  {snippet}"
        if used + len(block) > max_chars:
            break
        lines.append(block)
        used += len(block)
    return "\n".join(lines)


def run_call_brief(
    opp: dict, contact: dict, org_domain: str, mail: list[dict]
) -> CallBrief:
    """Synthesise one contact's call brief from their org's mail + the pursuit context.

    `contact` is {name, email, title, company}; `org_domain` is their email domain; `mail` is
    every message the rep's mailbox holds with anyone at that domain (see
    composio_utils.fetch_messages_for_domain).
    """
    organization_id = str(opp.get("organization_id") or "")
    agent = build_call_brief_agent(organization_id, contact, org_domain)

    opp_lines = [
        f"- {k}: {v}"
        for k, v in opp.items()
        if v not in (None, "", {}) and k in {
            "title", "agency", "naics", "psc_code", "set_aside", "opp_type",
            "place_of_performance", "response_deadline", "description",
            "bid_decision", "priority_score",
        }
    ]
    contact_lines = [
        f"- {k}: {contact[k]}" for k in ("name", "email", "title", "company") if contact.get(k)
    ]
    message = "THE PERSON YOU ARE PREPPING FOR:\n" + "\n".join(contact_lines)
    message += f"\nTheir organisation: {org_domain}\n\n"
    message += "PURSUIT (the opportunity this call is about):\n" + "\n".join(opp_lines)
    message += document_context(opp)  # solicitation text, when the opp has one
    message += f"\n\nCORRESPONDENCE WITH {org_domain} (the rep's mailbox, newest first):\n"
    message += _format_mail(mail, str(contact.get("email") or ""))
    message += "\n\nWrite the call brief as JSON."

    result = agent.run(message)
    return coerce_output(result.content, CallBrief)
