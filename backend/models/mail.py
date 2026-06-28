"""The Mail Agent's output — a structured draft outreach email.

Shaped to serve two consumers from one JSON object:
  1. the frontend mail artifact (render to / subject / body / why-it's-grounded), and
  2. the Composio OUTLOOK_SEND_EMAIL tool (map straight onto its params).

The agent NEVER sends — it returns this draft. The user reviews it in the mail
artifact and clicks "Send", which posts the payload and calls OUTLOOK_SEND_EMAIL.
"""
from typing import Optional

from pydantic import BaseModel, Field


class MailDraft(BaseModel):
    to: Optional[str] = Field(None, description="Primary recipient email address.")
    to_name: Optional[str] = Field(None, description="Recipient display name (for the UI + Outlook).")
    cc: list[str] = Field(default_factory=list, description="CC recipient email addresses.")
    subject: str
    body: str = Field(..., description="The email content (plain text unless is_html=True).")
    is_html: bool = Field(False, description="True if `body` is HTML, False if plain text.")
    grounded_on: list[str] = Field(
        default_factory=list,
        description="The real experience / SharePoint material the email draws on. "
        "UI-only traceability — NOT sent with the email.",
    )

    def outlook_send_args(self, user_id: str = "me", save_to_sent_items: bool = True) -> dict:
        """Map this draft onto OUTLOOK_SEND_EMAIL's parameters (send immediately)."""
        args: dict = {
            "to": self.to,
            "subject": self.subject,
            "body": self.body,
            "is_html": self.is_html,
            "user_id": user_id,
            "save_to_sent_items": save_to_sent_items,
        }
        if self.to_name:
            args["to_name"] = self.to_name
        if self.cc:
            args["cc_emails"] = self.cc
        return args
