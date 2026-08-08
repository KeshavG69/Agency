"""Organization member management (admin).

The team-roster side of the auth layer: an admin lists members, promotes/demotes
roles, and removes people. Mirrors PriceIQ's organizations.py patterns
(require_admin + can_manage_user) but trimmed to what Collecct needs — no rate
presets / proposals. A last-admin guard makes it impossible to orphan an org.
"""
from datetime import datetime

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from auth.database import get_mongodb_client
from auth.crud import get_user_crud
from auth.dependencies import get_current_user, require_admin
from auth.rbac import can_manage_user
from utils.helpers import serialize_doc, serialize_docs
from utils.organizations import get_organization_crud

router = APIRouter(prefix="/api/organizations", tags=["organizations"])


def _count_active_admins(org_id: ObjectId) -> int:
    """How many active admins this org currently has (for the last-admin guard)."""
    users = get_mongodb_client().get_users_collection()
    return users.count_documents({
        "organizations": {"$elemMatch": {
            "organization_id": org_id, "status": "active", "role": "admin",
        }}
    })


def _find_target(user_id: str) -> dict | None:
    """Resolve a target user by id, tolerating ObjectId or string-UUID _id."""
    user_crud = get_user_crud()
    try:
        return user_crud.collection.find_one({"_id": ObjectId(user_id)})
    except Exception:
        return user_crud.collection.find_one({"$or": [{"_id": user_id}, {"id": user_id}]})


def _membership(target: dict, org_id: ObjectId) -> dict | None:
    return next(
        (o for o in target.get("organizations", []) if o["organization_id"] == org_id),
        None,
    )


class OrgUpdateRequest(BaseModel):
    name: str | None = None
    uei: str | None = None  # SAM.gov Unique Entity ID (govcon identifier)
    # Capability focus areas ("DevSecOps, AI engineering, zero trust"). Entered comma-
    # separated by an admin and stored as a list. These do NOT filter ingestion — they are
    # given to the agents as the second half of the fit lens, so a matching opportunity is
    # ranked HIGHER rather than a non-matching one being dropped.
    keywords: str | list[str] | None = None


@router.get("/me")
async def get_my_organization(current_user: dict = Depends(get_current_user)):
    """Current user's organization details."""
    org = get_organization_crud().get_by_id(current_user["organization_id"])
    if not org:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Organization not found")
    return serialize_doc(org)


@router.patch("/me")
async def update_organization(body: OrgUpdateRequest, current_user: dict = Depends(require_admin)):
    """Update the organization's name and/or UEI (admin only)."""
    updates: dict = {}
    if body.name is not None:
        name = body.name.strip()
        if not name:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Name cannot be empty")
        updates["name"] = name
    if body.uei is not None:
        updates["uei"] = body.uei.strip().upper()
    if body.keywords is not None:
        # Accept the raw comma-separated string the form sends, or an already-split list.
        raw = body.keywords.split(",") if isinstance(body.keywords, str) else body.keywords
        seen: dict[str, None] = {}  # de-dupe case-insensitively, keep the admin's order/casing
        for k in raw:
            k = str(k).strip()
            if k and k.lower() not in {s.lower() for s in seen}:
                seen[k] = None
        updates["keywords"] = list(seen)
    if not updates:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Nothing to update")
    updates["updated_at"] = datetime.utcnow()
    db = get_mongodb_client().get_database()
    db["organizations"].update_one({"_id": current_user["organization_id"]}, {"$set": updates})
    # Changing the UEI invalidates the cached SAM.gov profile the agents read.
    if "uei" in updates:
        from utils.sam_gov import invalidate_entity
        invalidate_entity(updates["uei"])
    return serialize_doc(get_organization_crud().get_by_id(current_user["organization_id"]))


@router.post("/me/uei-lookup")
async def lookup_organization_uei(current_user: dict = Depends(require_admin)):
    """Fetch the org's SAM.gov entity details from its stored UEI and save them (admin only)."""
    org = get_organization_crud().get_by_id(current_user["organization_id"])
    uei = (org or {}).get("uei")
    if not uei:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="Set the UEI first, then look it up.")
    # This is the Save/refresh path — bust the Redis cache so we pull FRESH from
    # SAM.gov and re-cache it forever. Agents then read the (now fresh) cache.
    from utils.sam_gov import entity_cached, invalidate_entity
    invalidate_entity(uei)
    try:
        details = entity_cached(uei)
    except RuntimeError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    if not details:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail=f"No SAM.gov entity found for UEI {uei}.")
    db = get_mongodb_client().get_database()
    db["organizations"].update_one(
        {"_id": org["_id"]},
        {"$set": {"company_details": details, "updated_at": datetime.utcnow()}},
    )
    return details


