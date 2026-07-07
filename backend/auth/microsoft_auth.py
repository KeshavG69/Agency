"""Microsoft OAuth authentication service ("Sign in with Microsoft").

A pure IDENTITY check — distinct from the Outlook/SharePoint DATA connections (which go
through Composio and need long-lived, refreshable tokens for ongoing API access). Here we
only need Microsoft to vouch for who the person is, once; we never store or refresh
Microsoft's tokens afterward — MSAL (Microsoft's own auth library) does the actual OAuth
code exchange + ID token signature/expiry verification, and we discard its tokens the
moment we've read the verified identity out of them.
"""
from typing import Optional, Tuple

import msal

from .config import (
    MICROSOFT_AUTHORITY,
    MICROSOFT_CLIENT_ID,
    MICROSOFT_CLIENT_SECRET,
    MICROSOFT_REDIRECT_URI,
)
from .models import MicrosoftUserProfile

# The minimum Graph scope needed to read the signed-in person's own basic profile — this is
# an identity check, not a data connection, so nothing broader (Mail.*, Sites.*, etc.) belongs
# here. `openid profile email` are the standard OIDC scopes MSAL always includes implicitly;
# User.Read is what lets us additionally confirm scope consent for the /me profile read.
SCOPES = ["User.Read"]


class MicrosoftAuthService:
    """Wraps msal.ConfidentialClientApplication for the login-only authorization-code flow."""

    def __init__(self):
        if not MICROSOFT_CLIENT_ID or not MICROSOFT_CLIENT_SECRET:
            raise ValueError(
                "MICROSOFT_CLIENT_ID / MICROSOFT_CLIENT_SECRET environment variables are required"
            )
        self._app = msal.ConfidentialClientApplication(
            client_id=MICROSOFT_CLIENT_ID,
            client_credential=MICROSOFT_CLIENT_SECRET,
            authority=MICROSOFT_AUTHORITY,
        )

    def get_authorization_url(self, state: str) -> str:
        """The URL to send the browser to — Microsoft will redirect back to
        MICROSOFT_REDIRECT_URI with `?code=...&state=...` once the person signs in."""
        return self._app.get_authorization_request_url(
            scopes=SCOPES,
            state=state,
            redirect_uri=MICROSOFT_REDIRECT_URI,
        )

    def acquire_token(self, code: str) -> Tuple[Optional[MicrosoftUserProfile], Optional[str]]:
        """Exchange the authorization code for tokens and return the VERIFIED identity.

        MSAL validates the ID token's signature (against Microsoft's published signing keys),
        issuer, audience, and expiry internally before returning `id_token_claims` — we never
        parse or verify the JWT ourselves. Returns (profile, None) on success, or (None,
        error_message) on failure — never raises, since a bad/expired code is an expected,
        recoverable case the caller should turn into a clean 401, not a 500.
        """
        result = self._app.acquire_token_by_authorization_code(
            code=code,
            scopes=SCOPES,
            redirect_uri=MICROSOFT_REDIRECT_URI,
        )
        if "error" in result:
            return None, result.get("error_description") or result.get("error")

        claims = result.get("id_token_claims") or {}
        email = claims.get("email") or claims.get("preferred_username")
        oid = claims.get("oid")
        if not email or not oid:
            return None, "Microsoft did not return an email/identity for this account"

        profile = MicrosoftUserProfile(
            oid=oid,
            name=claims.get("name", ""),
            given_name=claims.get("given_name", ""),
            family_name=claims.get("family_name", ""),
            email=email.lower(),
            email_verified=True,
        )
        return profile, None
