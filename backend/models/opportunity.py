"""Canonical opportunity schema.

Every ingestion source (Excel, SAM.gov, Outlook, ...) is normalized into this
one shape before being pushed to EspoCRM (the source of truth).
"""
from pydantic import BaseModel, Field

STAGES = ["Discover", "Qualify", "Capture", "Pursue", "Submitted", "Won", "Lost"]


class Opportunity(BaseModel):
    """Normalized government opportunity."""

    title: str = Field(description="Opportunity title (maps to EspoCRM Opportunity.name)")
    solicitation_number: str | None = Field(None, description="Solicitation / notice number")
    notice_id: str | None = Field(None, description="SAM.gov notice ID (stable dedup key)")
    agency: str | None = Field(None, description="Department / agency / office")
    naics: str | None = Field(None, description="NAICS code")
    psc_code: str | None = Field(None, description="Product/Service Code")
    place_of_performance: str | None = Field(None, description="Where the work is performed")
    set_aside: str | None = Field(None, description="WOSB, 8(a), SDVOSB, Full & Open, ...")
    opp_type: str | None = Field(None, description="Solicitation, Sources Sought, ...")
    posted_date: str | None = Field(None, description="Posted date (YYYY-MM-DD)")
    response_deadline: str | None = Field(None, description="Response due date (YYYY-MM-DD)")
    estimated_value: float | None = Field(None, description="Estimated value in USD")
    poc_name: str | None = Field(None, description="Point of contact name")
    poc_email: str | None = Field(None, description="Point of contact email")
    stage: str = Field("Discover", description="Pipeline stage")
    description: str | None = None
    link: str | None = Field(None, description="SAM.gov / source URL")
    # Full solicitation / PWS document — link + parsed text (grounds every agent)
    document_url: str | None = Field(None, description="URL or path to the solicitation PDF/document")
    document_text: str | None = Field(None, description="Parsed text of the solicitation document (LiteParse)")
    source: str = Field("excel", description="Where the record came from")
    extra: dict = Field(default_factory=dict, description="Unmapped source columns")
