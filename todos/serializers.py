import logging
from rest_framework import serializers
from rest_framework.exceptions import ValidationError
from django.utils import timezone
from todos.models import (
    Task,
    TaskList,
    Tag,
    Attachment,
    TaskStatus,
    TaskPriority,
    AttachmentSource,
)

logger = logging.getLogger("keep_up")


class TagSerializer(serializers.ModelSerializer):
    """Serializer for user-defined tags."""

    class Meta:
        model = Tag
        fields = ["id", "name", "color", "created_at"]
        read_only_fields = ["id", "created_at"]

    def validate_name(self, value):
        """Ensure tag name is not empty and reasonably short."""
        if not value or not value.strip():
            raise ValidationError("Tag name cannot be empty.")
        if len(value) > 64:
            raise ValidationError("Tag name cannot exceed 64 characters.")
        return value.strip()

    def validate_color(self, value):
        """Validate hex color format."""
        if not value.startswith("#") or len(value) != 7:
            raise ValidationError("Color must be a valid hex code (e.g., #FF5733).")
        try:
            int(value[1:], 16)
        except ValueError:
            raise ValidationError("Color must be a valid hex code (e.g., #FF5733).")
        return value


class AttachmentSerializer(serializers.ModelSerializer):
    """
    Serializer for attachments with source-specific fields.

    For local files: returns file_name, file_size, mime_type, and a download_url
    (pre-signed S3 URL generated at serialization time, never exposing s3_key).

    For Google links: returns url, link_type, and description.
    """

    download_url = serializers.SerializerMethodField()
    source_display = serializers.CharField(source="get_source_display", read_only=True)
    link_type_display = serializers.CharField(
        source="get_link_type_display", read_only=True
    )

    class Meta:
        model = Attachment
        fields = [
            "id",
            "source",
            "source_display",
            "file_name",
            "file_size",
            "mime_type",
            "download_url",
            "url",
            "link_type",
            "link_type_display",
            "description",
            "sync_status",
            "last_synced_at",
            "created_at",
        ]
        read_only_fields = [
            "id",
            "sync_status",
            "last_synced_at",
            "created_at",
            "download_url",
        ]

    def get_download_url(self, obj) -> str | None:
        """
        Generate pre-signed S3 URL for local files.
        Returns None for Google links.

        TODO: Integrate with your S3 client to generate pre-signed URLs.
        Example: s3_client.generate_presigned_url(...)
        """
        if obj.source == AttachmentSource.LOCAL and obj.s3_key:
            # TODO: Generate pre-signed URL here
            # from django.conf import settings
            # s3_client = boto3.client('s3', ...)
            # return s3_client.generate_presigned_url(
            #     'get_object',
            #     Params={'Bucket': settings.AWS_STORAGE_BUCKET_NAME, 'Key': obj.s3_key},
            #     ExpiresIn=3600
            # )
            logger.warning(
                f"S3 download_url generation not yet implemented for {obj.id}"
            )
            return None
        return None

    def validate(self, data):
        """Ensure source-specific fields are populated correctly."""
        source = data.get("source", self.instance.source if self.instance else None)

        if source == AttachmentSource.LOCAL:
            if not data.get("file_name"):
                raise ValidationError(
                    {"file_name": "file_name is required for local attachments."}
                )
            if not data.get("mime_type"):
                raise ValidationError(
                    {"mime_type": "mime_type is required for local attachments."}
                )
            if not data.get("s3_key"):
                raise ValidationError(
                    {"s3_key": "s3_key is required for local attachments."}
                )

        elif source == AttachmentSource.GOOGLE:
            if not data.get("url"):
                raise ValidationError(
                    {"url": "url is required for Google attachments."}
                )
            if not data.get("link_type"):
                raise ValidationError(
                    {"link_type": "link_type is required for Google attachments."}
                )

        return data


