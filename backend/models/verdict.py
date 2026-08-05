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
    # A "Watch" used to leave a card nothing ever re-read, so an early-stage notice that
    # became a real solicitation was simply missed. These two fields put the opportunity
    # back on the agent to-do list with a date and a reason a rep can read.
    recheck_after_days: Optional[int] = Field(
        None, ge=1, le=365,
        description="Days until this should be judged again. Required for 'Watch'; "
                    "null for a decided Bid or No-Bid.",
    )
    recheck_reason: Optional[str] = Field(
        None, description="One line a rep will read next to the date: what you expect to "
                          "have changed by then (e.g. 'the RFP should follow this Sources Sought')",
    )
