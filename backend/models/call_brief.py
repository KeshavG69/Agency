"""Call-brief output model — the prep for ONE person on a pursuit.

One brief per contact, but each is grounded in that person's WHOLE ORGANISATION: the agent
reads every email the rep's mailbox holds with anyone at their domain, so prepping Jane @
cbp.dhs.gov uses the full CBP correspondence, not just Jane's own thread. That is what makes
`approach` — "talk to this person like this" — worth reading: it is informed by everything
happening with their org, not one thread.
"""
from typing import Optional

from pydantic import BaseModel, Field


class CallBrief(BaseModel):
    """Prep for one contact: where we stand with them, and how to open the call.

    No contact name/email here on purpose — the caller already knows who the brief is for (it
    is the tab they clicked, and the stored doc carries `contact_email`). Asking the model to
    echo it back only spends tokens and invites it to "correct" a name we already hold.
    """

    org_name: str = Field(description="Their organisation")
    summary: str = Field(
        description="2-3 sentences: who this person is and where we stand with them"
    )
    relationship: Optional[str] = Field(
        None,
        description="Our history with THIS person specifically — who last spoke, when, about "
        "what. Say plainly if we have never corresponded with them.",
    )
    org_context: Optional[str] = Field(
        None,
        description="What is going on with their wider organisation, from mail with anyone "
        "there — other threads, other people, other business in flight",
    )
    approach: str = Field(
        description="How to talk to THIS person: the angle and tone to take, given their role "
        "and our standing with them. One or two lines the rep reads right before dialling.",
    )
    talking_points: list[str] = Field(
        default_factory=list,
        description="2-4 things to raise with this person, grounded in the mail and/or pursuit",
    )
    open_threads: list[str] = Field(
        default_factory=list,
        description="Unresolved / in-flight items involving them or their org worth raising",
    )
    suggested_ask: str = Field(
        description="The one concrete next step / callback to secure from this person"
    )
