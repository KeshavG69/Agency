"""
Invitation management router.
Handles email invitations with token hashing for organization members.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from bson import ObjectId
from pydantic import BaseModel, EmailStr, Field
from typing import List
from auth.dependencies import get_current_user, require_admin
from utils.helpers import serialize_doc, serialize_docs
from utils.invitations import get_invitation_crud
from auth.crud import get_user_crud, UserCRUD
from auth.models import UserSignup
from auth.utils import create_access_token
from auth.refresh_token import create_refresh_token
from datetime import timedelta
from datetime import datetime as dt


router = APIRouter(prefix="/api/invitations", tags=["invitations"])


class InviteUserRequest(BaseModel):
    """Request body for sending invitation"""
    email: EmailStr = Field(..., description="Email address to invite")
    role: str = Field(..., description="Role for new user (admin or user)")
    proposal_ids: List[str] = Field(default=[], description="Proposal IDs to grant access to upon acceptance")

    class Config:
        schema_extra = {
            "example": {
                "email": "colleague@example.com",
                "role": "user",
                "proposal_ids": []
            }
        }


class AcceptInvitationRequest(BaseModel):
    """Request body for accepting invitation"""
    token: str = Field(..., description="Invitation token from email")
    firstName: str = Field(None, min_length=1, max_length=100, description="Required for new users only")
    lastName: str = Field(None, min_length=1, max_length=100, description="Required for new users only")
    password: str = Field(None, min_length=8, max_length=100, description="Required for new users only")
    terms_accepted: bool = Field(None, description="Required for new users only")

    class Config:
        schema_extra = {
            "example": {
                "token": "abc123...",
                "firstName": "John",
                "lastName": "Doe",
                "password": "SecurePassword123!",
                "terms_accepted": True
            }
        }


@router.post("")
async def send_invitation(
    invite_data: InviteUserRequest,
    current_user: dict = Depends(require_admin)
):
    """
    Send an invitation to join the organization (admin only).

    Creates invitation with hashed token and sends email.
    Invitation expires in 7 days.

    Args:
        invite_data: Email and role for new user

    Returns:
        Created invitation document (without plain token)

    Raises:
        HTTPException 400: If user already exists or has pending invitation
        HTTPException 403: If not admin
    """
    # Validate role
    if invite_data.role not in ["admin", "user"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Role must be 'admin' or 'user'"
        )

    invitation_crud = get_invitation_crud()

    try:
        invitation = invitation_crud.create_invitation(
            org_id=current_user["organization_id"],
            email=invite_data.email,
            role=invite_data.role,
            invited_by=current_user["_id"],
            proposal_ids=invite_data.proposal_ids
        )

        # Remove token_hash from response (security)
        invitation_response = serialize_doc(invitation)
        invitation_response.pop("token_hash", None)

        # Auto-completion hook: Mark team invited
        try:
            from utils.onboarding import get_onboarding_crud
            onboarding_crud = get_onboarding_crud()
            onboarding_crud.update_task(
                user_id=str(current_user["_id"]),
                organization_id=str(current_user["organization_id"]),
                task_id="team_invited",
                completed=True
            )
        except Exception as e:
            # Don't fail request if onboarding update fails
            print(f"Failed to update onboarding progress: {e}")

        return {
            "message": "Invitation sent successfully",
            "invitation": invitation_response
        }

    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to send invitation: {str(e)}"
        )


@router.get("")
async def list_invitations(
    status: str = None,
    current_user: dict = Depends(require_admin)
):
    """
    Get invitations for the organization (admin only).

    Args:
        status: Optional filter by status (pending, accepted, expired, revoked)

    Returns:
        List of invitation documents sorted by creation date (newest first).
        Excludes token hashes for security.
    """
    invitation_crud = get_invitation_crud()

    # Get all invitations for the organization
    from auth.database import get_mongodb_client
    db = get_mongodb_client().get_database()

    query = {"organization_id": current_user["organization_id"]}
    if status:
        query["status"] = status

    invitations = list(db["invitations"].find(query).sort("created_at", -1))

    # Remove token_hash and convert to camelCase
    for invitation in invitations:
        invitation.pop("token_hash", None)
        # Convert snake_case to camelCase for consistency
        if "created_at" in invitation:
            invitation["createdAt"] = invitation.pop("created_at")
        if "expires_at" in invitation:
            invitation["expiresAt"] = invitation.pop("expires_at")
        if "accepted_at" in invitation:
            invitation["acceptedAt"] = invitation.pop("accepted_at")

    return serialize_docs(invitations)


@router.get("/stats")
async def get_invitation_stats(current_user: dict = Depends(require_admin)):
    """
    Get invitation statistics for the organization (admin only).

    Returns:
        Counts of invitations by status
    """
    from auth.database import get_mongodb_client
    db = get_mongodb_client().get_database()

    org_id = current_user["organization_id"]

    # Get counts for each status
    pipeline = [
        {"$match": {"organization_id": org_id}},
        {"$group": {
            "_id": "$status",
            "count": {"$sum": 1}
        }}
    ]

    results = list(db["invitations"].aggregate(pipeline))

    # Convert to dict
    stats = {
        "pending": 0,
        "accepted": 0,
        "expired": 0,
        "revoked": 0,
        "total": 0
    }

    for result in results:
        status = result["_id"]
        count = result["count"]
        if status in stats:
            stats[status] = count
        stats["total"] += count

    return stats


@router.delete("/{invitation_id}")
async def revoke_invitation(
    invitation_id: str,
    current_user: dict = Depends(require_admin)
):
    """
    Revoke/cancel a pending invitation (admin only).

    Args:
        invitation_id: Invitation's ObjectId as string

    Returns:
        Success message

    Raises:
        HTTPException 400: If invalid invitation ID
        HTTPException 404: If invitation not found or already accepted
    """
    # Validate ObjectId
    try:
        inv_oid = ObjectId(invitation_id)
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid invitation ID format"
        )

    invitation_crud = get_invitation_crud()
    success = invitation_crud.revoke_invitation(inv_oid, current_user["organization_id"])

    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Invitation not found or already processed"
        )

    return {
        "message": "Invitation revoked successfully",
        "invitation_id": invitation_id
    }


@router.post("/{invitation_id}/resend")
async def resend_invitation(
    invitation_id: str,
    current_user: dict = Depends(require_admin)
):
    """
    Resend a pending invitation (admin only) — mints a fresh token, resets the 7-day
    expiry, and re-sends the email. Lets an admin retry a stuck, near-expiry, or
    lost/never-received invite without revoking it and creating a new one from scratch.

    Args:
        invitation_id: Invitation's ObjectId as string

    Returns:
        The updated invitation document (without the token hash)

    Raises:
        HTTPException 400: If invalid invitation ID
        HTTPException 404: If invitation not found or no longer pending
    """
    try:
        inv_oid = ObjectId(invitation_id)
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid invitation ID format"
        )

    invitation_crud = get_invitation_crud()
    try:
        invitation = invitation_crud.resend_invitation(inv_oid, current_user["organization_id"])
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))

    invitation_response = serialize_doc(invitation)
    invitation_response.pop("token_hash", None)
    return {"message": "Invitation resent successfully", "invitation": invitation_response}


@router.get("/validate/{token}")
async def validate_invitation_token(token: str):
    """
    Validate invitation token (public endpoint).

    Checks if token is valid, not expired, and not already used.
    Does not require authentication.

    Args:
        token: Invitation token from email URL

    Returns:
        Invitation details (email, organization name, role, inviter, user_exists flag)

    Raises:
        HTTPException 400: If token is invalid, expired, or already used
    """
    invitation_crud = get_invitation_crud()
    user_crud = get_user_crud()

    try:
        invitation = invitation_crud.validate_token(token)

        # Check if user already exists
        existing_user = user_crud.collection.find_one({"email": invitation["email"]})
        user_exists = existing_user is not None

        # Return safe invitation details (no token_hash, no ObjectIds)
        # Use camelCase for consistency with other API responses
        expires_at = invitation["expires_at"]
        created_at = invitation["created_at"]

        return {
            "email": invitation["email"],
            "organization_name": invitation["organization_name"],
            "role": invitation["role"],
            "invited_by_name": invitation["invited_by_name"],
            "expiresAt": expires_at.isoformat() + 'Z' if expires_at.tzinfo is None else expires_at.isoformat(),
            "createdAt": created_at.isoformat() + 'Z' if created_at.tzinfo is None else created_at.isoformat(),
            "user_exists": user_exists
        }

    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )


@router.post("/accept")
async def accept_invitation(accept_data: AcceptInvitationRequest):
    """
    Accept invitation and join organization (public endpoint).

    For new users: Creates account with firstName, lastName, password.
    For existing users: Updates their organization and role.

    Args:
        accept_data: Token (required), firstName/lastName/password (required for new users only)

    Returns:
        User document and authentication tokens

    Raises:
        HTTPException 400: If token invalid or missing required fields for new users
    """
    invitation_crud = get_invitation_crud()
    user_crud = get_user_crud()

    try:
        # Validate token
        invitation = invitation_crud.validate_token(accept_data.token)

        # Check if user already exists
        existing_user = user_crud.collection.find_one({"email": invitation["email"]})

        if existing_user:
            # Existing user - add to organizations array (multi-org support)
            # Check if already in organizations array
            organizations = existing_user.get("organizations", [])
            existing_org_entry = None
            for org in organizations:
                if org["organization_id"] == invitation["organization_id"]:
                    existing_org_entry = org
                    break

            if existing_org_entry:
                if existing_org_entry.get("status") != "removed":
                    # Already an active member
                    raise ValueError("You are already a member of this organization")
                else:
                    # Was removed - reactivate membership
                    user_crud.collection.update_one(
                        {
                            "_id": existing_user["_id"],
                            "organizations.organization_id": invitation["organization_id"]
                        },
                        {
                            "$set": {
                                "organizations.$.status": "active",
                                "organizations.$.role": invitation["role"],
                                "organizations.$.joinedAt": dt.utcnow(),
                                "current_organization_id": invitation["organization_id"],
                                "updatedAt": dt.utcnow()
                            }
                        }
                    )
            else:
                # New organization membership - add to array
                new_org = {
                    "organization_id": invitation["organization_id"],
                    "role": invitation["role"],
                    "status": "active",
                    "joinedAt": dt.utcnow()
                }
                user_crud.collection.update_one(
                    {"_id": existing_user["_id"]},
                    {
                        "$push": {"organizations": new_org},
                        "$set": {
                            "current_organization_id": invitation["organization_id"],
                            "updatedAt": dt.utcnow()
                        }
                    }
                )
            user = user_crud.collection.find_one({"_id": existing_user["_id"]})
            user_id = existing_user["_id"]
        else:
            # New user - validate required fields
            if not accept_data.firstName or not accept_data.lastName or not accept_data.password:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="firstName, lastName, and password are required for new users"
                )

            if accept_data.terms_accepted is None or not accept_data.terms_accepted:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="You must accept the terms and conditions to create an account"
                )

            # Create the user as a member of ONLY the invited organization (no personal
            # org). This keeps their role bound to the invite — they can't escalate by
            # switching to an org where they happen to be admin.
            user = user_crud.create_user_with_organization(
                email=invitation["email"],
                first_name=accept_data.firstName,
                last_name=accept_data.lastName,
                password=accept_data.password,
                organization_id=invitation["organization_id"],
                role=invitation["role"],
            )
            user_id = user["_id"]

        # Mark invitation as accepted
        invitation_crud.accept_invitation(accept_data.token, user_id)

        # Grant access to proposals specified in invitation
        if invitation.get("proposal_ids"):
            from auth.database import get_mongodb_client
            mongodb = get_mongodb_client()
            proposals_collection = mongodb.get_database()["proposals"]

            for proposal_id in invitation["proposal_ids"]:
                try:
                    prop_oid = ObjectId(proposal_id)
                    proposals_collection.update_one(
                        {"_id": prop_oid, "organization_id": invitation["organization_id"]},
                        {"$addToSet": {"shared_with": str(user_id)}}
                    )
                except Exception as e:
                    print(f"Warning: Could not grant access to proposal {proposal_id}: {e}")

        # Generate authentication tokens
        access_token = create_access_token(
            data={"sub": user["email"]},
            expires_delta=timedelta(minutes=30)
        )

        refresh_token = await create_refresh_token(
            user_email=user["email"],
            device_info="Invitation acceptance",
            ip_address="Unknown"
        )

        # Remove sensitive fields from user response
        user_response = serialize_doc(user)
        user_response.pop("password", None)

        return {
            "message": "Invitation accepted successfully",
            "user": user_response,
            "access_token": access_token,
            "refresh_token": refresh_token,
            "token_type": "bearer"
        }

    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to accept invitation: {str(e)}"
        )
