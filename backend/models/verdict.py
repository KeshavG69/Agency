"""The Analyst Agent's structured output."""
from typing import Literal, Optional

from pydantic import BaseModel, Field


class CallAction(BaseModel):
    """The call-plan entry — only produced for Bid opportunities."""

    contact: str = Field(description="Who to contact (POC name, or 'agency contracting office')")
    channel: Literal["email", "call"] = Field("email", description="How to reach out")
    talking_point: str = Field(description="One line: why we're reaching out and what to say")


class RiskFactor(BaseModel):
    """ONE named risk on a pursuit, with the reasoning behind it.

    The agent already separates hard disqualifiers from confirmable gates in its reasoning;
    this is that judgement made STRUCTURED so the UI can show a risk meter and a rep can
    filter on it, instead of the same finding being buried in a paragraph of rationale.
    """

    factor: Literal[
        "capability",     # can we self-perform, or do we depend on a sub/OEM we don't have?
        "eligibility",    # set-aside, clearance, certification, facility requirement
        "competition",    # incumbent strength, expected bidders, recompete
        "past_performance",  # do we have relevant, citable CPARS for this scope?
        "scope_clarity",  # attachments/specs unavailable — we don't fully know the ask
        "schedule",       # enough time to produce a compliant response?
        "contract_type",  # FFP on unclear scope, unfunded options, pricing exposure
        "teaming",        # need a partner/authorisation we do not currently hold
    ]
    severity: Literal["blocker", "high", "medium", "low"] = Field(
        description="'blocker' = a HARD DISQUALIFIER (forces No-Bid). Anything else is a risk "
                    "to weigh or a gate to confirm — never a reason to reject on its own."
    )
    note: str = Field(description="One line a rep can act on: what the risk is and why")


class AnalystVerdict(BaseModel):
    """The Analyst Agent's decision on a single opportunity."""

    bid_decision: Literal["Bid", "No-Bid", "Watch"]
    # The headline for the risk meter. Derived from the factors below, but stated explicitly
    # so the UI never has to re-derive it (and so it survives an empty factor list).
    risk_level: Literal["Low", "Medium", "High"] = Field(
        "Medium", description="Overall risk of pursuing. Any 'blocker' factor => High."
    )
    risk_factors: list[RiskFactor] = Field(
        default_factory=list,
        description="Every material risk, each with its own reasoning. Empty is valid — say "
                    "so rather than inventing risks to fill the list.",
    )
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
