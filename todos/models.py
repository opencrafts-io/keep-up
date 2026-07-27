"""
Copyright (c) 2025 Open Crafts Interactive. All Rights Reserved.

Redesigned models for local-first task management with async Google Tasks sync.

Design principles:
- Tasks and lists are created locally first, then pushed to Google Tasks via Celery
- Google-specific fields (external_id, etag, sync_status) are kept separate from
  user-facing fields to make the boundary between local and Google data clear
- Local-only fields (tags, color, priority) are never pushed to Google
- sync_status drives the Celery worker — it only processes pending/failed records
"""

import uuid
from django.db import models


class SyncStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    SYNCED = "synced", "Synced"
    FAILED = "failed", "Failed"
    # The owner has not linked Google, so there is nothing to push. Distinct
    # from FAILED, which means a real error worth a human looking at.
    SKIPPED = "skipped", "Skipped"


class TaskPriority(models.TextChoices):
    NONE = "none", "None"
    LOW = "low", "Low"
    MEDIUM = "medium", "Medium"
    HIGH = "high", "High"


class TaskStatus(models.TextChoices):
    NEEDS_ACTION = "needsAction", "Needs Action"
    COMPLETED = "completed", "Completed"


class AttachmentSource(models.TextChoices):
    LOCAL = "local", "Local"
    GOOGLE = "google", "Google"


class GoogleSyncState(models.Model):
    """
    Per-user watermark for pulling changes back from Google Tasks.

    The Tasks API has no sync tokens, so incremental reads are driven by
    updatedMin against last_pulled_at. The watermark is the time the pull
    started, never the time it finished, or changes made while a pull was
    running would be skipped forever.
    """

    owner_id = models.UUIDField(primary_key=True)
    last_pulled_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Start time of the last successful pull. Null means never pulled.",
    )
    last_attempted_at = models.DateTimeField(null=True, blank=True)
    last_error = models.CharField(max_length=512, blank=True, default="")

    def __str__(self):
        return f"{self.owner_id} last pulled {self.last_pulled_at}"


