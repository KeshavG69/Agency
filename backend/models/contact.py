"""CRM Agent output models — the ranked shortlist of who to engage."""
from typing import Optional

from pydantic import BaseModel, Field


class ContactRecommendation(BaseModel):
    name: str
    email: Optional[str] = None
    company: Optional[str] = None
    title: Optional[str] = None
    relevance_score: int = Field(description="0–100; higher = more valuable for this opp", ge=0, le=100)
    reason: str = Field(description="1–2 lines: why this person matters for THIS opportunity")
    suggested_outreach: str = Field(description="One line: how to open the conversation")


class CRMResult(BaseModel):
    recommendations: list[ContactRecommendation] = Field(default_factory=list)