@router.get("/me/members")
async def get_organization_members(current_user: dict = Depends(require_admin)):
    """List all members of the organization (admin only)."""
    members = get_organization_crud().get_members(current_user["organization_id"])
    for member in members:
        member.pop("password", None)
        member.pop("google_profile", None)
        member.pop("blacklisted_tokens", None)
    return serialize_docs(members)


@router.get("/me/stats")
async def get_organization_stats(current_user: dict = Depends(require_admin)):
    """Member + pending-invitation counts (admin only)."""
    db = get_mongodb_client().get_database()
    org_id = current_user["organization_id"]
    active_members = db["users"].count_documents({
        "organizations": {"$elemMatch": {"organization_id": org_id, "status": "active"}}
    })
    pending_invitations = db["invitations"].count_documents({
        "organization_id": org_id, "status": "pending",
    })
    return {
        "active_members": active_members,
        "active_admins": _count_active_admins(org_id),
        "pending_invitations": pending_invitations,
    }


@router.post("/members/{user_id}/promote")
async def promote_member_to_admin(user_id: str, current_user: dict = Depends(require_admin)):
    """Promote an active member to admin (admin only)."""
    target = _find_target(user_id)
    if not target:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    org_id = current_user["organization_id"]
    membership = _membership(target, org_id)
    if not membership:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="User is not a member of your organization")
    if membership["status"] != "active":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="User is not an active member")
    if membership["role"] == "admin":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="User is already an admin")

    get_user_crud().collection.update_one(
        {"_id": target["_id"], "organizations.organization_id": org_id},
        {"$set": {"organizations.$.role": "admin", "updatedAt": datetime.utcnow()}},
    )
    return {"success": True, "user_id": user_id, "new_role": "admin"}


@router.post("/members/{user_id}/demote")
async def demote_member_to_user(user_id: str, current_user: dict = Depends(require_admin)):
    """Demote an admin back to a regular member (admin only).

    Blocked if the target is the org's only remaining admin (would orphan the org),
    and you cannot demote yourself.
    """
    current_user_id = str(current_user.get("_id"))
    if current_user_id == user_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="You cannot demote yourself")

    target = _find_target(user_id)
    if not target:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    org_id = current_user["organization_id"]
    membership = _membership(target, org_id)
    if not membership:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="User is not a member of your organization")
    if membership["role"] != "admin":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="User is not an admin")
    if _count_active_admins(org_id) <= 1:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="Cannot demote the last admin of the organization")

    get_user_crud().collection.update_one(
        {"_id": target["_id"], "organizations.organization_id": org_id},
        {"$set": {"organizations.$.role": "user", "updatedAt": datetime.utcnow()}},
    )
    return {"success": True, "user_id": user_id, "new_role": "user"}


@router.delete("/members/{user_id}")
async def remove_organization_member(user_id: str, current_user: dict = Depends(require_admin)):
    """Remove a member from the organization (admin only, soft delete).

    Cannot remove yourself, someone outside your org, or the last remaining admin.
    """
    current_user_id = str(current_user.get("_id"))
    if current_user_id == user_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="Cannot remove yourself from the organization")

    target = _find_target(user_id)
    if not target:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    org_id = current_user["organization_id"]
    membership = _membership(target, org_id)
    if not membership:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="User does not belong to your organization")
    if not can_manage_user(target, current_user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="You do not have permission to remove this user")
    if membership.get("role") == "admin" and _count_active_admins(org_id) <= 1:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="Cannot remove the last admin of the organization")

    get_user_crud().collection.update_one(
        {"_id": target["_id"], "organizations.organization_id": org_id},
        {"$set": {"organizations.$.status": "removed", "updatedAt": datetime.utcnow()}},
    )
    return {"message": "User removed successfully from organization", "user_id": user_id}