class Tag(models.Model):
    """
    User-defined tags for tasks. Local only — not synced to Google Tasks.
    Tags are scoped per user so users don't see each other's tags.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner_id = models.UUIDField(db_index=True)
    name = models.CharField(max_length=64)
    color = models.CharField(
        max_length=7,
        default="#6B7280",
        help_text="Hex color code e.g. #FF5733",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                fields=["owner_id", "name"], name="unique_tag_per_user"
            )
        ]

    def __str__(self):
        return f"{self.name} ({self.owner_id})"


class TaskList(models.Model):
    """
    A named collection of tasks, maps to a Google Tasks TaskList.

    Lifecycle:
        1. Created locally with sync_status=pending
        2. Celery worker picks it up and creates it in Google Tasks
        3. Worker writes back external_id, etag, and sets sync_status=synced
        4. Tasks in this list can now be synced (they need the list's external_id)
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner_id = models.UUIDField(db_index=True)

    title = models.CharField(max_length=255)
    color = models.CharField(
        max_length=7,
        default="#3B82F6",
        help_text="Hex color code for UI display. Local only.",
    )
    is_default = models.BooleanField(
        default=False,
        help_text="The default list is used when no list is specified for a new task.",
    )

    # Google Tasks sync fields
    external_id = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text="Google Tasks list ID. Empty until first sync.",
    )
    etag = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text="Google Tasks ETag for conflict detection.",
    )
    sync_status = models.CharField(
        max_length=16,
        choices=SyncStatus.choices,
        default=SyncStatus.PENDING,
        db_index=True,
    )
    last_synced_at = models.DateTimeField(null=True, blank=True)

    # Housekeeping
    deleted = models.BooleanField(default=False, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-is_default", "title"]
        constraints = [
            models.UniqueConstraint(
                fields=["owner_id"],
                condition=models.Q(is_default=True, deleted=False),
                name="unique_default_list_per_user",
            ),
            # The pull upserts on this pair. Without the constraint two
            # overlapping pulls can both miss and both insert.
            models.UniqueConstraint(
                fields=["owner_id", "external_id"],
                condition=~models.Q(external_id=""),
                name="unique_list_external_id_per_user",
            ),
        ]

    def __str__(self):
        return f"{self.title} ({self.owner_id})"


class Task(models.Model):
    """
    A single task. Created locally first, then synced to Google Tasks by Celery.

    Sync dependency: a task's TaskList must be synced (has an external_id) before
    this task can be pushed to Google. The Celery worker must check this.

    Subtasks: supported one level deep (matching Google Tasks' constraint).
    A parent task cannot itself have a parent.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner_id = models.UUIDField(db_index=True)

    task_list = models.ForeignKey(
        TaskList,
        on_delete=models.CASCADE,
        related_name="tasks",
        null=True,
        blank=True,
        help_text="The list this task belongs to. Null means the user's default list.",
    )
    parent = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="subtasks",
        help_text="Parent task for subtasks. One level deep only (Google Tasks constraint).",
    )

    # Core fields
    title = models.CharField(max_length=1024)
    notes = models.CharField(max_length=8192, blank=True, null=True)
    status = models.CharField(
        max_length=32,
        choices=TaskStatus.choices,
        default=TaskStatus.NEEDS_ACTION,
    )
    due = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Google Tasks only stores the date, not the time.",
    )
    completed = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Set automatically when status changes to completed.",
    )

    # Local-only fields — never pushed to Google Tasks
    priority = models.CharField(
        max_length=16,
        choices=TaskPriority.choices,
        default=TaskPriority.NONE,
        help_text="Local priority. Not synced to Google Tasks.",
    )
    tags = models.ManyToManyField(
        Tag,
        blank=True,
        related_name="tasks",
        help_text="Local tags. Not synced to Google Tasks.",
    )
    position = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text="Ordering string from Google Tasks. Used to preserve sort order after sync.",
    )

    # Google Tasks sync fields
    external_id = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text="Google Tasks task ID. Empty until first sync.",
    )
    etag = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text="Google Tasks ETag for conflict detection on updates.",
    )
    sync_status = models.CharField(
        max_length=16,
        choices=SyncStatus.choices,
        default=SyncStatus.PENDING,
        db_index=True,
    )
    last_synced_at = models.DateTimeField(null=True, blank=True)

    # Housekeeping
    hidden = models.BooleanField(
        default=False,
        help_text="Set by Google when a completed task's list is cleared. Read-only after sync.",
    )
    deleted = models.BooleanField(default=False, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["status", "due", "position"]
        constraints = [
            # The pull upserts on this pair. Without the constraint two
            # overlapping pulls can both miss and both insert.
            models.UniqueConstraint(
                fields=["owner_id", "external_id"],
                condition=~models.Q(external_id=""),
                name="unique_task_external_id_per_user",
            ),
        ]

    def __str__(self):
        return f"{self.title} ({self.owner_id})"


class Attachment(models.Model):
    """
    A file or link attached to a task.

    Two sources:
      - local: a real file uploaded by the user, stored in S3. Clients receive
               a pre-signed download_url at serialization time, never the s3_key.
      - google: a read-only link surfaced from Google Tasks' links[] field.
                Has a url and link_type but no actual file.

    Size constraint: total size of all local attachments on a single task must
    not exceed 100MB. Enforced in the serializer, not the model.
    """

    GOOGLE_LINK_TYPES = [
        ("email", "Email"),
        ("generic", "Generic"),
        ("chat_message", "Chat Message"),
        ("keep_note", "Keep Note"),
    ]

    MAX_TASK_ATTACHMENT_SIZE = 10 * 1024 * 1024  # 10 mb

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    task = models.ForeignKey(
        Task,
        on_delete=models.CASCADE,
        related_name="attachments",
    )
    # Denormalized for ownership checks without joining through task
    owner_id = models.UUIDField(db_index=True)

    source = models.CharField(
        max_length=16,
        choices=AttachmentSource.choices,
        default=AttachmentSource.LOCAL,
        db_index=True,
    )

    # Local file fields — populated for source=local only
    file_name = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text="Original filename as uploaded by the user.",
    )
    file_size = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="File size in bytes.",
    )
    mime_type = models.CharField(
        max_length=127,
        blank=True,
        default="",
        help_text="MIME type e.g. image/jpeg, application/pdf, video/mp4.",
    )
    s3_key = models.CharField(
        max_length=1024,
        blank=True,
        default="",
        help_text="S3 object key. Never exposed to clients — use download_url instead.",
    )

    # Google link fields — populated for source=google only
    url = models.URLField(
        blank=True,
        default="",
        help_text="URL for Google Tasks links. Empty for local files.",
    )
    link_type = models.CharField(
        max_length=32,
        blank=True,
        default="",
        choices=GOOGLE_LINK_TYPES,
        help_text="Google Tasks link type. Empty for local files.",
    )
    description = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text="Optional description. Maps to Google Tasks link description.",
    )

    # Sync fields
    sync_status = models.CharField(
        max_length=16,
        choices=SyncStatus.choices,
        default=SyncStatus.PENDING,
        db_index=True,
        help_text="Google attachments are created as synced. Local attachments start as pending.",
    )
    last_synced_at = models.DateTimeField(null=True, blank=True)

    # Housekeeping
    deleted = models.BooleanField(default=False, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]

    def __str__(self):
        if self.source == AttachmentSource.LOCAL:
            return f"{self.file_name} → {self.task_id}"
        return f"{self.link_type}: {self.url} → {self.task_id}"
