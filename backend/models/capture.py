"""Structured outputs for the Capture agent."""
from typing import Literal

from pydantic import BaseModel, Field


class CaptureDeliverable(BaseModel):
    """One document the Capture agent generated + uploaded. `capture_plan` is the optional
    INTERNAL strategy doc; the rest are EXTERNAL, customer-facing deliverables.

    The download URL is NOT taken from the model — it is resolved server-side from the actual
    s3_upload_tool result, matched by `filename`. This prevents the model from fabricating a
    URL for a file it never uploaded."""

    doc_type: Literal["capture_plan", "rfi_response", "white_paper", "capability_briefing"]
    title: str = Field(description="Title of the deliverable")
    filename: str = Field(description="The EXACT filename passed to s3_upload_tool for this document")
    summary: str = Field(description="2-3 sentence summary of the deliverable")


class CaptureOutput(BaseModel):
    """All deliverables the Capture agent decided this opportunity needs."""

    deliverables: list[CaptureDeliverable] = Field(
        description="One or more documents produced for the opportunity"
    )
