"""
Authentication router for user management endpoints.
Cookie-based authentication with refresh token rotation.
"""

from fastapi import APIRouter, HTTPException, Depends, status, Response, Request, Cookie
from datetime import timedelta, datetime
from typing import Optional
from pydantic import BaseModel, EmailStr

# Authentication imports
from auth.models import UserSignup, UserLogin, UserResponse, LogoutResponse, GoogleLoginRequest, TokenRefreshResponse
from auth.crud import UserCRUD
from auth.utils import create_access_token, verify_token, ACCESS_TOKEN_EXPIRE_MINUTES
from auth.cookies import (
    set_access_token_cookie,
    set_refresh_token_cookie,
    clear_auth_cookies,
    COOKIE_ACCESS_TOKEN_NAME,
    COOKIE_REFRESH_TOKEN_NAME
)
from auth.refresh_token import (
    create_refresh_token,
    validate_refresh_token,
    rotate_refresh_token,
    revoke_refresh_token
)
from auth.google_auth import GoogleAuthService
from auth.dependencies import get_current_user as get_current_user_from_deps
from utils.email_verification import get_email_verification_crud
from utils.password_reset import get_password_reset_crud
from utils.onboarding import get_onboarding_crud
from utils.helpers import serialize_doc

router = APIRouter(prefix="/auth", tags=["authentication"])


class VerifyEmailRequest(BaseModel):
    """Request body for email verification"""
    token: str


class ResendVerificationRequest(BaseModel):
    """Request body for resending verification email"""
    email: EmailStr


class ForgotPasswordRequest(BaseModel):
    """Request body for password reset request"""
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    """Request body for password reset"""
    token: str
    new_password: str


class ChangePasswordRequest(BaseModel):
    """Request body for authenticated password change"""
    current_password: str
    new_password: str


class UpdateProfileRequest(BaseModel):
    """Request body for updating user profile"""
    name: str


@router.post("/signup")
async def signup(user_data: UserSignup):
    """
    Register a new user and send email verification

    Args:
        user_data: User signup data including firstName, lastName, email, password, terms_accepted

    Returns:
        Message and email address for verification
    """
    try:
        # Open self-registration (PriceIQ model): anyone can create an account. Each
        # signup creates its OWN organization (the user becomes its admin) and must
        # verify their email before logging in. Teammates are added later via
        # invitations INTO that org (invited users are verified by the invite link).
        # Validate terms acceptance
        if not user_data.terms_accepted:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="You must accept the Terms and Conditions to create an account"
            )

        # Create user with unverified status
        user = UserCRUD.create_user(user_data, email_verified=False)

        # Create verification token and send email
        verification_crud = get_email_verification_crud()

        try:
            # user.id is a UUID string, pass it directly
            verification_crud.create_verification(user.id, user.email)
        except Exception as e:
            print(f"Failed to send verification email: {e}")
            # Continue even if email fails - user can resend later

        return {
            "message": "Account created successfully. Please check your email to verify your account.",
            "email": user.email,
            "requires_verification": True
        }
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create user: {str(e)}"
        )


