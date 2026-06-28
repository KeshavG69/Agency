from google.auth.transport import requests
from google.oauth2 import id_token
from typing import Optional
from .models import GoogleUserProfile
from .config import GOOGLE_CLIENT_ID


class GoogleAuthService:
    """Google OAuth authentication service"""

    def __init__(self):
        if not GOOGLE_CLIENT_ID:
            raise ValueError("GOOGLE_CLIENT_ID environment variable is required")

    def verify_google_token(self, token: str) -> Optional[GoogleUserProfile]:
        """
        Verify Google JWT token and extract user information

        Args:
            token: Google JWT token from frontend

        Returns:
            GoogleUserProfile if token is valid, None otherwise
        """
        try:
            # Verify the token
            idinfo = id_token.verify_oauth2_token(
                token,
                requests.Request(),
                GOOGLE_CLIENT_ID
            )

            # Token is valid, extract user information
            user_profile = GoogleUserProfile(
                sub=idinfo['sub'],
                name=idinfo.get('name', ''),
                given_name=idinfo.get('given_name', ''),
                family_name=idinfo.get('family_name', ''),
                picture=idinfo.get('picture', ''),
                email=idinfo['email'],
                email_verified=idinfo.get('email_verified', False)
            )

            return user_profile

        except ValueError as e:
            # Token verification failed
            print(f"Google token verification failed: {str(e)}")
            return None
        except Exception as e:
            # Other errors
            print(f"Error verifying Google token: {str(e)}")
            return None
