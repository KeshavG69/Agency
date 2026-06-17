"""Structured outputs for the capture agents."""
from typing import Literal

from pydantic import BaseModel, Field


class CapturePlanResult(BaseModel):
    """What the Capture Plan Agent returns after generating + uploading the doc."""

    title: str = Field(description="Title of the capture plan")
    content: str = Field(description="The FULL capture-plan text (passed to the Shaping Agent)")
    doc_url: str = Field(description="URL of the uploaded capture-plan document")
    summary: str = Field(description="2-3 sentence summary of the capture strategy")


class ShapingResult(BaseModel):
    """One external deliverable the Shaping Agent generated + uploaded."""

    doc_type: Literal["rfi_response", "white_paper", "capability_briefing"]
    title: str = Field(description="Title of the deliverable")
    doc_url: str = Field(description="URL of the uploaded document")
    summary: str = Field(description="2-3 sentence summary of the deliverable")


class ShapingOutput(BaseModel):
    """All deliverables the Shaping Agent decided this opportunity needs."""

    deliverables: list[ShapingResult] = Field(
        description="One or more external deliverables produced for the opportunity"
    )