@router.post("/login")
async def login(user_data: UserLogin, request: Request):
    """
    Authenticate user and return JWT tokens in response body

    Args:
        user_data: User login data including email and password
        request: FastAPI Request object for device info

    Returns:
        Dict with tokens and user info
    """
    try:
        user = UserCRUD.authenticate_user(user_data.email, user_data.password)
        if not user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Incorrect email or password",
            )

        # Get full user document to extract organization info
        from auth.database import get_mongodb_client
        users_collection = get_mongodb_client().get_users_collection()
        user_doc = users_collection.find_one({"email": user_data.email})

        # Check email verification status
        if not user_doc.get("email_verified", False):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Please verify your email address before logging in. Check your inbox for the verification link."
            )

        # Extract role and organization from organizations array
        current_org_id = user_doc.get("current_organization_id")
        organizations = user_doc.get("organizations", [])

        if organizations and current_org_id:
            current_org = next(
                (org for org in organizations if org["organization_id"] == current_org_id),
                None
            )
            if current_org:
                user.organization_id = str(current_org["organization_id"])
                user.role = current_org["role"]
                user.status = current_org["status"]

        # Create access token
        access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
        access_token = create_access_token(
            data={"sub": user.email}, expires_delta=access_token_expires
        )

        # Create refresh token and store in MongoDB
        device_info = request.headers.get("User-Agent", "Unknown")
        ip_address = request.client.host if request.client else "Unknown"

        refresh_token = await create_refresh_token(
            user_email=user.email,
            device_info=device_info,
            ip_address=ip_address
        )

        # Check terms acceptance status
        from auth import config
        user_version = user_doc.get("terms_accepted_version")
        current_version = config.CURRENT_TERMS_VERSION
        needs_terms_acceptance = (user_version != current_version)

        # Add terms fields to user object
        user.terms_accepted_version = user_version
        user.terms_accepted_at = user_doc.get("terms_accepted_at")
        user.needs_terms_acceptance = needs_terms_acceptance

        # Fetch onboarding progress (single query, indexed)
        onboarding_progress = None
        if user.organization_id and user.role:
            onboarding_crud = get_onboarding_crud()
            progress = onboarding_crud.get_or_create_progress(
                user_id=str(user_doc["_id"]),
                organization_id=str(user.organization_id),
                role=user.role
            )
            if progress:
                onboarding_progress = serialize_doc(progress)

        # Convert user to dict and add onboarding_progress
        user_dict = {
            "id": str(user_doc["_id"]),
            "email": user.email,
            "firstName": user.firstName,
            "lastName": user.lastName,
            "organization_id": user.organization_id,
            "role": user.role,
            "status": user.status,
            "created_at": user_doc["createdAt"].isoformat() if user_doc.get("createdAt") else None,
            "terms_accepted_version": user.terms_accepted_version,
            "terms_accepted_at": user.terms_accepted_at.isoformat() if user.terms_accepted_at else None,
            "needs_terms_acceptance": user.needs_terms_acceptance,
            "onboarding_progress": onboarding_progress
        }

        # Return tokens in response body (not cookies)
        return {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "token_type": "bearer",
            "user": user_dict
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Login failed: {str(e)}"
        )


@router.post("/google/login")
async def google_login(
    google_request: GoogleLoginRequest,
    request: Request
):
    """
    Authenticate user with Google OAuth token and return JWT tokens

    Args:
        google_request: GoogleLoginRequest containing the Google JWT token
        request: FastAPI Request object for device info

    Returns:
        Dict with tokens and user info
    """
    try:
        # Initialize Google Auth service
        google_auth = GoogleAuthService()

        # Verify Google token and get user profile
        google_profile = google_auth.verify_google_token(google_request.credential)
        if not google_profile:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid Google token"
            )

        # Create or update user in database. Open self-registration: a brand-new Google
        # user gets their own organization (pre-verified by Google).
        user = UserCRUD.create_or_update_google_user(google_profile)

        # Get full user document to extract organization info
        from auth.database import get_mongodb_client
        users_collection = get_mongodb_client().get_users_collection()
        user_doc = users_collection.find_one({"email": user.email})

        # Extract role and organization from organizations array
        current_org_id = user_doc.get("current_organization_id")
        organizations = user_doc.get("organizations", [])

        if organizations and current_org_id:
            current_org = next(
                (org for org in organizations if org["organization_id"] == current_org_id),
                None
            )
            if current_org:
                user.organization_id = str(current_org["organization_id"])
                user.role = current_org["role"]
                user.status = current_org["status"]

        # Create access token
        access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
        access_token = create_access_token(
            data={"sub": user.email}, expires_delta=access_token_expires
        )

        # Create refresh token and store in MongoDB
        device_info = request.headers.get("User-Agent", "Unknown")
        ip_address = request.client.host if request.client else "Unknown"

        refresh_token = await create_refresh_token(
            user_email=user.email,
            device_info=device_info,
            ip_address=ip_address
        )

        # Check terms acceptance status
        from auth import config
        user_version = user_doc.get("terms_accepted_version")
        current_version = config.CURRENT_TERMS_VERSION
        needs_terms_acceptance = (user_version != current_version)

        # Add terms fields to user object
        user.terms_accepted_version = user_version
        user.terms_accepted_at = user_doc.get("terms_accepted_at")
        user.needs_terms_acceptance = needs_terms_acceptance

        # Fetch onboarding progress (single query, indexed)
        onboarding_progress = None
        if user.organization_id and user.role:
            onboarding_crud = get_onboarding_crud()
            progress = onboarding_crud.get_or_create_progress(
                user_id=str(user_doc["_id"]),
                organization_id=str(user.organization_id),
                role=user.role
            )
            if progress:
                onboarding_progress = serialize_doc(progress)

        # Convert user to dict and add onboarding_progress
        user_dict = {
            "id": str(user_doc["_id"]),
            "email": user.email,
            "firstName": user.firstName,
            "lastName": user.lastName,
            "organization_id": user.organization_id,
            "role": user.role,
            "status": user.status,
            "created_at": user_doc["createdAt"].isoformat() if user_doc.get("createdAt") else None,
            "terms_accepted_version": user.terms_accepted_version,
            "terms_accepted_at": user.terms_accepted_at.isoformat() if user.terms_accepted_at else None,
            "needs_terms_acceptance": user.needs_terms_acceptance,
            "onboarding_progress": onboarding_progress
        }

        # Return tokens in response body
        return {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "token_type": "bearer",
            "user": user_dict
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Google login failed: {str(e)}"
        )