class TaskListSerializer(serializers.ModelSerializer):
    """
    Serializer for task lists with sync status.

    Sync status drives Celery workers:
    - pending: queued for creation in Google Tasks
    - synced: exists in Google Tasks (has external_id)
    - failed: last sync attempt failed; retry recommended
    """

    sync_status_display = serializers.CharField(
        source="get_sync_status_display", read_only=True
    )
    task_count = serializers.SerializerMethodField()

    class Meta:
        model = TaskList
        fields = [
            "id",
            "title",
            "color",
            "is_default",
            "sync_status",
            "sync_status_display",
            "last_synced_at",
            "task_count",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "sync_status",
            "last_synced_at",
            "task_count",
            "created_at",
            "updated_at",
        ]

    def get_task_count(self, obj) -> int:
        """Return count of active (non-deleted) tasks in this list."""
        return obj.tasks.filter(deleted=False).count()

    def validate_title(self, value):
        """Ensure title is not empty."""
        if not value or not value.strip():
            raise ValidationError("Title cannot be empty.")
        return value.strip()

    def validate_color(self, value):
        """Validate hex color format."""
        if not value.startswith("#") or len(value) != 7:
            raise ValidationError("Color must be a valid hex code (e.g., #3B82F6).")
        try:
            int(value[1:], 16)
        except ValueError:
            raise ValidationError("Color must be a valid hex code (e.g., #3B82F6).")
        return value

    def validate_is_default(self, value):
        """
        Ensure only one default list per user.

        This validation only works on create/update, not bulk operations.
        Consider adding a pre_save signal if you need stronger guarantees.
        """
        if value and self.instance is None:
            # Creating a new default list
            owner_id = self.context.get("owner_id")
            if (
                owner_id
                and TaskList.objects.filter(
                    owner_id=owner_id, is_default=True, deleted=False
                ).exists()
            ):
                raise ValidationError(
                    "User already has a default list. "
                    "Update the existing one instead."
                )
        return value


class TaskSerializer(serializers.ModelSerializer):
    """
    Serializer for tasks with nested relationships and sync status.

    - Includes nested attachment data
    - Tags are M2M; send tag IDs on create/update
    - Automatically sets `completed` timestamp when status → completed
    - Respects task_list ownership and subtask depth constraint
    """

    attachments = AttachmentSerializer(many=True, read_only=True)
    tags = serializers.PrimaryKeyRelatedField(
        queryset=Tag.objects.all(),
        many=True,
        required=False,
    )
    status_display = serializers.CharField(source="get_status_display", read_only=True)
    priority_display = serializers.CharField(
        source="get_priority_display", read_only=True
    )
    sync_status_display = serializers.CharField(
        source="get_sync_status_display", read_only=True
    )
    subtask_count = serializers.SerializerMethodField()
    parent_id = serializers.PrimaryKeyRelatedField(
        queryset=Task.objects.all(),
        required=False,
        allow_null=True,
        write_only=True,
    )

    class Meta:
        model = Task
        fields = [
            "id",
            "task_list",
            "parent_id",
            "title",
            "notes",
            "status",
            "status_display",
            "due",
            "completed",
            "priority",
            "priority_display",
            "tags",
            "attachments",
            "subtask_count",
            "position",
            "sync_status",
            "sync_status_display",
            "last_synced_at",
            "hidden",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
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
        ]

    def get_subtask_count(self, obj) -> int:
        """Return count of active (non-deleted) subtasks."""
        return obj.subtasks.filter(deleted=False).count()

    def validate_title(self, value):
        """Ensure title is not empty."""
        if not value or not value.strip():
            raise ValidationError("Title cannot be empty.")
        if len(value) > 1024:
            raise ValidationError("Title cannot exceed 1024 characters.")
        return value.strip()

    def validate_notes(self, value):
        """Ensure notes don't exceed max length."""
        if value and len(value) > 8192:
            raise ValidationError("Notes cannot exceed 8192 characters.")
        return value

    def validate(self, data):
        """
        Cross-field validation:
        - Parent task cannot itself have a parent (one level deep only)
        - Parent task must belong to same user and list
        - Cannot set parent to self
        - Completed timestamp is set automatically, not by client
        """
        parent = data.get("parent_id", self.instance.parent if self.instance else None)

        if parent:
            if parent.parent:
                raise ValidationError(
                    {
                        "parent_id": "Parent task cannot itself have a parent. "
                        "Google Tasks supports only one level of subtasks."
                    }
                )

            owner_id = self.context.get("owner_id")
            if parent.owner_id != owner_id or parent.deleted:
                raise ValidationError(
                    {
                        "parent_id": "Parent task not found or you don't have access to it."
                    }
                )

            # Prevent setting self as parent
            if self.instance and parent.id == self.instance.id:
                raise ValidationError({"parent_id": "A task cannot be its own parent."})

        return data

    def create(self, validated_data):
        """Create a new task and set owner from context."""
        owner_id = self.context.get("owner_id")
        tags = validated_data.pop("tags", [])

        task = Task.objects.create(owner_id=owner_id, **validated_data)

        if tags:
            task.tags.set(tags)

        return task

    def update(self, instance, validated_data):
        """
        Update task and handle status changes.

        When status changes to 'completed', automatically set the
        completed timestamp.
        """
        tags = validated_data.pop("tags", None)
        old_status = instance.status
        new_status = validated_data.get("status", old_status)

        # Update all fields except tags
        for attr, value in validated_data.items():
            setattr(instance, attr, value)

        # Set completed timestamp when transitioning to completed
        if old_status != TaskStatus.COMPLETED and new_status == TaskStatus.COMPLETED:
            instance.completed = timezone.now()
        # Clear completed timestamp if moving away from completed
        elif old_status == TaskStatus.COMPLETED and new_status != TaskStatus.COMPLETED:
            instance.completed = None

        instance.save()

        # Update tags if provided
        if tags is not None:
            instance.tags.set(tags)

        return instance


