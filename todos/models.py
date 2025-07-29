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

    id = models.CharField(
        max_length=255,
        primary_key=True,
    )
    kind = models.CharField(
        max_length=3,
    )
    etag = models.CharField()
    title = models.CharField(max_length=1024)
    updated = models.DateTimeField(
        auto_created=True,
        auto_now_add=True,
    )
    self_link = models.URLField()
    parent = models.CharField()
    position = models.CharField()
    notes = models.CharField(
        max_length=8192,
    )
    status = models.CharField(
        max_length=32,
        choices=TASK_STATUSES,
    )
    due = models.DateTimeField(
        auto_now_add=True,
    )
    completed = models.DateTimeField()
    deleted = models.BooleanField(default=False)
    hidden = models.BooleanField(default=False)
    web_view_link = models.URLField()
