"""
User account management endpoints.

Includes account deletion with organization admin continuity checks.
"""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from datetime import datetime
from bson import ObjectId
from typing import List, Dict, Any
from auth.dependencies import get_current_user
from auth.database import get_mongodb_client
from auth.crud import get_user_crud
from utils.organizations import get_organization_crud

router = APIRouter()


@router.get("/me/deletion-check")
async def check_deletion_eligibility(
    current_user: dict = Depends(get_current_user)
):
    """
    Check if user can delete account or needs to handle blocking orgs.

    A user is blocked from deletion if they are the last admin in any organization
    that has other members. Organizations where the user is the sole member will be
    automatically deleted.

    Returns:
    {
        "can_delete": bool,
        "blocking_organizations": [
            {
                "id": str,
                "name": str,
                "role": "admin",
                "is_last_admin": bool,
                "member_count": int,
                "can_promote_members": [
                    {"id": str, "name": str, "email": str, "role": "user"}
                ]
            }
        ],
        "organizations_to_delete": [
            {"id": str, "name": str, "role": str}
        ],
        "other_organizations": [
            {"id": str, "name": str, "role": str}
        ]
    }
    """
    db = get_mongodb_client().get_database()
    users_collection = db["users"]
    orgs_collection = db["organizations"]

    # Get current user's organizations
    user = users_collection.find_one({"_id": current_user["_id"]})
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    user_orgs = user.get("organizations", [])

    blocking_organizations = []
    organizations_to_delete = []
    other_organizations = []

    for user_org in user_orgs:
        org_id = user_org["organization_id"]
        role = user_org["role"]
        org_status = user_org.get("status", "active")

        # Skip inactive organizations
        if org_status != "active":
            continue

        # Get organization details
        org = orgs_collection.find_one({"_id": org_id})
        if not org or org.get("status") != "active":
            continue

        # Get total member count for this organization
        member_count = users_collection.count_documents({
            "organizations": {
                "$elemMatch": {
                    "organization_id": org_id,
                    "status": "active"
                }
            },
            "status": "active"
        })

        # If user is the only member, org will be deleted
        if member_count == 1:
            organizations_to_delete.append({
                "id": str(org_id),
                "name": org.get("name", "Unknown"),
                "role": role
            })
            continue

        # If user is admin, check if they're the last admin
        if role == "admin":
            # Count total admins in this organization
            admin_count = users_collection.count_documents({
                "organizations": {
                    "$elemMatch": {
                        "organization_id": org_id,
                        "role": "admin",
                        "status": "active"
                    }
                },
                "status": "active"
            })

            if admin_count == 1:
                # User is the last admin with other members - this is a blocking org
                # Get list of members who can be promoted
                promotable_members = []
                members = users_collection.find({
                    "organizations": {
                        "$elemMatch": {
                            "organization_id": org_id,
                            "role": "user",
                            "status": "active"
                        }
                    },
                    "status": "active",
                    "_id": {"$ne": current_user["_id"]}  # Exclude current user
                })

                for member in members:
                    promotable_members.append({
                        "id": str(member["_id"]),
                        "name": f"{member.get('firstName', '')} {member.get('lastName', '')}".strip(),
                        "email": member.get("email", ""),
                        "role": "user"
                    })

                blocking_organizations.append({
                    "id": str(org_id),
                    "name": org.get("name", "Unknown"),
                    "role": role,
                    "is_last_admin": True,
                    "member_count": member_count,
                    "can_promote_members": promotable_members
                })
            else:
                # There are other admins, user can leave safely
                other_organizations.append({
                    "id": str(org_id),
                    "name": org.get("name", "Unknown"),
                    "role": role
                })
        else:
            # User is not admin, they can leave safely
            other_organizations.append({
                "id": str(org_id),
                "name": org.get("name", "Unknown"),
                "role": role
            })

    can_delete = len(blocking_organizations) == 0

    return {
        "can_delete": can_delete,
        "blocking_organizations": blocking_organizations,
        "organizations_to_delete": organizations_to_delete,
        "other_organizations": other_organizations
    }


@router.delete("/me")
async def delete_my_account(
    confirm: bool = Query(..., description="Must be true to confirm deletion"),
    current_user: dict = Depends(get_current_user)
):
    """
    Delete user account after resolving blocking organizations.

    Checks:
    - No blocking organizations (last admin check)
    - Confirmation flag is true

    Actions:
    - Delete organizations where user is sole member (hard delete)
    - Delete all proposals belonging to those organizations (hard delete)
    - Anonymize user data (soft delete):
      - email → "deleted_user_{user_id}@deleted.local"
      - firstName → "Deleted"
      - lastName → "User"
      - password → null
      - status → "deleted" (this invalidates all JWT tokens automatically)
    - Keep user_id in proposals for other orgs (for audit trail)
    - Keep billing records unchanged

    Returns:
    {
        "success": true,
        "message": "Account deleted successfully",
        "organizations_deleted": int
    }
    """
    if not confirm:
        raise HTTPException(
            status_code=400,
            detail="Confirmation required. Set confirm=true query parameter."
        )

    db = get_mongodb_client().get_database()
    users_collection = db["users"]
    orgs_collection = db["organizations"]
    proposals_collection = db["proposals"]

    user_id = str(current_user["_id"])

    # Run pre-deletion check
    deletion_check = await check_deletion_eligibility(current_user)
    if not deletion_check["can_delete"]:
        blocking_count = len(deletion_check["blocking_organizations"])
        raise HTTPException(
            status_code=400,
            detail=f"Cannot delete account. You are the last admin in {blocking_count} organization(s). "
                   f"Please promote another member to admin or contact support."
        )

    # Get current user data
    user = users_collection.find_one({"_id": current_user["_id"]})
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    original_email = user.get("email")
    original_name = f"{user.get('firstName', '')} {user.get('lastName', '')}".strip()

    now = datetime.utcnow()

    # Delete organizations where user is sole member
    organizations_to_delete = deletion_check.get("organizations_to_delete", [])
    org_ids_to_delete = [ObjectId(org["id"]) for org in organizations_to_delete]

    if org_ids_to_delete:
        # Delete all proposals belonging to these organizations
        proposals_deleted = proposals_collection.delete_many({
            "organization_id": {"$in": org_ids_to_delete}
        })

        # Delete the organizations
        orgs_deleted = orgs_collection.delete_many({
            "_id": {"$in": org_ids_to_delete}
        })
    else:
        orgs_deleted = type('obj', (object,), {'deleted_count': 0})()

    # Anonymize user document
    # Note: Setting status="deleted" automatically invalidates all JWT tokens
    # via the check in auth/dependencies.py get_current_user()
    users_collection.update_one(
        {"_id": current_user["_id"]},
        {
            "$set": {
                "email": f"deleted_user_{user_id}@deleted.local",
                "firstName": "Deleted",
                "lastName": "User",
                "password": None,
                "status": "deleted",
                "deleted_at": now,
                "updatedAt": now
            }
        }
    )

    # Keep proposals in other orgs but don't modify them (user_id is preserved for audit)
    # Proposals will show "Deleted User" in the UI by checking user status

    return {
        "success": True,
        "message": "Account deleted successfully",
        "organizations_deleted": orgs_deleted.deleted_count
    }
