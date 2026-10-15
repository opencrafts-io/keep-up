import logging
from uuid import UUID

from django.db import IntegrityError, transaction
from django.utils import timezone
from todos.models import TaskList, SyncStatus

logger = logging.getLogger("keep_up")


def enqueue_list_sync(list_id) -> None:
    """
    Queue a Google Tasks push once the surrounding transaction commits.

    Enqueueing inside the transaction would let a worker read the row before
    it is durable. The import is deferred because todos.tasks imports this
    module.

    Outside an atomic block Django runs the callback inline, so this executes
    within the request. A broker that cannot be reached must therefore not
    fail the write: the record is already committed as pending, and the
    periodic sweep re-queues it. Syncing late beats refusing to save.
    """
    from todos.tasks import sync_task_list

    def enqueue():
        try:
            sync_task_list.delay(str(list_id))
        except Exception:
            logger.exception(
                "Could not queue a Google sync for task list %s; the sweep "
                "will retry it",
                list_id,
            )

    transaction.on_commit(enqueue)


class TaskListService:
    """Service for managing task lists with sync awareness."""

    @staticmethod
    def create_task_list(
        owner_id: str,
        title: str,
        color: str = "#3B82F6",
        is_default: bool = False,
    ) -> TaskList:
        """
        Create a new task list.

        Args:
            owner_id: UUID of the list owner
            title: List title (max 255 chars)
            color: Hex color code for UI (default: blue)
            is_default: Whether this is the default list for the user

        Returns:
            Created TaskList instance (sync_status=pending)

        Raises:
            ValueError: If is_default=True but user already has a default list
            IntegrityError: If title is empty or invalid
        """
        title = title.strip() if title else ""
        if not title:
            raise ValueError("List title cannot be empty.")

        if is_default:
            existing_default = TaskList.objects.filter(
                owner_id=owner_id, is_default=True, deleted=False
            ).first()
            if existing_default:
                raise ValueError(
                    f"User already has a default list: '{existing_default.title}'. "
                    "Set is_default=False or update the existing default list."
                )

        task_list = TaskList.objects.create(
            owner_id=owner_id,
            title=title,
            color=color,
            is_default=is_default,
            sync_status=SyncStatus.PENDING,
        )
        logger.info(
            f"Created task list '{title}' for user {owner_id} "
            f"(default={is_default}, sync_status=pending)"
        )
        enqueue_list_sync(task_list.id)
        return task_list

    @staticmethod
    def update_task_list(owner_id: str, list_id: str, **updates) -> TaskList:
        """
        Update a task list.

        Note: external_id, etag, and sync_status should only be updated by the
        sync worker, not by users. This method enforces that.

        Args:
            owner_id: UUID of the list owner (for authorization)
            list_id: UUID of the list to update
            **updates: Fields to update (title, color, is_default)

        Returns:
            Updated TaskList instance

        Raises:
            TaskList.DoesNotExist: If list not found or doesn't belong to user
            ValueError: If trying to update sync fields or setting invalid is_default
        """
        task_list = TaskList.objects.get(id=list_id, owner_id=owner_id, deleted=False)

        # Prevent manual updates to sync fields
        sync_fields = {"external_id", "etag", "sync_status", "last_synced_at"}
        if sync_fields & set(updates.keys()):
            logger.warning(
                f"Attempted to manually update sync fields on list {list_id}"
            )
            raise ValueError(
                "Sync fields (external_id, etag, sync_status) "
                "are managed by the sync worker and cannot be updated manually."
            )

        if "title" in updates:
            new_title = updates["title"].strip() if updates["title"] else ""
            if not new_title:
                raise ValueError("List title cannot be empty.")
            task_list.title = new_title

        if "color" in updates:
            task_list.color = updates["color"]

        if "is_default" in updates:
            new_is_default = updates["is_default"]
            if new_is_default and not task_list.is_default:
                # Unset any existing default list
                TaskList.objects.filter(
                    owner_id=owner_id, is_default=True, deleted=False
                ).update(is_default=False)
            task_list.is_default = new_is_default
        # A local edit invalidates whatever Google currently holds.
        if task_list.sync_status == SyncStatus.SYNCED:
            task_list.sync_status = SyncStatus.PENDING
        task_list.save()
        logger.info(f"Updated task list {list_id} for user {owner_id}")
        enqueue_list_sync(task_list.id)
        return task_list

    @staticmethod
    def delete_task_list(owner_id: str, list_id: str) -> None:
        """
        Soft-delete a task list (marks as deleted but keeps data for sync).

        Also soft-deletes all tasks in this list.

        Args:
            owner_id: UUID of the list owner (for authorization)
            list_id: UUID of the list to delete

        Raises:
            TaskList.DoesNotExist: If list not found or doesn't belong to user
            ValueError: If deleting the default list
        """
        task_list = TaskList.objects.get(id=list_id, owner_id=owner_id, deleted=False)

        if task_list.is_default:
            raise ValueError(
                "Cannot delete the default list. "
                "Designate another list as default first."
            )

        task_list.deleted = True
        task_list.sync_status = SyncStatus.PENDING
        task_list.save()

        # Soft-delete all tasks in this list. Their sync_status is left alone:
        # deleting the list at Google removes its tasks server-side, so the
        # worker settles them once the list deletion actually lands.
        task_list.tasks.update(deleted=True)

        logger.info(f"Soft-deleted task list {list_id} and its tasks")
        enqueue_list_sync(task_list.id)

    @staticmethod
    def get_user_lists(owner_id: str):
        """Get all active task lists for a user, ordered by default status then title."""
        return TaskList.objects.filter(owner_id=owner_id, deleted=False).order_by(
            "-is_default", "title"
        )

    @staticmethod
    def get_task_list(owner_id: str, list_id: str) -> TaskList:
        """Retrieve a single task list by ID (with ownership and active checks)."""
        return TaskList.objects.get(id=list_id, owner_id=owner_id, deleted=False)

    @staticmethod
    def get_or_create_default_list(owner_id: str) -> TaskList:
        """
        Get the user's default list, or create one if it doesn't exist.

        Useful for auto-assigning new tasks when no list is specified.
        """
        task_list = TaskList.objects.filter(
            owner_id=owner_id, is_default=True, deleted=False
        ).first()

        if not task_list:
            task_list = TaskListService.create_task_list(
                owner_id=owner_id,
                title="My Tasks",
                is_default=True,
            )
            logger.info(f"Auto-created default list for user {owner_id}")

        return task_list

    @staticmethod
    def mark_synced(list_id: UUID | str, external_id: str, etag: str) -> TaskList:
        """
        Called by the Celery sync worker after successfully creating a list in Google Tasks.

        Args:
            list_id: UUID of the local list
            external_id: Google Tasks list ID
            etag: Google Tasks ETag

        Returns:
            Updated TaskList instance with sync_status=synced
        """
        task_list = TaskList.objects.get(id=list_id)
        task_list.external_id = external_id
        task_list.etag = etag
        task_list.sync_status = SyncStatus.SYNCED
        task_list.updated_at = timezone.now()
        task_list.save(
            update_fields=["external_id", "etag", "sync_status", "updated_at"]
        )
        logger.info(
            f"Marked task list {list_id} as synced " f"(external_id={external_id})"
        )
        return task_list

    @staticmethod
    def mark_skipped(list_id: UUID | str, reason: str = "") -> TaskList:
        """
        Called by the sync worker when the owner has not linked Google.

        Not a failure: there is simply nothing to push. The periodic sweep
        revisits skipped records so linking Google later backfills them.
        """
        task_list = TaskList.objects.get(id=list_id)
        task_list.sync_status = SyncStatus.SKIPPED
        task_list.save(update_fields=["sync_status"])
        logger.info(
            f"Skipped sync for task list {list_id}: {reason or 'google not linked'}"
        )
        return task_list

    @staticmethod
    def mark_sync_failed(list_id: UUID | str, reason: str = "") -> TaskList:
        """
        Called by the Celery sync worker if syncing fails.

        Args:
            list_id: UUID of the local list
            reason: Error message explaining why sync failed

        Returns:
            Updated TaskList instance with sync_status=failed
        """
        task_list = TaskList.objects.get(id=list_id)
        task_list.sync_status = SyncStatus.FAILED
        task_list.save(update_fields=["sync_status"])
        logger.error(f"Marked task list {list_id} as failed sync. Reason: {reason}")
        return task_list
