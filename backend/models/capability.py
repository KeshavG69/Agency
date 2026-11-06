"""The capability matrix: what the government wants done, how strong we are at each part (1-5),
the proof from our library, and the gaps.

The model proposes; code decides. Every requirement's quote is checked against the solicitation,
every piece of evidence against its own library file, and the score can never be higher than the
proven evidence allows (see agent/capability_agent.py, `ceiling`).
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

# The rubric, in one place: the Excel legend and the UI read it from here.
RUBRIC = {
    5: "We did this exact work on a contract that is active or ended in the last 3 years",
    4: "We did this exact work on a contract",
    3: "We did related work on a contract, or our documents describe doing this exact work",
    2: "Only people, tools or related documents — no contract",
    1: "Nothing in the library",
}


# ---- pass 1: what the government wants ----------------------------------------------------
class ExtractedRequirement(BaseModel):
    text: str = Field(description="The requirement in plain words, one sentence")
    source_ref: Optional[str] = Field(None, description="Where it is, e.g. 'PWS 3.2.1', 'SOW 4.1'")
    quote: str = Field("", description="5-30 words copied EXACTLY from the files")


class ExtractedArea(BaseModel):
    name: str = Field(description="The area of work, e.g. 'Service Desk', 'Cybersecurity'")
    requirements: list[ExtractedRequirement] = Field(default_factory=list)


class Contact(BaseModel):
    name: str
    role: Optional[str] = Field(None, description="e.g. 'Contracting Officer', 'Contract Specialist'")
    email: Optional[str] = None
    phone: Optional[str] = None
    quote: str = Field("", description="The exact words naming this person")
    verified: bool = True  # set by code


class Extraction(BaseModel):
    areas: list[ExtractedArea] = Field(default_factory=list)
    contacts: list[Contact] = Field(default_factory=list)


# ---- pass 2: how strong we are ----------------------------------------------------------
class Evidence(BaseModel):
    kind: Literal["contract", "person", "document"]
    source_id: str = Field(description="The `id` of the candidate it came from")
    name: str = Field("", description="Contract number or title, person's name, or file name")
    quote: str = Field(description="5-30 words copied EXACTLY from the candidate's quotable text")
    claim: str = Field(description="What it shows about this requirement, one short sentence")
    match: Literal["same", "related"] = Field(description="'same' = this exact work; 'related' = similar")
    verified: bool = True  # set by code
    file: Optional[str] = None  # set by code: the file's name
    web_url: Optional[str] = None  # set by code


class Scored(BaseModel):
    index: int = Field(description="The requirement's number as given")
    score: int = Field(ge=1, le=5)
    rationale: str = Field(description="Why this score, one or two plain sentences")
    evidence: list[Evidence] = Field(default_factory=list)
    gap: Optional[str] = Field(None, description="What we are missing, if the score is 1-2")
    action: Optional[str] = Field(None, description="How to close the gap: partner, hire, ask the CO…")


class AreaScores(BaseModel):
    requirements: list[Scored] = Field(default_factory=list)


# ---- what is stored -----------------------------------------------------------------------
class CapRequirement(BaseModel):
    id: str
    text: str
    source_ref: Optional[str] = None
    quote: str = ""
    verified: bool = True
    score: int = 1
    model_score: Optional[int] = None  # what the model proposed, before the proof cap
    rationale: str = ""
    evidence: list[Evidence] = Field(default_factory=list)
    gap: Optional[str] = None
    action: Optional[str] = None


class CapArea(BaseModel):
    name: str
    requirements: list[CapRequirement] = Field(default_factory=list)
