"""Structured output for the Library agent — one SharePoint document, read and sorted.

Every fact the model returns carries the exact words it came from (`evidence`: field -> quote).
The proof gate in agent/library_agent.py drops any fact whose quote is not actually in the
document, so a fact that survives is one a proposal writer can cite. The model is not trusted
to police itself — same rule as the relationship engine's nudge proof.
"""
from typing import Literal, Optional

from pydantic import BaseModel, Field

Category = Literal[
    "past_performance",
    "cpars",
    "resume",
    "past_proposal",
    "capture_plan",
    "capability_statement",
    "technical_approach",
    "management_plan",
    "pricing",
    "certification",
    "contract_legal",
    "solicitation",
    "other",
]

# Display labels — the frontend carries its own copy; this one feeds prompts and logs.
CATEGORY_LABELS: dict[str, str] = {
    "past_performance": "Past performance",
    "cpars": "CPARS / performance rating",
    "resume": "Resume",
    "past_proposal": "Past proposal",
    "capture_plan": "Capture plan",
    "capability_statement": "Capability statement",
    "technical_approach": "Technical approach",
    "management_plan": "Management / plans",
    "pricing": "Pricing",
    "certification": "Certification / compliance",
    "contract_legal": "Contract / legal",
    "solicitation": "Solicitation",
    "other": "Other",
}


class Grounded(BaseModel):
    """Base for a fact sheet: `evidence` maps each filled field to the verbatim quote it came from."""

    evidence: dict[str, str] = Field(
        default_factory=dict,
        description="For EVERY field you filled: field name -> the exact words copied from the document",
    )


class ContractFacts(Grounded):
    """One contract we performed — from a past-performance write-up, a CPARS, or a proposal's
    past-performance volume. A single document can describe several."""

    title: Optional[str] = Field(None, description="Contract / project name")
    customer: Optional[str] = Field(None, description="Customer organisation, e.g. 'U.S. Army PEO EIS'")
    agency: Optional[str] = Field(None, description="Top-level agency, e.g. 'Department of the Army'")
    contract_number: Optional[str] = None
    role: Optional[str] = Field(None, description="'prime' or 'subcontractor' (and to whom)")
    value_usd: Optional[float] = Field(None, description="Total contract value in US dollars, as a plain number")
    start_date: Optional[str] = Field(None, description="As written in the document")
    end_date: Optional[str] = Field(None, description="As written in the document")
    naics: Optional[str] = None
    scope: Optional[str] = Field(None, description="1-2 sentences: what we delivered")
    customer_poc: Optional[str] = Field(None, description="Customer point of contact (name, title, email/phone)")
    rating: Optional[str] = Field(None, description="CPARS / customer rating if stated, e.g. 'Exceptional'")


class PersonFacts(Grounded):
    """One person's resume. A resume book can hold several."""

    name: Optional[str] = None
    current_role: Optional[str] = None
    years_experience: Optional[float] = None
    clearance: Optional[str] = Field(None, description="As written, e.g. 'TS/SCI', 'Secret', 'Public Trust'")
    certifications: list[str] = Field(default_factory=list, description="e.g. PMP, CISSP, ITIL")
    education: Optional[str] = Field(None, description="Highest degree + field + school")
    skills: list[str] = Field(default_factory=list, description="Up to 10 core skills / labor-category fit")


class ProposalFacts(Grounded):
    """A proposal we submitted (or one of its volumes)."""

    agency: Optional[str] = None
    solicitation_number: Optional[str] = None
    title: Optional[str] = None
    volume: Optional[str] = Field(None, description="e.g. 'Technical', 'Management', 'Past Performance', 'Price'")
    submitted_date: Optional[str] = None
    outcome: Optional[str] = Field(None, description="'won', 'lost' or 'unknown' — only if the document says")


class CertificationFacts(Grounded):
    """A certification, registration, insurance or clearance record."""

    name: Optional[str] = Field(None, description="e.g. 'ISO 9001:2015', 'CMMI-DEV ML3', 'Facility Clearance'")
    issuer: Optional[str] = None
    number: Optional[str] = None
    expires: Optional[str] = Field(None, description="Expiry date as written")


class LegalFacts(Grounded):
    """A contract, teaming agreement, NDA, or similar."""

    kind: Optional[str] = Field(None, description="e.g. 'Teaming agreement', 'NDA', 'Subcontract', 'Prime contract'")
    parties: list[str] = Field(default_factory=list)
    effective_date: Optional[str] = None
    expires: Optional[str] = None


class LibraryAnalysis(BaseModel):
    """What the Library agent returns for ONE document."""

    category: Category
    confidence: float = Field(description="0-1: how sure you are of the category")
    summary: str = Field(description="2-3 sentences: what this document is and what it is useful for")
    contracts: list[ContractFacts] = Field(default_factory=list)
    people: list[PersonFacts] = Field(default_factory=list)
    proposal: Optional[ProposalFacts] = None
    certifications: list[CertificationFacts] = Field(default_factory=list)
    legal: Optional[LegalFacts] = None
