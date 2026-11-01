"""What a solicitation requires us to submit — the Requirements agent's structured output.

Every solicitation is different: the instructions may be a separate Section L file, a section
inside the RFP, an addendum to FAR 52.212-1 or a paragraph in an email-style RFQ, and even the
rules flip (one counts the cover page toward the limit, another excludes it). So nothing here
hardcodes a list of volumes: the checklist is whatever THIS solicitation asks for.

Each line carries the words it was read from, and agent/requirements_agent.py checks them. A line
that can't be confirmed is KEPT and flagged, never removed: tables come out of the parser
scrambled, so a true quote can fail to match, and a blank due date reads as "none stated" while a
deleted requirement can disqualify the bid. A person reviews this list, and the flag says where.
"""
from typing import Literal, Optional

from pydantic import BaseModel, Field

ItemKind = Literal[
    "volume", "plan", "resume", "form", "certification", "pricing",
    "past_performance", "letter", "attachment", "other",
]


class RequirementItem(BaseModel):
    """One thing that must be submitted."""

    title: str = Field(description="Short name, as the solicitation names it")
    kind: ItemKind
    detail: str = Field("", description="What it must contain, in one or two plain sentences")
    page_limit: Optional[str] = Field(None, description="As stated, incl. exclusions: '30 pages max, excluding Draft CDRLs'")
    # Its own quote: the limit is often stated in a different paragraph from the item itself.
    page_limit_quote: str = Field("", description="The exact words stating the page limit")
    format: Optional[str] = Field(None, description="Required file type or template, e.g. 'Excel workbook', 'Attachment L001'")
    source_ref: Optional[str] = Field(None, description="Where it is asked for, e.g. 'L.3', 'Section M.4', 'Item 5'")
    quote: str = Field("", description="The exact words asking for it, copied from the document")
    verified: bool = True  # set by the proof gate, never by the model


class PassFailRule(BaseModel):
    """A rule that makes an offer unawardable if missed (Deficiency, 'ineligible', 'rejected')."""

    rule: str
    source_ref: Optional[str] = None
    quote: str = ""
    verified: bool = True


class EvaluationFactor(BaseModel):
    name: str
    importance: Optional[str] = Field(None, description="e.g. 'Most important', 'Pass/fail'")


class Grounded(BaseModel):
    evidence: dict[str, str] = Field(
        default_factory=dict,
        description="For EVERY field you filled: field name -> the exact words copied from the document",
    )
    unverified: list[str] = Field(default_factory=list, description="Leave empty; set by code")


class Submission(Grounded):
    due: Optional[str] = Field(None, description="Due date, time and time zone, as written")
    method: Optional[str] = Field(None, description="How it is submitted: portal, email, eBuy, hand delivery")
    address: Optional[str] = Field(None, description="The portal name or email address")
    file_rules: Optional[str] = Field(None, description="File type, size limit, file naming, email subject line, copies")
    questions_due: Optional[str] = Field(None, description="Deadline for questions, as written")


class FormatRules(Grounded):
    font: Optional[str] = None
    margins: Optional[str] = None
    page_size: Optional[str] = None
    page_counting: Optional[str] = Field(
        None, description="What counts toward page limits and what doesn't (cover, TOC, appendices)")


class Evaluation(Grounded):
    basis: Optional[str] = Field(None, description="e.g. 'Best-value tradeoff', 'Lowest price technically acceptable'")
    factors: list[EvaluationFactor] = Field(default_factory=list, description="In order of importance")


class PastPerformanceRules(Grounded):
    references: Optional[str] = Field(None, description="How many, e.g. 'Up to three'")
    recency: Optional[str] = None
    relevancy: Optional[str] = None
    prime_rule: Optional[str] = Field(None, description="e.g. 'At least two as prime contractor'")
    form: Optional[str] = Field(None, description="Questionnaire or form to use, e.g. 'Attachment L001'")


class ProposalOutline(BaseModel):
    """What the Requirements agent returns for one solicitation package."""

    found_instructions: bool = Field(description="False if the files contain no submission instructions")
    summary: str = Field(description="1-2 plain sentences: what must be submitted, and how")
    submission: Submission = Field(default_factory=Submission)
    format_rules: FormatRules = Field(default_factory=FormatRules)
    evaluation: Evaluation = Field(default_factory=Evaluation)
    past_performance: PastPerformanceRules = Field(default_factory=PastPerformanceRules)
    pass_fail_rules: list[PassFailRule] = Field(default_factory=list)
    prohibited: list[str] = Field(default_factory=list, description="Things that must NOT be submitted, verbatim")
    items: list[RequirementItem] = Field(default_factory=list)
