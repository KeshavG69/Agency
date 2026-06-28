"""
Authentication dependencies for FastAPI endpoints.
Provides JWT token validation and role-based access control.
"""

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from bson import ObjectId
from jose import jwt, JWTError
from typing import Optional
from auth import config
from auth.database import get_mongodb_client


security = HTTPBearer()


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security)
) -> dict:
    """
    Get current user from JWT token.

    Validates JWT token and returns user dict with organization info.
    Optimized: Single DB query for user + blacklist check.

    Args:
        credentials: HTTP Bearer token from Authorization header

    Returns:
        User document dict with _id, email, organization_id, role, status

    Raises:
        HTTPException 401: If token is invalid, expired, or user not found
        HTTPException 403: If account is suspended
    """
    token = credentials.credentials

    try:
        # Decode JWT token
        payload = jwt.decode(token, config.SECRET_KEY, algorithms=[config.ALGORITHM])
        email: str = payload.get("sub")

        if not email:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token: missing email"
            )

        # Single DB query: Get user (includes blacklisted_tokens if any)
        users_collection = get_mongodb_client().get_users_collection()
        user = users_collection.find_one({"email": email})

        if not user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="User not found"
            )

        # Check if user account is deleted
        if user.get("status") == "deleted":
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Account has been deleted"
            )

        # Check blacklist in-memory (no extra DB call)
        blacklisted_tokens = user.get("blacklisted_tokens", [])
        for bt in blacklisted_tokens:
            if bt.get("token") == token:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Token has been revoked"
                )

        # Get current organization membership from organizations array
        current_org_id = user.get("current_organization_id")
        organizations = user.get("organizations", [])

        # Find current organization membership
        current_org = next(
            (org for org in organizations if org["organization_id"] == current_org_id),
            None
        )

        # If current org is not active, try to switch to another active org
        if not current_org or current_org.get("status") != "active":
            # Find first active organization
            active_org = next(
                (org for org in organizations if org.get("status") == "active"),
                None
            )

            if active_org:
                # Switch to active organization
                current_org = active_org
                # Update current_organization_id in database
                users_collection.update_one(
                    {"_id": user["_id"]},
                    {"$set": {"current_organization_id": active_org["organization_id"]}}
                )
                user["current_organization_id"] = active_org["organization_id"]
            else:
                # No active organizations, account is suspended
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Account suspended or removed from all organizations"
                )

        # Add flat fields for easy access in endpoints
        user["organization_id"] = current_org["organization_id"]
        user["role"] = current_org["role"]
        user["status"] = current_org["status"]

        # Check terms and conditions version
        user_version = user.get("terms_accepted_version")
        current_version = config.CURRENT_TERMS_VERSION

        # Add flag to indicate if user needs to accept updated terms
        if user_version != current_version:
            user["needs_terms_acceptance"] = True
        else:
            user["needs_terms_acceptance"] = False

        return user

    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has expired"
        )
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token"
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Authentication failed: {str(e)}"
        )


async def require_admin(current_user: dict = Depends(get_current_user)) -> dict:
    """
    Require admin role for endpoint access.

    Args:
        current_user: User dict from get_current_user dependency

    Returns:
        User dict if user has admin role

    Raises:
        HTTPException 403: If user does not have admin role
    """
    if current_user.get("role") != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin privileges required"
        )

    return current_user


async def get_current_user_id(
    current_user: dict = Depends(get_current_user)
) -> ObjectId:
    """
    Get current user's ObjectId.

    Convenience dependency for endpoints that only need user_id.

    Args:
        current_user: User dict from get_current_user dependency

    Returns:
        User's MongoDB ObjectId
    """
    return current_user["_id"]


async def get_current_organization_id(
    current_user: dict = Depends(get_current_user)
) -> ObjectId:
    """
    Get current user's organization ObjectId.

    Convenience dependency for endpoints that only need organization_id.

    Args:
        current_user: User dict from get_current_user dependency

    Returns:
        Organization's MongoDB ObjectId

    Raises:
        HTTPException 403: If user does not belong to an organization
    """
    org_id = current_user.get("organization_id")
    if not org_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User does not belong to an organization"
        )

    return org_id
