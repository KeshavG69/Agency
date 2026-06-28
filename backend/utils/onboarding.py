"""
Onboarding Progress CRUD operations with MongoDB.

Tracks user onboarding progress including product tour state and checklist tasks.
"""

from datetime import datetime
from typing import Optional, Dict, Any, List
import threading
from auth.database import get_mongodb_client


# Task definitions with role requirements
# required_role: None = both admin and user, "admin" = admin only
ONBOARDING_TASKS = {
    "rates_configured": {
        "label": "Configure your rates",
        "description": "Set your organization's Fringe, OH, G&A, and Fee rates",
        "required_role": "admin",
        "order": 1
    },
    "payment_added": {
        "label": "Add payment method",
        "description": "Add a credit card to enable proposal generation",
        "required_role": "admin",
        "order": 2
    },
    "team_invited": {
        "label": "Invite team members",
        "description": "Collaborate by inviting colleagues to your workspace",
        "required_role": "admin",
        "order": 3
    },
    "first_proposal_uploaded": {
        "label": "Upload your first proposal",
        "description": "Upload a contract document to get started",
        "required_role": None,
        "order": 4
    }
}


# Global singleton instance
_onboarding_crud = None
_lock = threading.RLock()


class OnboardingCRUD:
    """Onboarding Progress CRUD operations (Singleton)"""

    def __init__(self):
        """Initialize OnboardingCRUD"""
        mongodb = get_mongodb_client()
        self.db = mongodb.get_database()
        self.collection = self.db["onboarding_progress"]

    def get_applicable_tasks(self, role: str) -> List[str]:
        """
        Get list of task IDs applicable for a given role

        Args:
            role: User's role ("admin" or "user")

        Returns:
            List of task IDs the user should see
        """
        applicable_tasks = []
        for task_id, task_meta in ONBOARDING_TASKS.items():
            required_role = task_meta.get("required_role")
            # Include task if:
            # - No role requirement (None), OR
            # - User is admin and task requires admin
            if required_role is None or (role == "admin" and required_role == "admin"):
                applicable_tasks.append(task_id)

        return applicable_tasks

    def filter_tasks_by_role(self, all_tasks: Dict[str, bool], role: str) -> Dict[str, bool]:
        """
        Filter tasks dictionary to only include applicable tasks for role

        Args:
            all_tasks: Full tasks dictionary
            role: User's role

        Returns:
            Filtered tasks dictionary
        """
        applicable_task_ids = self.get_applicable_tasks(role)
        return {task_id: all_tasks.get(task_id, False) for task_id in applicable_task_ids}

    def get_progress(self, user_id: str, organization_id: str) -> Optional[Dict[str, Any]]:
        """
        Get onboarding progress for a user in an organization

        Args:
            user_id: User's ID
            organization_id: Organization ID

        Returns:
            Onboarding progress document or None
        """
        return self.collection.find_one({
            "user_id": user_id,
            "organization_id": organization_id
        })

    def create_progress(self, user_id: str, organization_id: str) -> Dict[str, Any]:
        """
        Create initial onboarding progress for a new user

        Args:
            user_id: User's ID
            organization_id: Organization ID

        Returns:
            Created onboarding progress document
        """
        now = datetime.utcnow()

        # Initialize all tasks from ONBOARDING_TASKS definition
        tasks = {task_id: False for task_id in ONBOARDING_TASKS.keys()}

        progress = {
            "user_id": user_id,
            "organization_id": organization_id,

            # Tour state
            "tour_completed": False,
            "tour_skipped": False,
            "tour_last_step": 0,
            "tour_started_at": None,
            "tour_completed_at": None,

            # Checklist tasks (all from ONBOARDING_TASKS)
            "tasks": tasks,

            # UI state
            "checklist_dismissed": False,
            "checklist_collapsed": False,

            "created_at": now,
            "updated_at": now
        }

        result = self.collection.insert_one(progress)
        progress["_id"] = result.inserted_id

        return progress

    def get_or_create_progress(
        self,
        user_id: str,
        organization_id: str,
        role: str = "user"
    ) -> Dict[str, Any]:
        """
        Get existing progress or create if doesn't exist.
        Filters tasks based on user's role.

        Args:
            user_id: User's ID
            organization_id: Organization ID
            role: User's role ("admin" or "user")

        Returns:
            Onboarding progress document with role-filtered tasks
        """
        progress = self.get_progress(user_id, organization_id)

        if not progress:
            progress = self.create_progress(user_id, organization_id)

        # Filter tasks based on role
        all_tasks = progress.get("tasks", {})
        filtered_tasks = self.filter_tasks_by_role(all_tasks, role)

        # Calculate completion stats for filtered tasks
        completion_stats = self.get_completion_stats(filtered_tasks)

        # Return progress with filtered tasks and stats
        progress["tasks"] = filtered_tasks
        progress["completion_stats"] = completion_stats

        return progress

    def update_task(
        self,
        user_id: str,
        organization_id: str,
        task_id: str,
        completed: bool
    ) -> bool:
        """
        Update a specific task completion status and recalculate completion stats

        Args:
            user_id: User's ID
            organization_id: Organization ID
            task_id: Task identifier (e.g., 'payment_added')
            completed: Whether task is completed

        Returns:
            True if successful
        """
        # First update the task
        result = self.collection.update_one(
            {
                "user_id": user_id,
                "organization_id": organization_id
            },
            {
                "$set": {
                    f"tasks.{task_id}": completed,
                    "updated_at": datetime.utcnow()
                }
            },
            upsert=True
        )

        # Get updated progress to calculate stats
        progress = self.collection.find_one({
            "user_id": user_id,
            "organization_id": organization_id
        })

        if progress:
            # Get user's role to determine applicable tasks
            from auth.database import get_mongodb_client
            db = get_mongodb_client().get_database()
            user = db["users"].find_one({"_id": user_id})

            if user:
                # Get role from organizations array
                role = "user"  # default
                for org in user.get("organizations", []):
                    if str(org.get("organization_id")) == organization_id:
                        role = org.get("role", "user")
                        break

                # Calculate completion stats based on role
                applicable_tasks = self.get_applicable_tasks(role)
                all_tasks = progress.get("tasks", {})

                completed_count = sum(1 for task_id in applicable_tasks if all_tasks.get(task_id, False))
                total_count = len(applicable_tasks)
                percentage = (completed_count / total_count * 100) if total_count > 0 else 0

                # Update completion stats
                self.collection.update_one(
                    {
                        "user_id": user_id,
                        "organization_id": organization_id
                    },
                    {
                        "$set": {
                            "completion_stats": {
                                "completed_count": completed_count,
                                "total_count": total_count,
                                "percentage": percentage
                            }
                        }
                    }
                )

        return result.modified_count > 0 or result.upserted_id is not None

    def start_tour(self, user_id: str, organization_id: str) -> bool:
        """
        Mark tour as started

        Args:
            user_id: User's ID
            organization_id: Organization ID

        Returns:
            True if successful
        """
        result = self.collection.update_one(
            {
                "user_id": user_id,
                "organization_id": organization_id
            },
            {
                "$set": {
                    "tour_started_at": datetime.utcnow(),
                    "tour_last_step": 0,
                    "updated_at": datetime.utcnow()
                }
            },
            upsert=True
        )

        return result.modified_count > 0 or result.upserted_id is not None

    def update_tour_step(
        self,
        user_id: str,
        organization_id: str,
        step_index: int
    ) -> bool:
        """
        Update current tour step

        Args:
            user_id: User's ID
            organization_id: Organization ID
            step_index: Current step index

        Returns:
            True if successful
        """
        result = self.collection.update_one(
            {
                "user_id": user_id,
                "organization_id": organization_id
            },
            {
                "$set": {
                    "tour_last_step": step_index,
                    "updated_at": datetime.utcnow()
                }
            }
        )

        return result.modified_count > 0

    def complete_tour(
        self,
        user_id: str,
        organization_id: str,
        skipped: bool = False
    ) -> bool:
        """
        Mark tour as completed or skipped

        Args:
            user_id: User's ID
            organization_id: Organization ID
            skipped: Whether tour was skipped

        Returns:
            True if successful
        """
        now = datetime.utcnow()

        result = self.collection.update_one(
            {
                "user_id": user_id,
                "organization_id": organization_id
            },
            {
                "$set": {
                    "tour_completed": not skipped,
                    "tour_skipped": skipped,
                    "tour_completed_at": now,
                    "tasks.tour_completed": not skipped,
                    "updated_at": now
                }
            },
            upsert=True
        )

        return result.modified_count > 0 or result.upserted_id is not None

    def restart_tour(
        self,
        user_id: str,
        organization_id: str
    ) -> bool:
        """
        Reset tour state to allow user to replay the tour

        Args:
            user_id: User's ID
            organization_id: Organization ID

        Returns:
            True if successful
        """
        result = self.collection.update_one(
            {
                "user_id": user_id,
                "organization_id": organization_id
            },
            {
                "$set": {
                    "tour_completed": False,
                    "tour_skipped": False,
                    "tour_last_step": 0,
                    "tour_started_at": None,
                    "tour_completed_at": None,
                    "tasks.tour_completed": False,
                    "updated_at": datetime.utcnow()
                }
            },
            upsert=True
        )

        return result.modified_count > 0 or result.upserted_id is not None

    def dismiss_checklist(
        self,
        user_id: str,
        organization_id: str,
        dismissed: bool = True
    ) -> bool:
        """
        Dismiss or restore checklist

        Args:
            user_id: User's ID
            organization_id: Organization ID
            dismissed: Whether to dismiss or restore

        Returns:
            True if successful
        """
        result = self.collection.update_one(
            {
                "user_id": user_id,
                "organization_id": organization_id
            },
            {
                "$set": {
                    "checklist_dismissed": dismissed,
                    "updated_at": datetime.utcnow()
                }
            }
        )

        return result.modified_count > 0

    def toggle_checklist_collapsed(
        self,
        user_id: str,
        organization_id: str,
        collapsed: bool
    ) -> bool:
        """
        Toggle checklist collapsed state

        Args:
            user_id: User's ID
            organization_id: Organization ID
            collapsed: Whether checklist is collapsed

        Returns:
            True if successful
        """
        result = self.collection.update_one(
            {
                "user_id": user_id,
                "organization_id": organization_id
            },
            {
                "$set": {
                    "checklist_collapsed": collapsed,
                    "updated_at": datetime.utcnow()
                }
            }
        )

        return result.modified_count > 0

    def get_completion_percentage(self, tasks: Dict[str, bool]) -> float:
        """
        Calculate completion percentage from tasks

        Args:
            tasks: Dictionary of task statuses

        Returns:
            Percentage completed (0-100)
        """
        if not tasks:
            return 0.0

        completed_count = sum(1 for completed in tasks.values() if completed)
        total_count = len(tasks)

        return (completed_count / total_count) * 100 if total_count > 0 else 0.0

    def get_completion_stats(self, tasks: Dict[str, bool]) -> Dict[str, Any]:
        """
        Calculate completion statistics for tasks

        Args:
            tasks: Dictionary of task statuses

        Returns:
            Dictionary with completed_count, total_count, and percentage
        """
        if not tasks:
            return {
                "completed_count": 0,
                "total_count": 0,
                "percentage": 0.0
            }

        completed_count = sum(1 for completed in tasks.values() if completed)
        total_count = len(tasks)
        percentage = (completed_count / total_count) * 100 if total_count > 0 else 0.0

        return {
            "completed_count": completed_count,
            "total_count": total_count,
            "percentage": round(percentage, 1)
        }


def get_onboarding_crud() -> OnboardingCRUD:
    """
    Get or create OnboardingCRUD instance (singleton pattern)

    Returns:
        OnboardingCRUD instance
    """
    global _onboarding_crud
    with _lock:
        if _onboarding_crud is None:
            _onboarding_crud = OnboardingCRUD()
        return _onboarding_crud
