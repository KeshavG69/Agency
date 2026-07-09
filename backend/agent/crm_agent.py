"""The CRM / Relation Agent.

Given ONE opportunity, it SEARCHES the FalkorDB knowledge graph (via a tool) for
candidate contacts, then ranks the few actually worth engaging — each with a
reason and a suggested first-touch.

Agentic retrieval: the agent calls `search_network(query)` to pull only relevant
candidates (keyword + semantic/vector search) instead of dumping the whole network
into the prompt — so it scales to thousands of contacts. Claude/Gemini model,
prompt-JSON + coerce_output (no Agno output_schema).
"""
from __future__ import annotations

import json

from agno.agent import Agent

from agent.company_profile import company_context
from app.settings import settings
from client.graph_store import search_contacts_hybrid
from client.llm_client import get_chat_llm_agno
from models.contact import CRMResult
from utils.agno_tools import create_exa_web_search_tool, create_reasoning_tool
from utils.doc_parse import document_context
from utils.structured import coerce_output


def _instructions(company: str, profile: str) -> str:
    return f"""\
You are a business-development relationship manager for {company}.
Given ONE opportunity, find the contacts in the company's network worth engaging for it.

COMPANY PROFILE (the fit lens):
{profile}

HOW TO WORK:
1. From the opportunity (agency, NAICS, scope, description), derive the capabilities /
   technologies / company-types that a useful contact would have.
2. Call the `search_network` tool with those terms to pull candidate contacts from the
   graph. Search more than once with different angles if the first pass is thin.
3. Rank ONLY the contacts the tool returns. NEVER invent people, emails, or companies.
   If the tool returns nobody relevant, return an empty list — that's a valid answer.

How to judge a returned contact's value:
- COMPANY FIT — does their company plausibly team on / compete for / influence this work?
- ROLE — is their title one that matters (BD, capture, program, contracts, technical lead)?
- RELATIONSHIP STRENGTH — `corr_count` = how often the company has emailed them; a warm,
  frequent contact at a relevant company is ideal. A frequent contact at an irrelevant
  company is still not relevant.

If a contact's company is unfamiliar, or you're not confident what it actually does, use
the `web_search` tool to check before judging COMPANY FIT. NEVER guess or assume what a
company does from its name alone (e.g. assuming a company "must be" a staffing firm, an
IT shop, etc. just because the name sounds like one) — an unverified guess is worse than
searching. If the search turns up nothing useful either, say so in `reason` rather than
inventing a fit.

Use the reasoning tool to think before ranking. Return at most the 10 most valuable.

Your FINAL message must be ONLY this JSON — no prose, no markdown fences, no <reasoning> tags:
{{"recommendations": [{{"name": "<name>", "email": "<email or null>", "company": "<company or null>", "title": "<title or null>", "relevance_score": <integer 0-100>, "reason": "<1-2 lines>", "suggested_outreach": "<one line>"}}]}}
Return {{"recommendations": []}} if no contact is worth engaging.
"""


def build_crm_agent(
    employee_email: str | None = None, organization_id: str | None = None
) -> Agent:
    """Build the CRM / Relation Agent (graph search tool + reasoning).

    `employee_email` (the acting BD rep) + `organization_id` are bound into the search
    tool so it only ever sees that employee's OWN contact network inside their org's
    graph — the LLM never sets them.
    """
    def search_network(query: str) -> str:
        """Search the acting employee's contact network for people relevant to `query`.

        Pass capability / technology / company-type terms (comma or space separated),
        e.g. "speech-to-text, real-time voice, data infrastructure, agent tooling".
        Combines keyword + semantic (vector) search over the employee's graph. Returns a
        JSON list of contacts, each with: email, name, title, company, corr_count (how
        many times the employee has emailed them = relationship strength). Returns [] if
        nothing matches. Call it more than once with different angles if helpful.
        """
        rows = search_contacts_hybrid(query, employee_email or "", organization_id or "")
        return json.dumps(rows)

    company, profile = company_context(organization_id or "")
    return Agent(
        name="CRM",
        model=get_chat_llm_agno(model=settings.ANALYST_MODEL),
        tools=[search_network, create_exa_web_search_tool(), create_reasoning_tool()],
        instructions=_instructions(company, profile),
        debug_mode=True,
    )


def recommend_contacts(
    opp: dict, proposal: str | None = None, employee_email: str | None = None
) -> CRMResult:
    """Have the CRM agent search the acting employee's graph and rank relevant contacts.

    The org graph is taken from the opportunity's `organization_id`.
    """
    organization_id = str(opp.get("organization_id") or "")
    agent = build_crm_agent(employee_email, organization_id)
    opp_lines = [
        f"- {k}: {v}"
        for k, v in opp.items()
        if v not in (None, "", {}) and k in {
            "title", "agency", "naics", "psc_code", "set_aside", "opp_type",
            "place_of_performance", "description",
        }
    ]
    message = "OPPORTUNITY:\n" + "\n".join(opp_lines)
    if proposal:
        message += f"\n\nPROPOSAL CONTEXT:\n{proposal}"
    message += document_context(opp, max_chars=30000)
    message += "\n\nSearch the network and return the relevant contacts as JSON."
    result = agent.run(message)
    return coerce_output(result.content, CRMResult)
