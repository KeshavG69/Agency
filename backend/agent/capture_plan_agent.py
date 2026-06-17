"""The Capture Plan Agent.

Phase 2 — produces the INTERNAL capture strategy for one opportunity:
customer pain points, mission objectives, competitor assessment, win themes,
discriminators, partner strategy.

It researches the customer (web search), reasons through the strategy, then
GENERATES a capture-plan document (via the pdf/docx/pptx skills + python REPL),
uploads it to iDrive, and returns the document URL.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from agno.agent import Agent
from agno.skills import LocalSkills, Skills

from agent.analyst_agent import NEXAGEN_PROFILE
from client.llm_client import get_chat_llm_agno
from models.capture import CapturePlanResult
from utils.agno_tools import create_exa_web_search_tool, create_reasoning_tool
from utils.python_repl_tool import python_repl_tool, set_session_id
from utils.s3_upload_tool import s3_upload_tool
from utils.structured import coerce_output

_SKILLS_DIR = Path(__file__).resolve().parent / "skills"


@lru_cache(maxsize=1)
def get_capture_skills() -> Skills:
    """Load the pdf/docx/pptx skills once per process."""
    return Skills(loaders=[LocalSkills(str(_SKILLS_DIR), validate=True)])


_INSTRUCTIONS = f"""\
You are a capture strategist for Nexagen Networks (a government contractor).
You are given ONE opportunity. Produce an INTERNAL capture plan (for the BD team,
not for the customer).

NEXAGEN PROFILE:
{NEXAGEN_PROFILE}

Steps:
1. Research the customer/agency, the likely incumbent, and competitors with the web
   search tool. Use the reasoning tool to think.
2. Build the capture strategy covering: customer pain points, mission objectives,
   competitor assessment, win themes, discriminators, and partner / teaming strategy.
3. Generate a professional capture-plan DOCUMENT using the docx skill + python_repl_tool.
   Save it with a simple filename like "capture_plan.docx" (no directory path).
4. Upload it with s3_upload_tool(filename="capture_plan.docx") and take the returned url.
5. Return: the title; the FULL capture-plan text in `content` (this is handed to the
   Shaping Agent, so include the actual strategy — pain points, win themes, discriminators,
   partner strategy — not just a summary); the uploaded doc_url; and a 2-3 sentence summary.

Ground everything in your research and the opportunity data. Be honest about unknowns —
never fabricate facts about the customer or competitors.

After generating + uploading the document, your FINAL message must be ONLY this JSON —
no prose, no markdown fences, no <reasoning> tags:
{{"title": "<title>", "content": "<the full capture-plan text>", "doc_url": "<uploaded url>", "summary": "<2-3 sentences>"}}
"""


def build_capture_plan_agent() -> Agent:
    """Build the Capture Plan Agent (cached model + web search + reasoning + doc-gen)."""
    return Agent(
        name="Capture Plan",
        model=get_chat_llm_agno(),
        tools=[
            create_exa_web_search_tool(),
            create_reasoning_tool(),
            python_repl_tool,
            s3_upload_tool,
        ],
        skills=get_capture_skills(),
        instructions=_INSTRUCTIONS,
    )


def generate_capture_plan(opp: dict) -> CapturePlanResult:
    """Run the agent on one opportunity → generates + uploads the capture plan."""
    set_session_id(f"capture-{opp.get('id', 'default')}")
    agent = build_capture_plan_agent()
    lines = [f"- {k}: {v}" for k, v in opp.items() if v not in (None, "", {}) and k != "extra"]
    result = agent.run("OPPORTUNITY:\n" + "\n".join(lines))
    return coerce_output(result.content, CapturePlanResult)
