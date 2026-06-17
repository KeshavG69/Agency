"""
Python REPL tool for the pricing agent.

Adapted from Kroolo's enterprise-fastapi. Each chat session gets an isolated
TemporaryDirectory; Python code runs in-process with per-session locals/globals.
If execution fails, the LLM is asked to auto-correct once and re-run.
"""

from agno.tools import tool
from langchain_experimental.utilities import PythonREPL
from client.llm_client import get_chat_llm
import logging
from typing import Dict, Any
import os
import re
import tempfile
import contextvars

from app.settings import settings

logger = logging.getLogger(__name__)

# Keep TemporaryDirectory objects alive per session so files persist across
# multiple tool calls within the same chat session.
_session_temp_dirs: Dict[str, tempfile.TemporaryDirectory] = {}

# Persistent PythonREPL per session — variables, imports, and helper functions
# defined in one tool call stay alive for the next call in the same session.
_session_repls: Dict[str, Any] = {}

# Session ID is read from contextvar at runtime so tools don't have to pass it.
_current_session_id = contextvars.ContextVar("session_id", default="default")


def get_session_id() -> str:
    return _current_session_id.get()


def set_session_id(session_id: str):
    """Set the current session_id in the context. Call before agent.run()."""
    _current_session_id.set(session_id)


class SafePythonREPL:
    """Session-scoped wrapper around LangChain's PythonREPL."""

    def __init__(self, session_id: str = None):
        self.session_id = session_id or "default"

        # Reuse the per-session REPL so imports, helpers, and variables defined
        # in an earlier tool call remain available for the next call.
        if self.session_id in _session_repls:
            self._repl = _session_repls[self.session_id]
        else:
            self._repl = PythonREPL()
            # exec() uses separate globals/locals by default; top-level imports
            # end up in locals and helper function bodies can't see them.
            # Unifying them fixes NameError inside nested functions.
            self._repl.locals = self._repl.globals
            _session_repls[self.session_id] = self._repl

        if self.session_id in _session_temp_dirs:
            self._temp_dir_obj = _session_temp_dirs[self.session_id]
            self.working_dir = self._temp_dir_obj.name
        else:
            self._temp_dir_obj = tempfile.TemporaryDirectory(
                prefix=f"priceiq_repl_{self.session_id}_"
            )
            self.working_dir = self._temp_dir_obj.name
            _session_temp_dirs[self.session_id] = self._temp_dir_obj
            logger.info(
                f"[python_repl] new temp dir for session={self.session_id}: {self.working_dir}"
            )

    def run(self, code: str) -> str:
        try:
            full_code = (
                f"import os\n"
                f"os.chdir({self.working_dir!r})\n"
                f"{code}"
            )
            return self._repl.run(full_code)
        except Exception as e:
            logger.error(f"[python_repl] exec error: {e}", exc_info=True)
            return f"Error executing Python code: {e}"


def cleanup_session(session_id: str):
    """Release the temp directory and REPL state for a session (called on chat close)."""
    _session_repls.pop(session_id, None)
    if session_id in _session_temp_dirs:
        temp_dir = _session_temp_dirs.pop(session_id)
        temp_dir.cleanup()
        logger.info(f"[python_repl] cleaned up session={session_id}")


async def _correct_python_with_llm(code: str, error: str) -> str:
    """Ask the LLM to fix broken Python once, given the error output."""
    llm = get_chat_llm(
        model="anthropic/claude-sonnet-4.6",
        api_key=settings.OPENROUTER_API_KEY,
        base_url="https://openrouter.ai/api/v1",
    )

    messages = [
        {
            "role": "system",
            "content": (
                "You are an expert Python developer. Fix the broken Python code based on the error. "
                "Return ONLY the corrected Python code with no explanations, no markdown, no backticks."
            ),
        },
        {
            "role": "user",
            "content": f"Error:\n{error}\n\nBroken Code:\n{code}\n\nReturn only the fixed Python code.",
        },
    ]

    response = await llm.ainvoke(messages)
    fixed = (response.content or "").strip()
    fixed = re.sub(r"^```python\s*\n", "", fixed)
    fixed = re.sub(r"^```\s*\n", "", fixed)
    fixed = re.sub(r"\n```\s*$", "", fixed)
    fixed = fixed.strip()

    if not fixed:
        raise RuntimeError("LLM returned empty code after correction attempt")
    return fixed