@router.get("/me")
async def get_current_user_info(current_user: dict = Depends(get_current_user_from_deps)):
    """
    Get current authenticated user information including onboarding progress

    Returns:
        User information with current organization role and onboarding progress
    """
    # Fetch onboarding progress (single query, indexed)
    onboarding_progress = None
    if current_user.get("organization_id") and current_user.get("role"):
        onboarding_crud = get_onboarding_crud()
        progress = onboarding_crud.get_or_create_progress(
            user_id=str(current_user["_id"]),
            organization_id=str(current_user["organization_id"]),
            role=current_user["role"]
        )
        if progress:
            onboarding_progress = serialize_doc(progress)

    # Return user info with organization-specific fields and onboarding progress
    return {
        "id": str(current_user["_id"]),
        "email": current_user["email"],
        "firstName": current_user["firstName"],
        "lastName": current_user["lastName"],
        "organization_id": str(current_user.get("organization_id")) if current_user.get("organization_id") else None,
        "role": current_user.get("role"),
        "status": current_user.get("status"),
        "created_at": current_user["createdAt"].isoformat() if current_user.get("createdAt") else None,
        # Terms and conditions
        "terms_accepted_version": current_user.get("terms_accepted_version"),
        "terms_accepted_at": current_user.get("terms_accepted_at").isoformat() if current_user.get("terms_accepted_at") else None,
        "needs_terms_acceptance": current_user.get("needs_terms_acceptance", False),
        # Onboarding progress (included to avoid separate API call)
        "onboarding_progress": onboarding_progress
    }


@router.post("/refresh")
async def refresh_token_endpoint(
    request: Request,
    response: Response
):
    """
    Refresh access token using refresh token from request body.
    Implements refresh token rotation for security.

    Args:
        request: FastAPI Request object for device info and refresh token

    Returns:
        Dict with new tokens
    """
    # Get refresh token from request body
    body = await request.json()
    refresh_token = body.get("refresh_token")

    if not refresh_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token not found"
        )

    try:
        # Validate refresh token
        validation_result = await validate_refresh_token(refresh_token)

        if not validation_result.valid:
            # If token was reused (potential theft), revoke entire family
            if validation_result.reason == "token_reused":
                from auth.refresh_token import revoke_refresh_token_family
                await revoke_refresh_token_family(validation_result.token_family_id)
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Token reuse detected. All sessions invalidated."
                )

            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=validation_result.reason or "Invalid refresh token"
            )

        # Create new access token
        access_token = create_access_token(
            data={"sub": validation_result.user_email},
            expires_delta=timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
        )

        # Rotate refresh token (invalidate old, create new)
        device_info = request.headers.get("User-Agent", "Unknown")
        ip_address = request.client.host if request.client else "Unknown"

        new_refresh_token = await rotate_refresh_token(
            old_token=refresh_token,
            token_family_id=validation_result.token_family_id,
            device_info=device_info,
            ip_address=ip_address
        )

        # Return new tokens in response body
        return {
            "access_token": access_token,
            "refresh_token": new_refresh_token,
            "token_type": "bearer"
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Token refresh failed: {str(e)}"
        )


