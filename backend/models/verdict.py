"""The Analyst Agent's structured output."""
from typing import Literal, Optional

from pydantic import BaseModel, Field


class CallAction(BaseModel):
    """The call-plan entry — only produced for Bid opportunities."""

    contact: str = Field(description="Who to contact (POC name, or 'agency contracting office')")
    channel: Literal["email", "call"] = Field("email", description="How to reach out")
    talking_point: str = Field(description="One line: why we're reaching out and what to say")


class AnalystVerdict(BaseModel):
    """The Analyst Agent's decision on a single opportunity."""

    bid_decision: Literal["Bid", "No-Bid", "Watch"]
    priority_score: int = Field(description="0–100; higher = pursue sooner", ge=0, le=100)
    rationale: str = Field(
        description="2–5 sentences: the fit read, the specific gates to confirm internally "
        "(e.g. IDIQ/vehicle access, facility clearance, past performance), the winnability read, "
        "and the recommendation"
    )
    recommended_stage: Literal["Qualify", "Discover", "No-Bid"] = Field(
        description="Bid -> Qualify, Watch -> Discover, No-Bid -> No-Bid"
    )
    call_action: Optional[CallAction] = Field(
        None, description="Present only when bid_decision == 'Bid'"
    )