@tool(stop_after_tool_call=False)
async def python_repl_tool(code: str, description: str = "") -> Dict[str, Any]:
    """
    🐍 Execute Python code to compute pricing figures from the proposal state.

    USE THIS when the user asks for a computation that isn't pre-computed in
    the <proposal_state> block — e.g. "what's the weighted-average FBLR across
    all senior positions?", "show me the top 5 most expensive positions by
    year-3 cost", "compare prime vs sub labor share as percentages".

    HOW TO USE:
    1. Parse the <proposal_state> JSON into a Python dict.
    2. Implement the formula from FORMULAS.md using the raw inputs.
    3. Print the result.

    CANONICAL FORMULAS (all in FORMULAS.md):
    - BLS FBLR: dl + fringe + oh + ga + fee (fee-inclusive)
    - GSA cost: gsaRate × (1 − discount) × hours
    - Travel: amount × (1 + ga_rate)
    - ODC: amount × (1 + smh_rate)
    - Sub billable: baseRate × (1 + smh + ga_passthrough + sub_fee)

    DOCUMENT GENERATION (write a file → s3_upload_tool returns a download URL):
    - reportlab     → PDF reports (SimpleDocTemplate, Paragraph, Table)
    - python-pptx   → PowerPoint decks (from pptx import Presentation)
    - python-docx   → Word documents (from docx import Document)
    - openpyxl      → Excel workbooks (from openpyxl import Workbook)
    - xlsxwriter    → Excel with charts/formatting
    - matplotlib    → PNG/SVG chart images
    Save with a simple filename ("pricing_summary.pdf"), then call
    s3_upload_tool(filename="pricing_summary.pdf") to get a shareable URL.

    GROUND RULES:
    - Do NOT invent numbers. Every figure must come from the state block.
    - For simple reads (already in the state), SKIP this tool and quote directly.
    - Use print() — the tool returns what is printed.
    - Format money as $1,234,567.89, rates as 7.11%, hours as 1,920.

    EXAMPLE — weighted-average FBLR across a subset:
    ```python
    import json
    state = json.loads(STATE)  # STATE is the proposal_state you pulled from context
    positions = [p for p in state["positions"] if p["location_type"] == "On-Site"]
    total_cost  = sum(p["total_amount"] for p in positions)
    total_hours = sum(p["total_hours"]  for p in positions)
    avg_fblr = total_cost / total_hours if total_hours else 0
    print(json.dumps({"avg_fblr": round(avg_fblr, 2), "positions": len(positions)}))
    ```

    Args:
        code: Python code to execute. Must include print() statements.
        description: Short past-tense description, e.g. "Computed avg FBLR for on-site".

    Returns:
        Dict with success, output, working_dir, error.
    """
    import json as _json

    def _parse_json(output: str):
        try:
            parsed = _json.loads(output.strip())
            return parsed if isinstance(parsed, dict) else None
        except (ValueError, _json.JSONDecodeError):
            return None

    try:
        session_id = get_session_id()
        repl = SafePythonREPL(session_id=session_id)

        output = repl.run(code)

        if output and ("Error" in output or "Traceback" in output):
            logger.warning(f"[python_repl] error, attempting LLM correction: {output[:200]}")
            try:
                fixed_code = await _correct_python_with_llm(code, output)
                fixed_output = repl.run(fixed_code)
                if fixed_output and ("Error" in fixed_output or "Traceback" in fixed_output):
                    return {
                        "success": False,
                        "output": fixed_output,
                        "data": None,
                        "working_dir": repl.working_dir,
                        "error": "Execution failed even after LLM correction",
                    }
                return {
                    "success": True,
                    "output": fixed_output or "(no output)",
                    "data": _parse_json(fixed_output or ""),
                    "working_dir": repl.working_dir,
                    "error": None,
                }
            except Exception as fix_err:
                logger.error(f"[python_repl] LLM correction failed: {fix_err}")
                return {
                    "success": False,
                    "output": output,
                    "data": None,
                    "working_dir": repl.working_dir,
                    "error": "Execution errored and correction attempt failed",
                }

        return {
            "success": True,
            "output": output or "(no output)",
            "data": _parse_json(output or ""),
            "working_dir": repl.working_dir,
            "error": None,
        }

    except Exception as e:
        logger.error(f"[python_repl] tool failure: {e}", exc_info=True)
        return {
            "success": False,
            "output": "",
            "data": None,
            "working_dir": None,
            "error": f"Failed to execute: {e}",
        }