@router.post("/logout", response_model=LogoutResponse)
async def logout(
    request: Request,
    current_user: dict = Depends(get_current_user_from_deps)
):
    """
    Logout user by revoking refresh token

    Args:
        request: FastAPI Request object with refresh token in body
        current_user: Current authenticated user (for validation)

    Returns:
        LogoutResponse: Confirmation message with timestamp
    """
    try:
        # Get refresh token from request body
        body = await request.json()
        refresh_token = body.get("refresh_token")

        # Revoke refresh token if present
        if refresh_token:
            await revoke_refresh_token(refresh_token)

        return LogoutResponse(
            message="Successfully logged out",
            timestamp=datetime.utcnow()
        )

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Logout failed: {str(e)}"
        )


@router.post("/verify-email")
async def verify_email(verify_data: VerifyEmailRequest, request: Request):
    """
    Verify email address using magic link token (public endpoint)

    Args:
        verify_data: Verification token from email link
        request: FastAPI Request object for device info

    Returns:
        Dict with authentication tokens and user info
    """
    verification_crud = get_email_verification_crud()

    try:
        # Validate token
        verification = verification_crud.validate_token(verify_data.token)

        # Get user (user_id is a UUID string)
        from auth.database import get_mongodb_client
        users_collection = get_mongodb_client().get_users_collection()
        user = users_collection.find_one({"_id": verification["user_id"]})

        if not user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found"
            )

        # Mark as verified
        verification_crud.mark_verified(verify_data.token, verification["user_id"])

        # Get updated user document
        user = users_collection.find_one({"_id": verification["user_id"]})

        # Extract organization info
        current_org_id = user.get("current_organization_id")
        organizations = user.get("organizations", [])

        # Create user response object
        from auth.models import UserResponse
        user_response = UserResponse(
            id=str(user["_id"]),
            email=user["email"],
            firstName=user["firstName"],
            lastName=user["lastName"],
            organization_id=str(current_org_id) if current_org_id else None,
            role=None,
            status=None,
            createdAt=user["createdAt"]
        )

        if organizations and current_org_id:
            current_org = next(
                (org for org in organizations if org["organization_id"] == current_org_id),
                None
            )
            if current_org:
                user_response.organization_id = str(current_org["organization_id"])
                user_response.role = current_org["role"]
                user_response.status = current_org["status"]

        # Generate authentication tokens
        access_token = create_access_token(
            data={"sub": user["email"]},
            expires_delta=timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
        )

        device_info = request.headers.get("User-Agent", "Unknown")
        ip_address = request.client.host if request.client else "Unknown"

        refresh_token = await create_refresh_token(
            user_email=user["email"],
            device_info=device_info,
            ip_address=ip_address
        )

        return {
            "message": "Email verified successfully",
            "access_token": access_token,
            "refresh_token": refresh_token,
            "token_type": "bearer",
            "user": user_response
        }

    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Email verification failed: {str(e)}"
        )


@router.post("/resend-verification")
async def resend_verification(resend_data: ResendVerificationRequest):
    """
    Resend verification email to unverified user (public endpoint)

    Args:
        resend_data: Email address to resend verification to

    Returns:
        Success message
    """
    verification_crud = get_email_verification_crud()

    try:
        # Resend verification email
        verification_crud.resend_verification(resend_data.email)

        return {
            "message": "Verification email sent successfully. Please check your inbox.",
            "email": resend_data.email
        }

    except ValueError as e:
        # Don't reveal if user exists or not (security)
        if "not found" in str(e).lower():
            return {
                "message": "If an account exists with this email, a verification link has been sent.",
                "email": resend_data.email
            }
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to resend verification: {str(e)}"
        )


