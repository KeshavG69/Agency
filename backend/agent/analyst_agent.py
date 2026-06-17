"""The Analyst Agent.

Judges a SINGLE government opportunity for Nexagen — bid / no-bid, priority, and
(if worth pursuing) the call-plan entry. One agent per opportunity; the Celery
layer fans these out in parallel and writes the verdicts back to EspoCRM.

Tools: Exa web search (to research agency / incumbent / recent awards for
winnability) + a reasoning tool. The opportunity + Nexagen profile are fed into
the context; the agent returns a structured AnalystVerdict.
"""
from __future__ import annotations

from datetime import datetime
from functools import lru_cache

from agno.agent import Agent
from agno.models.openrouter import OpenRouter

from app.settings import settings
from models.verdict import AnalystVerdict
from utils.agno_tools import create_exa_web_search_tool, create_reasoning_tool
from utils.structured import coerce_output

# Verified Nexagen profile (see research) — the "fit" lens for every judgment.
NEXAGEN_PROFILE = """\
Company: Nexagen Networks, Inc. (UEI UFZCFEVTJG77, CAGE 3EGC9), Wall Township NJ + Aberdeen MD.
Set-aside status: EDWOSB (Economically Disadvantaged Woman-Owned Small Business) —
  qualifies for EDWOSB, WOSB, and small-business set-asides. NOT 8(a), SDVOSB, or HUBZone
  (those would require a teaming partner).
Size: ~101-200 employees, ~$30.8M revenue. Certs: CMMI L3, ISO 9001 / 20000 / 27001.
NAICS codes: 541330 (Engineering Services), 541511 (Custom Computer Programming),
  541512 (Computer Systems Design), 541513 (Computer Facilities Management),
  541611 (Admin/General Mgmt Consulting), 541614 (Process/Logistics Consulting),
  611420 (Computer Training), 611430 (Professional & Mgmt Dev Training).
Capabilities: systems & software engineering, SDLC, cybersecurity / RMF / COMSEC,
  SATCOM & RF engineering, DevSecOps / cloud, network engineering, logistics, program management.
Customers: U.S. Army (CECOM, PM Tactical Network / WIN-T), Air Force, Navy, Marines, DHS.
"""

_INSTRUCTIONS = f"""\
You are a government-contracting (govcon) business-development analyst for Nexagen Networks.
You receive ONE opportunity. Decide whether Nexagen should pursue it.

NEXAGEN PROFILE (your "fit" lens):
{NEXAGEN_PROFILE}

How to judge:
1. FIT — Does the NAICS match Nexagen's codes? Is the set-aside one they qualify for
   (EDWOSB / WOSB / small business)? Does the work match their capabilities?
   An 8(a)/SDVOSB/HUBZone-ONLY set-aside is a hard blocker unless teaming — lean No-Bid.
   A NAICS / scope far outside their list -> No-Bid.
2. WINNABILITY — Use the web search tool to research the agency, the likely incumbent,
   and recent similar awards. Note: you have NO internal past-performance data, so if you
   can't establish winnability, say so plainly and stay cautious — do NOT invent it.
3. URGENCY — Compare the response deadline to today's date (given in the input).
4. VALUE — Weigh the estimated contract value.

Use the reasoning tool to think before deciding.

Output rules:
- bid_decision: "Bid" (good fit + worth pursuing), "No-Bid" (can't bid or poor fit),
  or "Watch" (early-stage like Sources Sought, or promising but uncertain).
- priority_score: 0-100, weighing fit + winnability + urgency + value.
- recommended_stage: Bid -> "Qualify"; Watch -> "Discover"; No-Bid -> "No-Bid".
- call_action: ONLY when bid_decision == "Bid". contact = the opportunity POC (or the
  agency contracting office); talking_point = one line on why to reach out and what to say.
  Leave call_action null for No-Bid and Watch.
- rationale: 1-2 lines. Be honest about low-confidence judgments.

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


@lru_cache(maxsize=1)
def _get_model() -> OpenRouter:
    return OpenRouter(id=settings.ANALYST_MODEL, api_key=settings.OPENROUTER_API_KEY)


def build_analyst_agent() -> Agent:
    """Build a fresh Analyst Agent (cached model, two tools, structured output)."""
    return Agent(
        name="Analyst",
        model=_get_model(),
        tools=[create_exa_web_search_tool(), create_reasoning_tool()],
        instructions=_INSTRUCTIONS,
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
    return "\n".join(lines)


def analyze_opportunity(opp: dict, today: str | None = None) -> AnalystVerdict:
    """Run the Analyst Agent on one opportunity and return its structured verdict."""
    today = today or datetime.now().strftime("%Y-%m-%d")
    agent = build_analyst_agent()
    result = agent.run(_format_opportunity(opp, today))
    return coerce_output(result.content, AnalystVerdict)
