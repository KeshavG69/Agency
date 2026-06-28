"""The Analyst Agent.

Judges a SINGLE government opportunity for the company — bid / no-bid, priority, and
(if worth pursuing) the call-plan entry. One agent per opportunity; the Celery
layer fans these out in parallel and writes the verdicts back to the CRM store.

Tools: Exa web search (to research agency / incumbent / recent awards for
winnability) + a reasoning tool. The opportunity + the company profile (built per-org
from its UEI) are fed into the context; the agent returns a structured AnalystVerdict.
"""
from __future__ import annotations

import logging
from datetime import datetime

from agno.agent import Agent

from agent.company_profile import company_context
from app.settings import settings
from client.llm_client import get_chat_llm_agno
from models.verdict import AnalystVerdict
from utils.agno_tools import create_exa_web_search_tool, create_reasoning_tool
from utils.doc_parse import document_context
from utils.structured import coerce_output

logger = logging.getLogger(__name__)


def _instructions(company: str, profile: str) -> str:
    return f"""\
You are a government-contracting (govcon) business-development analyst for {company}.
You receive ONE opportunity. Decide whether {company} should pursue it.

COMPANY PROFILE (your "fit" lens):
{profile}

How to judge:
1. FIT — Does the NAICS match the company's codes? Is the set-aside one the company is
   eligible for (per its socioeconomic / set-aside eligibility above)? Does the work match
   what its NAICS imply? A set-aside the company is NOT eligible for is a hard blocker
   unless teaming — lean No-Bid. A NAICS / scope far outside its list -> No-Bid.
2. WINNABILITY — Use the web search tool to research the agency, the likely incumbent,
   and recent similar awards. Note: you have NO internal past-performance data, so if you
   can't establish winnability, say so plainly and stay cautious — do NOT invent it.
3. URGENCY — Compare the response deadline to today's date (given in the input).
4. VALUE — Weigh the estimated contract value.

Use the reasoning tool to think before deciding.

GROUNDING & CITATIONS — NON-NEGOTIABLE:
- Every factual claim you make MUST be backed by EITHER (a) a field in the provided
  opportunity data, OR (b) a web-search result you actually retrieved. Nothing else.
- NEVER fabricate, guess, or "fill in" specifics. Do NOT invent: dates or years,
  contract numbers, dollar amounts, prior pursuits, incumbents, award histories, or
  ANY past performance. If you did not read it in the opportunity data or in a web
  result, you do not know it — say so.
- When you reference an external fact (about the agency, the incumbent, recent awards,
  or {company}'s own past performance), cite the source URL inline in the rationale.
- The COMPANY PROFILE above is the ONLY company background you may assert without a
  citation. Anything about {company} NOT in that profile (e.g. a specific past contract
  or year) must come from a web search WITH a cited URL, or must not be stated at all.
- If you cannot verify something, write "unverified" rather than asserting it. An honest
  "I could not confirm winnability" is REQUIRED over an invented justification.

Output rules:
- bid_decision: "Bid" (good fit + worth pursuing), "No-Bid" (can't bid or poor fit),
  or "Watch" (early-stage like Sources Sought, or promising but uncertain).
- priority_score: 0-100, weighing fit + winnability + urgency + value.
- recommended_stage: Bid -> "Qualify"; Watch -> "Discover"; No-Bid -> "No-Bid".
- call_action: ONLY when bid_decision == "Bid". contact = the opportunity POC (or the
  agency contracting office); talking_point = one line on why to reach out and what to say.
  Leave call_action null for No-Bid and Watch.
- rationale: 1-2 lines. Cite a source URL for any external fact you rely on. Be honest
  about low-confidence judgments and mark anything you could not verify as "unverified".

Ground everything in the provided opportunity data and your web research. Never fabricate.

After researching and reasoning, your FINAL message must be ONLY this JSON object —
no prose, no markdown fences, no <reasoning> tags:
{{"bid_decision": "Bid" | "No-Bid" | "Watch", "priority_score": <integer 0-100>,
  "rationale": "<1-2 lines>", "recommended_stage": "Qualify" | "Discover" | "No-Bid",
  "call_action": {{"contact": "<who>", "channel": "email", "talking_point": "<one line>"}}}}
Use null for "call_action" unless bid_decision is "Bid".
"""

_OPP_FIELDS = [
    "title", "solicitation_number", "notice_id", "agency", "naics", "psc_code",
    "set_aside", "opp_type", "posted_date", "response_deadline", "estimated_value",
    "place_of_performance", "poc_name", "poc_email", "description", "link",
]


def build_analyst_agent(organization_id: str | None = None) -> Agent:
    """Build a fresh Analyst Agent grounded in the org's own company profile (from its UEI)."""
    company, profile = company_context(organization_id or "")
    return Agent(
        name="Analyst",
        model=get_chat_llm_agno(model=settings.ANALYST_MODEL),
        tools=[create_exa_web_search_tool(), create_reasoning_tool()],
        instructions=_instructions(company, profile),
        debug_mode=True
    )


def _format_opportunity(opp: dict, today: str) -> str:
    lines = [f"Today's date: {today}", "", "OPPORTUNITY:"]
    for key in _OPP_FIELDS:
        val = opp.get(key)
        if val not in (None, ""):
            lines.append(f"- {key}: {val}")
    extra = opp.get("extra") or {}
    if extra:
        lines.append(f"- additional notes: {extra}")
    return "\n".join(lines) + document_context(opp)


def analyze_opportunity(
    opp: dict, today: str | None = None, max_attempts: int = 3
) -> AnalystVerdict:
    """Run the Analyst Agent on one opportunity and return its structured verdict.

    Reasoning models (Gemini) occasionally return empty/unparseable content — a
    flaky one-off. Retry the run a few times before giving up; a fresh run almost
    always produces clean JSON.
    """
    today = today or datetime.now().strftime("%Y-%m-%d")
    organization_id = str(opp.get("organization_id") or "")
    message = _format_opportunity(opp, today)
    last_err: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        result = build_analyst_agent(organization_id).run(message)
        try:
            return coerce_output(result.content, AnalystVerdict)
        except Exception as e:  # empty content / invalid JSON -> retry the run
            last_err = e
            logger.warning(
                "Analyst output unparseable (attempt %d/%d) for %s: %s",
                attempt, max_attempts, opp.get("id"), e,
            )
    raise last_err  # type: ignore[misc]
