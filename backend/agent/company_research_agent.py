"""The Company Research Agent — the ONLY LLM step in the enrichment pipeline.

Everything else that enriches a contact is ordinary code: filtering out no-reply
addresses is string matching, resolving a company from its domain is a Mongo lookup,
reading a signature block is text parsing, scoring evidence is arithmetic. A model is
called for exactly one question, and only when the free dataset could not answer it:

    "nexagen-solutions.com — whose company is this, and what do they actually do?"

That answer is what the Relation agent needs to judge teaming relevance, and it is the
one thing no local dataset can supply.

GROUNDING IS THE WHOLE JOB. A wrong industry quietly mis-ranks a company for every bid
it is ever considered for, and nobody can tell it is wrong. So the agent must cite a URL
for what it reports, and a miss must stay a miss: `found=false` is a correct, useful
answer, and far better than a plausible guess. The result is recorded as `web.cited-claim`
evidence (0.40, supporting) — deliberately NOT strong enough to be asserted on a record
on its own; it corroborates rather than decides.

See docs/enrichment-implementation-plan.md §5.8.
"""
from __future__ import annotations

import logging
from typing import Optional

from agno.agent import Agent
from pydantic import BaseModel, Field

from agent.skills_registry import get_bd_skills
from app.settings import settings
from client.llm_client import get_chat_llm_agno
from utils.agno_tools import create_exa_web_search_tool
from utils.structured import coerce_output

logger = logging.getLogger(__name__)


class CompanyResearch(BaseModel):
    """What a company is, as read off the public web."""

    found: bool = Field(description="False if you could not establish what this company is")
    name: Optional[str] = Field(None, description="The company's own name for itself")
    industry: Optional[str] = Field(
        None, description="Short sector label, e.g. 'Defense IT services', 'Aerospace manufacturing'"
    )
    description: Optional[str] = Field(
        None, description="One or two sentences: what they actually do"
    )
    website: Optional[str] = None
    linkedin: Optional[str] = None
    source_url: Optional[str] = Field(
        None, description="The page you read this from. REQUIRED when found is true."
    )


def _instructions(domain: str, budget: int) -> str:
    return f"""\
You research ONE company for a government-contracting business-development team, so that
they can judge whether it is worth teaming with, competing against, or selling to.

THE COMPANY: the organisation that owns the email domain `{domain}`.

Two skills apply to this job and are worth loading before you start:
`company-research` (where to look, what actually matters, and the traps) and `grounding`
(what you may assert at all). Load them with get_skill_instructions.

HOW TO WORK
1. Search for the domain and the company name behind it. Prefer the company's own website
   ("about", "capabilities", "services"), then SAM.gov, then LinkedIn, then directories.
2. Establish what they DO — the capability, not the marketing. "Defense IT services and
   cyber engineering for DoD" is useful. "Innovative solutions provider" is not.
3. Use at most {budget} searches. Stop as soon as you can answer; stop anyway at {budget}.

GROUNDING — the rules that matter more than the answer
- Report ONLY what you actually read on a page. Never infer what a company does from its
  NAME: a name that sounds like a staffing firm, an IT shop or a defence prime tells you
  nothing, and an unverified guess about a customer is worse than a blank field because
  nobody can tell it is wrong.
- `source_url` must be the page you read it from, and is REQUIRED when found is true.
- If the domain is a parked page, a personal site, a generic mail provider, or you simply
  cannot establish who they are, return found=false. A miss must stay a miss — that is a
  correct answer here, not a failure, and it costs the team nothing.
- Do not invent contract numbers, agency relationships, revenue, size, or past performance.

OUTPUT — your FINAL message must be ONLY this JSON object, no prose, no markdown fences:
{{"found": true | false, "name": "<company name or null>", "industry": "<short sector or null>",
  "description": "<1-2 sentences on what they do, or null>", "website": "<url or null>",
  "linkedin": "<url or null>", "source_url": "<the page you read, or null>"}}
"""


# `tool_call_limit` counts EVERY tool call, and loading a skill is a tool call
# (`get_skill_instructions`). Without headroom, an agent that reads its two relevant
# skills would spend its entire search budget before searching once. Three is enough for
# the skills it is pointed at, plus a spare.
_SKILL_CALL_ALLOWANCE = 3


def build_company_research_agent(domain: str, budget: int = 3) -> Agent:
    return Agent(
        name="CompanyResearch",
        model=get_chat_llm_agno(model=settings.ANALYST_MODEL),
        tools=[create_exa_web_search_tool()],
        skills=get_bd_skills(),
        # The budget is ENFORCED here, not merely requested in the prompt. A model that
        # decides one more search would help cannot take it — the cap is the pocket money,
        # not a note asking it to be careful. Without this, a single stubborn lookup can
        # quietly spend many times what the queue row authorised.
        tool_call_limit=max(1, int(budget)) + _SKILL_CALL_ALLOWANCE,
        instructions=_instructions(domain, budget),
    )


def research_company(domain: str, budget: int = 3, max_attempts: int = 2) -> CompanyResearch:
    """Look up one company by its email domain. Never raises for 'not found' — that is a
    result. Raises only when the model could not be made to produce valid JSON at all."""
    dom = (domain or "").strip().lower()
    if not dom:
        return CompanyResearch(found=False)

    message = (
        f"Research the company behind the email domain `{dom}`. "
        "Return the JSON object described in your instructions and nothing else."
    )
    last: Optional[Exception] = None
    for attempt in range(1, max_attempts + 1):
        result = build_company_research_agent(dom, budget).run(message)
        try:
            out = coerce_output(result.content, CompanyResearch)
        except Exception as exc:  # noqa: BLE001 — reasoning models occasionally emit junk
            last = exc
            logger.warning(
                "Company research output unparseable (attempt %d/%d) for %s: %s",
                attempt, max_attempts, dom, exc,
            )
            continue
        # An uncited claim is the exact shape of a hallucination; downgrade it to a miss
        # rather than storing it, since the evidence layer would reject it anyway.
        if out.found and not (out.source_url or "").strip():
            logger.warning("Company research for %s claimed a result with no source — treating as not found", dom)
            return CompanyResearch(found=False)
        return out
    raise last  # type: ignore[misc]
