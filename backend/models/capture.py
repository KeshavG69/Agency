"""Structured outputs for the Capture agent."""
from typing import Literal

from pydantic import BaseModel, Field


class CaptureDeliverable(BaseModel):
    """One document the Capture agent generated + uploaded. `capture_plan` is the optional
    INTERNAL strategy doc; the rest are EXTERNAL, customer-facing deliverables."""

    doc_type: Literal["capture_plan", "rfi_response", "white_paper", "capability_briefing"]
    title: str = Field(description="Title of the deliverable")
    doc_url: str = Field(description="URL of the uploaded document")
    summary: str = Field(description="2-3 sentence summary of the deliverable")


class CaptureOutput(BaseModel):
    """All deliverables the Capture agent decided this opportunity needs."""

    deliverables: list[CaptureDeliverable] = Field(
        description="One or more documents produced for the opportunity"
    )
