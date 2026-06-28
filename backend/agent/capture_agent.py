"""The Capture agent (Phase 2) — merged capture-plan + shaping.

One agent, one pass: it works out the INTERNAL capture strategy (customer pain points,
mission objectives, competitor/incumbent read, win themes, discriminators, teaming) and
produces the EXTERNAL, customer-facing deliverable(s) the opportunity needs — grounded in
that strategy and the company's real past performance. The internal capture-plan document
is optional (only when it adds value). This replaces the separate Capture Plan + Shaping
agents, which shared every tool and ran back-to-back.
"""
from __future__ import annotations

import asyncio
from functools import lru_cache
from pathlib import Path

from agno.agent import Agent
from agno.skills import LocalSkills, Skills

from agent.company_profile import company_context
from client.llm_client import get_chat_llm_agno
from client.sharepoint_graph import search_sharepoint
from models.capture import CaptureOutput
from utils.agno_tools import create_exa_web_search_tool, create_reasoning_tool
from utils.composio_utils import sharepoint_entity
from utils.doc_parse import document_context
from utils.python_repl_tool import python_repl_tool, set_session_id
from utils.s3_upload_tool import s3_upload_tool
from utils.sharepoint_tools import load_sharepoint_tools, sharepoint_tool_instructions
from utils.structured import coerce_output

_SKILLS_DIR = Path(__file__).resolve().parent / "skills"


@lru_cache(maxsize=1)
def get_capture_skills() -> Skills:
    """Load the pdf/docx/pptx skills once per process."""
    return Skills(loaders=[LocalSkills(str(_SKILLS_DIR), validate=True)])


def _instructions(company: str, profile: str) -> str:
    return f"""\
You are a capture strategist AND proposal writer for {company} (a government contractor).
You are given ONE opportunity. Do BOTH jobs in a single pass:
  1) Work out the INTERNAL capture strategy — customer pain points, mission objectives,
     likely incumbent + competitor assessment, win themes, discriminators, teaming strategy.
  2) Produce the EXTERNAL, customer-facing deliverable(s) this opportunity actually needs,
     grounded in that strategy.

COMPANY PROFILE:
{profile}

GROUND IN OUR PAST CAPABILITY — use the `search_sharepoint` tool to pull the company's REAL
past-performance / capability material (capability statements, prior proposals, project
summaries), and the SharePoint read tools to open a specific document. Back every capability
claim with real past performance you find — never invent it.

Steps:
1. Research the customer/agency, likely incumbent, and competitors with the web search tool,
   AND search SharePoint for our relevant past performance. Use the reasoning tool to think
   through the capture strategy before writing anything.
2. Decide which deliverable(s) to produce (usually ONE, at most two — don't produce ones that
   aren't warranted):
   - "rfi_response"        -> a formal response to a Sources Sought / RFI (Word / docx).
   - "white_paper"        -> a technical capability white paper (Word / docx).
   - "capability_briefing" -> a capability slide deck (PowerPoint / pptx).
   - "capture_plan"       -> an OPTIONAL internal capture-plan document (Word / docx) for the
                              BD team — produce this ONLY when it genuinely adds value
                              (e.g. a complex, high-value pursuit worth a gate-review doc).
3. For EACH deliverable you produce: write it in the right tone (customer-facing for the
   external ones, internal/strategic for the capture plan), grounded in the strategy + real
   past performance. Generate it with the right skill + python_repl_tool (pptx for a briefing,
   docx otherwise; simple filename, no path), then upload with s3_upload_tool(filename="...")
   and take the returned url.

Return `deliverables` — a list, one entry per document produced, each with doc_type, title,
doc_url, and a 2-3 sentence summary.

Ground everything in your research, our SharePoint past performance, and the opportunity data.
Be honest about unknowns — never fabricate facts about the customer, competitors, or our past
performance.

{sharepoint_tool_instructions()}

After generating + uploading the document(s), your FINAL message must be ONLY this JSON —
no prose, no markdown fences, no <reasoning> tags:
{{"deliverables": [{{"doc_type": "rfi_response" | "white_paper" | "capability_briefing" | "capture_plan", "title": "<title>", "doc_url": "<url>", "summary": "<2-3 sentences>"}}]}}
"""


def build_capture_agent(
    organization_id: str | None = None, employee_email: str | None = None
) -> Agent:
    """Build the Capture agent — grounded in the org's company profile (from its UEI) AND its
    SharePoint past-performance material (RBAC-filtered to the acting employee)."""
    company, profile = company_context(organization_id or "")

    def search_sharepoint_tool(query: str) -> str:
        """Search the company's SharePoint for past-performance / capability material relevant
        to `query` (e.g. "cybersecurity past performance", "SATCOM capability statement").
        Returns a JSON list of relevant docs/folders with paths + links — only documents the
        acting employee may read. Returns [] if nothing relevant."""
        return search_sharepoint(
            query, employee_email=employee_email, organization_id=organization_id or ""
        )

    return Agent(
        name="Capture",
        # Big output budget: the agent writes whole docx/pptx-generation scripts as a single
        # tool argument; 10k truncates the code mid-call -> "missing argument".
        model=get_chat_llm_agno(max_tokens=60000),
        tools=[
            create_exa_web_search_tool(),
            create_reasoning_tool(),
            search_sharepoint_tool,
            *load_sharepoint_tools(sharepoint_entity(organization_id or "")),
            python_repl_tool,
            s3_upload_tool,
        ],
        skills=get_capture_skills(),
        instructions=_instructions(company, profile),
        debug_mode=True,
    )


def generate_capture(opp: dict, employee_email: str | None = None) -> CaptureOutput:
    """Run the Capture agent on one opportunity → strategy + deliverable(s), uploaded."""
    set_session_id(f"capture-{opp.get('id', 'default')}")
    agent = build_capture_agent(str(opp.get("organization_id") or ""), employee_email)
    _skip = {"extra", "document_text"}  # document_text is appended cleanly below
    lines = [f"- {k}: {v}" for k, v in opp.items() if v not in (None, "", {}) and k not in _skip]
    message = "OPPORTUNITY:\n" + "\n".join(lines) + document_context(opp)
    # arun(): the doc-gen tools (python_repl_tool, s3_upload_tool) are async.
    result = asyncio.run(agent.arun(message))
    return coerce_output(result.content, CaptureOutput)
