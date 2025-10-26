import uuid
from django.db import models


# Create your models here.
class AssignmentInfo(models.Model):
    CONTEXT_TYPES = {
        "CONTEXT_TYPE_UNSPECIFIED": "Unknown value for this task's context.",
        "GMAIL": "The task is created from Gmail.",
        "DOCUMENT": "The task is assigned from a document.",
        "SPACE": " 	The task is assigned from a Chat Space.",
    }

    link_to_task = models.URLField()
    surface_type = models.CharField(
        max_length=64,
        choices=CONTEXT_TYPES,
    )

    task = models.ForeignKey(
        "Task", related_name="task_assignments", on_delete=models.CASCADE
    )


class Task(models.Model):
    TASK_STATUSES = {
        "needsAction": "Needs Action",
        "completed": "Completed",
    }

    external_id = models.CharField(
        max_length=255,
        primary_key=True,
    )
    kind = models.CharField(
        blank=True,
        null=True,
    )
    etag = models.CharField()
    title = models.CharField(max_length=1024)
    updated = models.DateTimeField(
        blank=True,
        null=True,
    )
    self_link = models.URLField()
    parent = models.CharField(blank=True, null=True)
    position = models.CharField()
    notes = models.CharField(
        max_length=8192,
        blank=True,
        null=True,
    )
    status = models.CharField(
        max_length=32,
        choices=TASK_STATUSES,
    )
    due = models.DateTimeField(
        null=True,
        blank=True,
    )
    completed = models.DateTimeField(
        null=True,
        blank=True,
    )
    deleted = models.BooleanField(default=False)
    hidden = models.BooleanField(default=False)
    web_view_link = models.URLField()
    owner_id = models.UUIDField(default=uuid.uuid4, editable=True)
