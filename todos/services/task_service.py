import logging
from uuid import UUID

from django.db import transaction
from django.db.models import QuerySet
from django.utils import timezone
from todos.models import Task, TaskStatus, SyncStatus
from .task_list_service import TaskListService

logger = logging.getLogger("keep_up")


def enqueue_task_sync(task_id) -> None:
    """
    Queue a Google Tasks push once the surrounding transaction commits.

    Enqueueing inside the transaction would let a worker read the row before
    it is durable. The import is deferred because todos.tasks imports this
    module.
    """
    from todos.tasks import sync_task

    transaction.on_commit(lambda: sync_task.delay(str(task_id)))


READONLY_FIELDS = {
    "priority_display",
    "status_display",
    "sync_status_display",
    "subtask_count",
    "id",
    "attachments",
    "subtask_count",
    "position",
    "sync_status",
    "sync_status_display",
    "last_synced_at",
    "hidden",
    "created_at",
    "updated_at",
    "completed",
}


class TaskService:
    """Service for managing tasks with complex lifecycle and sync awareness."""

    @staticmethod
    def create_task(
        owner_id: str,
        title: str,
        notes: str = None,
        due=None,
        priority: str = "none",
        task_list=None,
        parent=None,
        tags=None,
        **kwargs,
    ) -> Task:
        """
        Create a new task for a user.

        Args:
            owner_id: UUID of the task owner
            title: Task title (max 1024 chars, required)
            notes: Optional task notes (max 8192 chars)
            due: Optional due date
            priority: Local priority (not synced to Google)
            task_list: Optional TaskList instance. If None, uses user's default list.
            parent: Optional parent Task for subtasks (one level deep only)
            tags: Optional list of Tag instances

        Returns:
            Created Task instance (sync_status=pending)

        Raises:
            ValueError: If title is empty, parent is invalid, or subtask depth violated
            Task.DoesNotExist: If parent or task_list not found
        """
        title = title.strip() if title else ""
        if not title:
            raise ValueError("Task title cannot be empty.")

        if len(title) > 1024:
            raise ValueError("Task title cannot exceed 1024 characters.")

        # Validate parent task
        if parent:
            if str(parent.owner_id) != str(owner_id) or parent.deleted:
                raise ValueError(
                    "Parent task not found or you don't have access to it."
                )
            if parent.parent:
                raise ValueError(
                    "Parent task cannot itself have a parent. "
                    "Google Tasks supports only one level of subtasks."
                )

        # Use default list if none specified
        if not task_list:
            task_list = TaskListService.get_or_create_default_list(owner_id)

        # Verify task_list ownership
        if str(task_list.owner_id) != str(owner_id) or task_list.deleted:
            raise ValueError("Task list not found or you don't have access to it.")

        task = Task.objects.create(
            owner_id=owner_id,
            title=title,
            notes=notes,
            due=due,
            priority=priority,
            task_list=task_list,
            parent=parent,
            status=TaskStatus.NEEDS_ACTION,
            sync_status=SyncStatus.PENDING,
        )

        if tags:
            task.tags.set(tags)

        logger.info(
            f"Created task '{title}' for user {owner_id} "
            f"in list {task_list.id} (sync_status=pending)"
        )
        enqueue_task_sync(task.id)
        return task

    @staticmethod
    def update_task(
        owner_id: str,
        task_id: str,
        **updates,
    ) -> Task:
        """
        Update a task's fields.

        Automatically sets/clears the completed timestamp when status changes.

        Args:
            owner_id: UUID of the task owner (for authorization)
            task_id: UUID of the task to update
            **updates: Fields to update (title, notes, status, due, priority, tags, parent)

        Returns:
            Updated Task instance

        Raises:
            Task.DoesNotExist: If task not found or doesn't belong to user
            ValueError: If updates are invalid (empty title, invalid parent, etc.)
        """
        updates = {k: v for k, v in updates.items() if k not in READONLY_FIELDS}
        task = Task.objects.get(id=task_id, owner_id=owner_id, deleted=False)

        # Validate and sanitize title if provided
        if "title" in updates:
            title = updates["title"].strip() if updates["title"] else ""
            if not title:
                raise ValueError("Task title cannot be empty.")
            if len(title) > 1024:
                raise ValueError("Task title cannot exceed 1024 characters.")
            updates["title"] = title

        # Validate parent if provided
        if "parent" in updates:
            new_parent = updates["parent"]
            if new_parent:
                if str(new_parent.owner_id) != str(owner_id) or new_parent.deleted:
                    raise ValueError(
                        "Parent task not found or you don't have access to it."
                    )
                if new_parent.parent:
                    raise ValueError(
                        "Parent task cannot itself have a parent. "
                        "Google Tasks supports only one level of subtasks."
                    )
                # Prevent self-parenting
                if new_parent.id == task.id:
                    raise ValueError("A task cannot be its own parent.")

        # Handle status change and completed timestamp
        if "status" in updates:
            new_status = updates["status"]
            if new_status == TaskStatus.COMPLETED:
                if task.status != TaskStatus.COMPLETED:
                    updates["completed"] = timezone.now()
            elif new_status == TaskStatus.NEEDS_ACTION:
                if task.status == TaskStatus.COMPLETED:
                    updates["completed"] = None

        # Remove tags from updates dict and handle separately
        tags_to_set = updates.pop("tags", None)

        # Update sync status to pending if synced (user is making local changes)
        if task.sync_status == SyncStatus.SYNCED:
            updates["sync_status"] = SyncStatus.PENDING

        # Update the task
        for field, value in updates.items():
            setattr(task, field, value)
        task.save(update_fields=list(updates.keys()) + ["updated_at"])

        # Handle tags separately (many-to-many)
        if tags_to_set is not None:
            task.tags.set(tags_to_set)

        logger.info(
            f"Updated task {task_id} for user {owner_id}. "
            f"Fields: {list(updates.keys())}. Sync status: {task.sync_status}"
        )
        enqueue_task_sync(task.id)
        return task

    @staticmethod
    def complete_task(owner_id: str, task_id: str) -> Task:
        """
        Mark a task as completed.

        Shorthand for update_task with status=completed.

        Args:
            owner_id: UUID of the task owner
            task_id: UUID of the task to complete

        Returns:
            Updated Task instance

        Raises:
            Task.DoesNotExist: If task not found or doesn't belong to user
        """
        return TaskService.update_task(
            owner_id=owner_id,
            task_id=task_id,
            status=TaskStatus.COMPLETED,
        )

    @staticmethod
    def reopen_task(owner_id: str, task_id: str) -> Task:
        """
        Mark a completed task as needs_action.

        Shorthand for update_task with status=needs_action.

        Args:
            owner_id: UUID of the task owner
            task_id: UUID of the task to reopen

        Returns:
            Updated Task instance

        Raises:
            Task.DoesNotExist: If task not found or doesn't belong to user
        """
        return TaskService.update_task(
            owner_id=owner_id,
            task_id=task_id,
            status=TaskStatus.NEEDS_ACTION,
        )

    @staticmethod
    def delete_task(owner_id: str, task_id: str) -> None:
        """
        Soft-delete a task (mark as deleted, don't remove from DB).

        Soft deletion allows the sync worker to detect the deletion and
        push it to Google Tasks for cleanup.

        Args:
            owner_id: UUID of the task owner
            task_id: UUID of the task to delete

        Returns:
            None

        Raises:
            Task.DoesNotExist: If task not found or doesn't belong to user
        """
        task = Task.objects.get(id=task_id, owner_id=owner_id, deleted=False)
        task.deleted = True
        task.sync_status = SyncStatus.PENDING
        task.save(update_fields=["deleted", "sync_status", "updated_at"])

        logger.info(
            f"Deleted task {task_id} for user {owner_id} (soft delete, sync_status=pending)"
        )
        enqueue_task_sync(task.id)

    @staticmethod
    def mark_synced(
        task_id: UUID | str, external_id: str, etag: str, position: str = ""
    ) -> Task:
        """
        Called by the sync worker after a successful push to Google Tasks.

        Args:
            task_id: UUID of the local task
            external_id: Google Tasks task ID
            etag: Google Tasks ETag, stored for future two-way sync
            position: Google's ordering string, used to preserve sort order

        Returns:
            Updated Task instance with sync_status=synced
        """
        task = Task.objects.get(id=task_id)
        task.external_id = external_id
        task.etag = etag
        if position:
            task.position = position
        task.sync_status = SyncStatus.SYNCED
        task.last_synced_at = timezone.now()
        task.save(
            update_fields=[
                "external_id",
                "etag",
                "position",
                "sync_status",
                "last_synced_at",
                "updated_at",
            ]
        )
        logger.info(f"Marked task {task_id} as synced (external_id={external_id})")
        return task

    @staticmethod
    def mark_skipped(task_id: UUID | str, reason: str = "") -> Task:
        """
        Called by the sync worker when the owner has not linked Google.

        Not a failure: there is nothing to push. The periodic sweep revisits
        skipped records so linking Google later backfills them.
        """
        task = Task.objects.get(id=task_id)
        task.sync_status = SyncStatus.SKIPPED
        task.save(update_fields=["sync_status", "updated_at"])
        logger.info(
            f"Skipped sync for task {task_id}: {reason or 'google not linked'}"
        )
        return task

    @staticmethod
    def mark_sync_failed(task_id: UUID | str, reason: str = "") -> Task:
        """
        Called by the sync worker when Google rejected the write for a reason
        retrying cannot fix.
        """
        task = Task.objects.get(id=task_id)
        task.sync_status = SyncStatus.FAILED
        task.save(update_fields=["sync_status", "updated_at"])
        logger.error(f"Marked task {task_id} as failed sync. Reason: {reason}")
        return task

    @staticmethod
    def get_task(owner_id: str, task_id: str) -> Task:
        """
        Retrieve a single task by ID.

        Args:
            owner_id: UUID of the task owner
            task_id: UUID of the task to retrieve

        Returns:
            Task instance

        Raises:
            Task.DoesNotExist: If task not found, doesn't belong to user, or is deleted
        """
        return Task.objects.get(id=task_id, owner_id=owner_id, deleted=False)

    @staticmethod
    def get_user_tasks(
        owner_id: str, task_list_id: str = None, include_deleted: bool = False
    ) -> QuerySet:
        """
        Retrieve all tasks for a user, optionally filtered by task list.

        Args:
            owner_id: UUID of the task owner
            task_list_id: Optional TaskList UUID to filter by
            include_deleted: If True, include soft-deleted tasks

        Returns:
            QuerySet of Task instances, ordered by status, due date, and position
        """
        queryset = Task.objects.filter(owner_id=owner_id)

        if not include_deleted:
            queryset = queryset.filter(deleted=False)

        if task_list_id:
            queryset = queryset.filter(task_list_id=task_list_id)

        return queryset

    @staticmethod
    def move_task_to_list(
        owner_id: str,
        task_id: str,
        task_list_id: str,
    ) -> Task:
        """
        Move a task to a different task list.

        Args:
            owner_id: UUID of the task owner
            task_id: UUID of the task to move
            task_list_id: UUID of the destination TaskList

        Returns:
            Updated Task instance

        Raises:
            Task.DoesNotExist: If task not found or doesn't belong to user
            ValueError: If task_list not found, doesn't belong to user, or is deleted
        """
        task = Task.objects.get(id=task_id, owner_id=owner_id, deleted=False)
        task_list = TaskListService.get_task_list(owner_id, task_list_id)

        return TaskService.update_task(
            owner_id=owner_id,
            task_id=task_id,
            task_list=task_list,
        )

    @staticmethod
    def convert_to_subtask(
        owner_id: str,
        task_id: str,
        parent_task_id: str,
    ) -> Task:
        """
        Convert a task to a subtask by assigning it a parent.

        Args:
            owner_id: UUID of the task owner
            task_id: UUID of the task to convert
            parent_task_id: UUID of the parent task

        Returns:
            Updated Task instance

        Raises:
            Task.DoesNotExist: If task or parent not found, or don't belong to user
            ValueError: If parent already has a parent (depth violation)
        """
        parent_task = TaskService.get_task(owner_id, parent_task_id)
        return TaskService.update_task(
            owner_id=owner_id,
            task_id=task_id,
            parent=parent_task,
        )

    @staticmethod
    def promote_subtask(owner_id: str, task_id: str) -> Task:
        """
        Promote a subtask to a top-level task by removing its parent.

        Args:
            owner_id: UUID of the task owner
            task_id: UUID of the subtask to promote

        Returns:
            Updated Task instance

        Raises:
            Task.DoesNotExist: If task not found or doesn't belong to user
        """
        return TaskService.update_task(
            owner_id=owner_id,
            task_id=task_id,
            parent=None,
        )
