"""
Workspace/Organization switching router.
Allows users to switch between multiple organizations.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from bson import ObjectId
from pydantic import BaseModel
from auth.dependencies import get_current_user
from auth.database import get_mongodb_client
from utils.helpers import serialize_docs

router = APIRouter(prefix="/api/workspace", tags=["workspace"])


class SwitchOrganizationRequest(BaseModel):
    """Request body for switching organization"""
    organization_id: str


@router.get("/organizations")
async def get_user_organizations(current_user: dict = Depends(get_current_user)):
    """
    Get all organizations the current user belongs to.
    
    Returns:
        List of organizations with user's role in each
    """
    users_collection = get_mongodb_client().get_users_collection()
    db = get_mongodb_client().get_database()
    orgs_collection = db["organizations"]

    # Get user's organizations
    user = users_collection.find_one({"_id": current_user["_id"]})
    organizations_data = user.get("organizations", [])
    
    # For backward compatibility
    if not organizations_data and user.get("organization_id"):
        organizations_data = [{
            "organization_id": user.get("organization_id"),
            "role": user.get("role", "user"),
            "status": user.get("status", "active")
        }]
    
    result = []
    for org_membership in organizations_data:
        # Only show active organizations
        if org_membership.get("status") != "active":
            continue

        org = orgs_collection.find_one({"_id": org_membership["organization_id"]})
        if org:
            result.append({
                "id": str(org["_id"]),
                "name": org["name"],
                "role": org_membership["role"],
                "status": org_membership["status"],
                "is_current": org["_id"] == user.get("current_organization_id")
            })

    return result


@router.post("/switch")
async def switch_organization(
    switch_data: SwitchOrganizationRequest,
    current_user: dict = Depends(get_current_user)
):
    """
    Switch to a different organization.
    
    Args:
        switch_data: Organization ID to switch to
        
    Returns:
        Success message and updated user data
        
    Raises:
        HTTPException 400: If organization ID is invalid
        HTTPException 403: If user is not a member of the organization
    """
    users_collection = get_mongodb_client().get_users_collection()

    # Validate organization ID
    try:
        org_id = ObjectId(switch_data.organization_id)
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid organization ID"
        )

    # Get user's organizations
    user = users_collection.find_one({"_id": current_user["_id"]})
    organizations = user.get("organizations", [])
    
    # Check if user is a member of this organization
    is_member = any(
        org["organization_id"] == org_id and org["status"] == "active"
        for org in organizations
    )
    
    if not is_member:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You are not a member of this organization"
        )
    
    # Update current organization
    users_collection.update_one(
        {"_id": current_user["_id"]},
        {"$set": {"current_organization_id": org_id}}
    )
    
    return {
        "message": "Organization switched successfully",
        "organization_id": str(org_id)
    }
