"""Diagnostic: run the Analyst Agent on one opportunity and dump the RAW response.

Goal: find WHERE the model's text is going when `result.content` comes back empty.
Run:  uv run python scripts/test_analyst.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

    # import utils.agno_patches  # noqa: E402,F401  -- ThinkingBlock signature patch
    # import utils.agno_patches_gemini  # noqa: E402,F401  -- Gemini reasoning_details preservation
from app.settings import settings  # noqa: E402
from agent.analyst_agent import build_analyst_agent, _format_opportunity  # noqa: E402

OPP = {
    "title": "C5ISR Network Modernization Engineering Support Services",
    "solicitation_number": "W15P7T-26-R-0001",
    "agency": "U.S. Army CECOM",
    "naics": "541512",
    "set_aside": "Women-Owned Small Business (WOSB)",
    "opp_type": "Sources Sought",
    "posted_date": "2026-06-10",
    "response_deadline": "2026-07-01",
    "estimated_value": "$25,000,000",
    "place_of_performance": "Aberdeen Proving Ground, MD",
    "poc_name": "Jane Contracting Officer",
    "poc_email": "jane.co@army.mil",
    "description": "Engineering and integration support for tactical network modernization (WIN-T follow-on).",
}

print(f"ANALYST_MODEL = {settings.ANALYST_MODEL}\n")

agent = build_analyst_agent()
msg = _format_opportunity(OPP, "2026-06-18")
result = agent.run(msg)

print("=" * 70)
print("type(result)        :", type(result))
print("type(result.content):", type(result.content))
print("repr(result.content):", repr(result.content)[:500])
print("=" * 70)

# Dump anything that might hold the real text.
for attr in ("content", "reasoning_content", "thinking", "reasoning"):
    val = getattr(result, attr, "<none>")
    print(f"\n--- result.{attr} ---\n{repr(val)[:800]}")

# Walk the messages to see assistant content / reasoning.
print("\n" + "=" * 70 + "\nMESSAGES\n" + "=" * 70)
for i, m in enumerate(getattr(result, "messages", []) or []):
    role = getattr(m, "role", "?")
    content = getattr(m, "content", None)
    rc = getattr(m, "reasoning_content", None)
    tcs = getattr(m, "tool_calls", None)
    print(f"\n[{i}] role={role}")
    if content:
        print(f"    content: {repr(content)[:600]}")
    if rc:
        print(f"    reasoning_content: {repr(rc)[:300]}")
    if tcs:
        names = [tc.get("function", {}).get("name") if isinstance(tc, dict) else tc for tc in tcs]
        print(f"    tool_calls: {names}")
