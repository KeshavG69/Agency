"""
Role-Based Access Control (RBAC) utilities.
Provides functions to check permissions for organization resources.
"""

from bson import ObjectId
from typing import Optional


def can_access_proposal(proposal: dict, user: dict) -> bool:
    """
    Check if user can access a proposal.

    Access rules:
    1. Must be same organization
    2. Admin can access all org proposals
    3. Owner can access their proposals
    4. User can access explicitly shared proposals

    Args:
        proposal: Proposal document dict
        user: User document dict

    Returns:
        True if user can access proposal, False otherwise
    """
    # Must be same organization
    if proposal.get("organization_id") != user.get("organization_id"):
        return False

    # Admin can access all org proposals
    if user.get("role") == "admin":
        return True

    # Get user ID (handle both ObjectId and string formats)
    user_id = str(user.get("_id")) if user.get("_id") else user.get("id")
    proposal_owner_id = str(proposal.get("user_id"))

    # Owner can access their proposals
    if proposal_owner_id == user_id:
        return True

    # Check if explicitly shared with user
    shared_with = proposal.get("shared_with", [])
    # Normalize all IDs to strings for comparison
    shared_with_str = [str(uid) for uid in shared_with]
    if user_id in shared_with_str:
        return True

    return False


def can_modify_proposal(proposal: dict, user: dict) -> bool:
    """
    Check if user can modify/delete a proposal.

    Modification rules:
    1. Must be same organization
    2. Admin can modify all org proposals
    3. Owner can modify their proposals
    4. Shared users CAN modify (full access)

    Args:
        proposal: Proposal document dict
        user: User document dict

    Returns:
        True if user can modify proposal, False otherwise
    """
    # Must be same organization
    if proposal.get("organization_id") != user.get("organization_id"):
        return False

    # Admin can modify all org proposals
    if user.get("role") == "admin":
        return True

    # Get user ID (handle both ObjectId and string formats)
    user_id = str(user.get("_id")) if user.get("_id") else user.get("id")
    proposal_owner_id = str(proposal.get("user_id"))

    # Owner can modify their proposals
    if proposal_owner_id == user_id:
        return True

    # Shared users can modify (full access)
    shared_with = proposal.get("shared_with", [])
    shared_with_str = [str(uid) for uid in shared_with]
    if user_id in shared_with_str:
        return True

    return False


def require_same_org(resource_org_id: ObjectId, user_org_id: ObjectId):
    """
    Verify resource belongs to user's organization.

    Args:
        resource_org_id: Resource's organization ObjectId
        user_org_id: User's organization ObjectId

    Raises:
        ValueError: If resource belongs to different organization
    """
    if resource_org_id != user_org_id:
        raise ValueError("Resource belongs to different organization")


def can_manage_user(target_user: dict, current_user: dict) -> bool:
    """
    Check if current user can manage target user (remove, change role, etc.).

    Management rules:
    1. Must be admin
    2. Must be same organization
    3. Cannot manage yourself (prevent self-removal)

    Args:
        target_user: Target user document dict (raw from DB, with organizations array)
        current_user: Current user document dict (from get_current_user, with flat organization_id)

    Returns:
        True if current user can manage target user, False otherwise
    """
    # Must be admin
    if current_user.get("role") != "admin":
        return False

    # Must be same organization - check if target user has membership in current user's org
    current_org_id = current_user.get("organization_id")
    target_user_orgs = target_user.get("organizations", [])
    has_membership = any(
        org["organization_id"] == current_org_id
        for org in target_user_orgs
    )

    if not has_membership:
        return False

    # Cannot manage yourself (handle both ObjectId and string ID formats)
    target_id = str(target_user.get("_id")) if target_user.get("_id") else target_user.get("id")
    current_id = str(current_user.get("_id")) if current_user.get("_id") else current_user.get("id")
    if target_id == current_id:
        return False

    return True


def can_invite_user(inviter: dict, max_seats: Optional[int] = None) -> bool:
    """
    Check if user can invite new members.

    Invitation rules:
    1. Must be admin
    2. If max_seats specified, check if organization is at capacity

    Args:
        inviter: Inviter user document dict
        max_seats: Optional maximum seats allowed for organization

    Returns:
        True if user can invite, False otherwise
    """
    # Must be admin
    if inviter.get("role") != "admin":
        return False

    # Check seat limit if specified
    if max_seats is not None:
        # This would require counting active users in the organization
        # Implementation depends on subscription management
        pass

    return True


def is_organization_owner(user: dict, organization: dict) -> bool:
    """
    Check if user is the owner of the organization.

    Args:
        user: User document dict
        organization: Organization document dict

    Returns:
        True if user is organization owner, False otherwise
    """
    return user.get("_id") == organization.get("owner_id")


def can_modify_organization_settings(user: dict, organization: dict) -> bool:
    """
    Check if user can modify organization settings.

    Settings modification rules:
    1. Must be admin of the organization
    2. Organization must match user's organization

    Args:
        user: User document dict
        organization: Organization document dict

    Returns:
        True if user can modify settings, False otherwise
    """
    # Must be same organization
    if user.get("organization_id") != organization.get("_id"):
        return False

    # Must be admin
    if user.get("role") != "admin":
        return False

    return True
