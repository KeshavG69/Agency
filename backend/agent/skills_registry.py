"""The BD reasoning skills, loaded once and shared by every agent that needs them.

Two skill sets exist in this codebase and they are deliberately separate directories:

    agent/skills/     pdf, docx, pptx      — document PRODUCTION, loaded by the Capture agent
    agent/bd_skills/  grounding, evidence, — how to REASON about govcon BD work
                      company-research,
                      identity-matching,
                      writing-a-brief

`LocalSkills` loads a whole directory, so keeping them apart is what stops the Capture
agent being handed identity-matching and the research agent being handed .pptx authoring.

PROGRESSIVE DISCLOSURE — why carrying five skills is cheap. Only each skill's name and
description enter the system prompt (~3 KB for all five). The agent calls
`get_skill_instructions(name)` to pull the full text when it decides the skill applies.
The upshot is that this guidance is versioned markdown: it can be corrected without a
code deploy, which is exactly what you want for rules that get refined in response to a
bad answer someone spotted on a Tuesday.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from agno.skills import LocalSkills, Skills

BD_SKILLS_DIR = Path(__file__).resolve().parent / "bd_skills"


@lru_cache(maxsize=1)
def get_bd_skills() -> Skills:
    """The BD reasoning skills. Cached — one filesystem read per process."""
    return Skills(loaders=[LocalSkills(str(BD_SKILLS_DIR), validate=True)])