@router.post("/forgot-password")
async def forgot_password(request_data: ForgotPasswordRequest):
    """
    Request password reset email (public endpoint)

    Args:
        request_data: Email address to send reset link to

    Returns:
        Success message (same response whether user exists or not for security)
    """
    password_reset_crud = get_password_reset_crud()

    try:
        # Create reset request and send email
        password_reset_crud.create_reset_request(request_data.email)

        # Always return success to prevent user enumeration
        return {
            "message": "If an account exists with this email, a password reset link has been sent.",
            "email": request_data.email
        }

    except ValueError as e:
        # Rate limiting error - show to user
        if "Too many" in str(e):
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=str(e)
            )
        # Other errors - generic response for security
        return {
            "message": "If an account exists with this email, a password reset link has been sent.",
            "email": request_data.email
        }
    except Exception as e:
        # Log error but don't expose details
        print(f"Password reset request error: {e}")
        return {
            "message": "If an account exists with this email, a password reset link has been sent.",
            "email": request_data.email
        }


@router.post("/reset-password")
async def reset_password(reset_data: ResetPasswordRequest):
    """
    Reset password using token from email (public endpoint)

    Args:
        reset_data: Token and new password

    Returns:
        Success message
    """
    password_reset_crud = get_password_reset_crud()

    try:
        # Validate token and reset password
        password_reset_crud.reset_password(
            token=reset_data.token,
            new_password=reset_data.new_password
        )

        return {
            "message": "Password has been reset successfully. You can now login with your new password."
        }

    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Password reset failed: {str(e)}"
        )


@router.post("/change-password")
async def change_password(
    change_data: ChangePasswordRequest,
    current_user: dict = Depends(get_current_user_from_deps)
):
    """
    Change password for authenticated user (requires current password)

    Args:
        change_data: Current password and new password
        current_user: Current authenticated user

    Returns:
        Success message
    """
    import bcrypt
    from auth.database import get_mongodb_client

    try:
        # Validate new password strength
        if len(change_data.new_password) < 8:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="New password must be at least 8 characters long"
            )

        # Get user from database
        users_collection = get_mongodb_client().get_users_collection()
        user = users_collection.find_one({"_id": current_user["_id"]})

        if not user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found"
            )

        # Verify current password
        if not bcrypt.checkpw(
            change_data.current_password.encode('utf-8'),
            user["password"].encode('utf-8')
        ):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Current password is incorrect"
            )

        # Check if new password is same as current
        if change_data.current_password == change_data.new_password:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="New password must be different from current password"
            )

        # Hash new password
        hashed_password = bcrypt.hashpw(
            change_data.new_password.encode('utf-8'),
            bcrypt.gensalt()
        )

        # Update password
        result = users_collection.update_one(
            {"_id": current_user["_id"]},
            {
                "$set": {
                    "password": hashed_password.decode('utf-8'),
                    "updatedAt": datetime.utcnow()
                }
            }
        )

        if result.modified_count == 0:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to update password"
            )

        return {
            "message": "Password changed successfully"
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Password change failed: {str(e)}"
        )


@router.put("/profile")
async def update_profile(
    profile_data: UpdateProfileRequest,
    current_user: dict = Depends(get_current_user_from_deps)
):
    """
    Update user profile name (requires authentication)

    Args:
        profile_data: Updated name
        current_user: Current authenticated user

    Returns:
        Updated user info
    """
    from auth.database import get_mongodb_client

    try:
        # Validate name
        name = profile_data.name.strip()
        if not name:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Name cannot be empty"
            )

        # Split name into first and last (simple split on space)
        name_parts = name.split(maxsplit=1)
        firstName = name_parts[0]
        lastName = name_parts[1] if len(name_parts) > 1 else ""

        # Update user in database
        users_collection = get_mongodb_client().get_users_collection()
        result = users_collection.update_one(
            {"_id": current_user["_id"]},
            {
                "$set": {
                    "firstName": firstName,
                    "lastName": lastName,
                    "updatedAt": datetime.utcnow()
                }
            }
        )

        if result.modified_count == 0:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to update profile"
            )

        # Return updated user info
        return {
            "message": "Profile updated successfully",
            "firstName": firstName,
            "lastName": lastName
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Profile update failed: {str(e)}"
        )