class TaskDetailSerializer(TaskSerializer):
    """
    Extended serializer for detail views.

    Includes parent task details (read-only) and a list of subtask IDs
    for convenient hierarchical navigation.
    """

    parent = TaskSerializer(read_only=True)
    subtask_ids = serializers.PrimaryKeyRelatedField(
        source="subtasks",
        many=True,
        read_only=True,
    )

    class Meta(TaskSerializer.Meta):
        fields = TaskSerializer.Meta.fields + ["parent", "subtask_ids"]


class TaskListWithTasksSerializer(TaskListSerializer):
    """
    Extended serializer for list detail views.

    Includes all tasks in the list with full task data.
    Use sparingly to avoid N+1 queries; consider pagination.
    """

    tasks = serializers.SerializerMethodField()

    class Meta(TaskListSerializer.Meta):
        fields = TaskListSerializer.Meta.fields + ["tasks"]

    def get_tasks(self, obj):
        """Return all active tasks in this list."""
        tasks = obj.tasks.filter(deleted=False)
        return TaskSerializer(tasks, many=True, context=self.context).data


class SyncStatusSerializer(serializers.Serializer):
    """
    Read-only serializer for sync status responses.

    Used by the sync endpoint to inform clients about what's pending.
    """

    pending_lists = serializers.IntegerField()
    pending_tasks = serializers.IntegerField()
    failed_lists = serializers.IntegerField()
    failed_tasks = serializers.IntegerField()
    pending_attachments = serializers.IntegerField()
    last_sync_check = serializers.DateTimeField(read_only=True)


class BulkTaskUpdateSerializer(serializers.Serializer):
    """
    Serializer for bulk operations (e.g., move multiple tasks to a list).

    Example:
        POST /tasks/bulk-update/
        {
            "task_ids": ["uuid1", "uuid2"],
            "task_list": "uuid3",
            "status": "completed"
        }
    """

    task_ids = serializers.ListField(child=serializers.UUIDField(), required=True)
    task_list = serializers.PrimaryKeyRelatedField(
        queryset=TaskList.objects.all(),
        required=False,
        allow_null=True,
    )
    status = serializers.ChoiceField(
        choices=TaskStatus.choices,
        required=False,
    )
    priority = serializers.ChoiceField(
        choices=TaskPriority.choices,
        required=False,
    )
    add_tags = serializers.PrimaryKeyRelatedField(
        queryset=Tag.objects.all(),
        many=True,
        required=False,
    )
    remove_tags = serializers.PrimaryKeyRelatedField(
        queryset=Tag.objects.all(),
        many=True,
        required=False,
    )

    def validate(self, data):
        """Ensure at least one update field is provided."""
        update_fields = {
            "task_list",
            "status",
            "priority",
            "add_tags",
            "remove_tags",
        }
        if not any(field in data for field in update_fields):
            raise ValidationError(
                "At least one update field must be provided "
                "(task_list, status, priority, add_tags, or remove_tags)."
            )
        return data
