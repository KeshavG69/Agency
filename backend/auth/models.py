from pydantic import BaseModel, EmailStr
from typing import Optional
from datetime import datetime


class UserSignup(BaseModel):
    firstName: str
    lastName: str
    email: EmailStr
    password: str
    terms_accepted: bool


class UserLogin(BaseModel):
    email: EmailStr
    password: str


class UserResponse(BaseModel):
    id: str
    firstName: str
    lastName: str
    email: str
    organization_id: Optional[str] = None
    role: Optional[str] = None
    status: Optional[str] = None
    createdAt: datetime
    # Terms and Conditions
    terms_accepted_version: Optional[str] = None
    terms_accepted_at: Optional[datetime] = None
    needs_terms_acceptance: bool = False


class Token(BaseModel):
    access_token: str
    token_type: str


class TokenData(BaseModel):
    email: Optional[str] = None


class BlacklistedToken(BaseModel):
    token: str
    blacklisted_at: datetime
    expires_at: datetime


class LogoutResponse(BaseModel):
    message: str
    timestamp: datetime


class GoogleLoginRequest(BaseModel):
    credential: str


class GoogleUserProfile(BaseModel):
    sub: str
    name: str
    given_name: str
    family_name: str
    picture: str
    email: str
    email_verified: bool


class RefreshToken(BaseModel):
    """
    Refresh token model for MongoDB storage
    """
    token_id: str  # UUID for the token
    user_email: str
    refresh_token_hash: str  # bcrypt hash of actual token
    token_family_id: str  # Track token rotation chain
    expires_at: datetime
    created_at: datetime
    device_info: Optional[str] = None
    ip_address: Optional[str] = None
    is_revoked: bool = False
    revoked_at: Optional[datetime] = None


class TokenRefreshResponse(BaseModel):
    """
    Response model for token refresh endpoint
    """
    message: str = "Token refreshed successfully"


class RefreshTokenValidationResult(BaseModel):
    """
    Internal model for refresh token validation results
    """
    valid: bool
    user_email: Optional[str] = None
    token_family_id: Optional[str] = None
    token_id: Optional[str] = None
    reason: Optional[str] = None  # Reason for failure if not valid
