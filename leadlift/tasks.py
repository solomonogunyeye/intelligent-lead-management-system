"""LEADLIFT — Follow-up task management."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from leadlift.models import FollowUpTask
from leadlift.repository import InquiryRepository


class TaskManager:
    """Manage follow-up tasks with overdue detection."""

    def __init__(self, repository: Optional[InquiryRepository] = None):
        self.repository = repository or InquiryRepository()

    def create_task(
        self,
        inquiry_id: str,
        description: str,
        due_at: str,
        assigned_to: Optional[str] = None,
        notes: Optional[str] = None,
    ) -> FollowUpTask:
        """Create a new follow-up task.

        Prevents accidental duplicate creation of identical pending tasks.
        """
        from leadlift.repository import get_repository

        repo = self.repository

        # Check for existing identical pending task for this inquiry
        existing_tasks = repo.get_tasks_for_inquiry(inquiry_id)
        for task in existing_tasks:
            if (not task.completed and task.description == description and task.due_at == due_at):
                raise ValueError(f"A pending task with this description and due date already exists "
                                 f"(task_id: {task.task_id})")

        task = FollowUpTask(
            task_id=f"tsk_{uuid.uuid4().hex[:12]}",
            inquiry_id=inquiry_id,
            description=description,
            due_at=due_at,
            assigned_to=assigned_to,
            notes=notes,
            completed=False,
        )
        repo.save_task(task)
        return task

    def get_task(self, task_id: str) -> Optional[FollowUpTask]:
        return self.repository.get_task(task_id)

    def get_tasks_for_inquiry(self, inquiry_id: str) -> List[FollowUpTask]:
        return self.repository.get_tasks_for_inquiry(inquiry_id)

    def get_all_tasks(self, include_completed: bool = False) -> List[FollowUpTask]:
        return self.repository.get_all_tasks(include_completed=include_completed)

    def get_overdue_tasks(self) -> List[FollowUpTask]:
        """Return all incomplete tasks whose due date has passed."""
        tasks = self.repository.get_all_tasks(include_completed=False)
        return [t for t in tasks if t.is_overdue()]

    def get_upcoming_tasks(self) -> List[FollowUpTask]:
        """Return incomplete tasks due in the future, sorted by due date."""
        tasks = self.repository.get_all_tasks(include_completed=False)
        upcoming = [t for t in tasks if not t.is_overdue()]
        upcoming.sort(key=lambda t: t.due_at)
        return upcoming

    def complete_task(self, task_id: str, completed_at: Optional[str] = None) -> None:
        if completed_at is None:
            completed_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        self.repository.complete_task(task_id, completed_at)

    def edit_task(self, task_id: str, **updates) -> FollowUpTask:
        """Edit an existing task."""
        task = self.repository.get_task(task_id)
        if task is None:
            raise ValueError(f"Task {task_id} not found")

        for key, value in updates.items():
            if hasattr(task, key):
                setattr(task, key, value)

        task.updated_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        self.repository.save_task(task)
        return task

    def delete_task(self, task_id: str) -> bool:
        return self.repository.delete_task(task_id)