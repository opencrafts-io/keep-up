import uuid
from django.db import models
from safedelete.models import SafeDeleteModel
from safedelete.config import SOFT_DELETE_CASCADE


class Event(SafeDeleteModel):
    _safedelete_policy = SOFT_DELETE_CASCADE

    EVENT_STATUSES = {
        "confirmed": "Confirmed",
        "tentative": "Tentative",
        "cancelled": "Cancelled",
    }

    TRANSPARENCY_OPTIONS = {
        "opaque": "Opaque (blocks time on calendar)",
        "transparent": "Transparent (doesn't block time)",
    }

    # Google Calendar Event ID
    id = models.CharField(
        max_length=255,
        primary_key=True,
    )

    # Event details
    summary = models.CharField(max_length=1024)
    description = models.TextField(blank=True, null=True)
    location = models.CharField(max_length=1024, blank=True, null=True)

    # Time information
    start_time = models.DateTimeField()
    end_time = models.DateTimeField()
    all_day = models.BooleanField(default=False)
    timezone = models.CharField(max_length=64, default="UTC")

    # Event metadata
    status = models.CharField(
        max_length=32,
        choices=EVENT_STATUSES,
        default="confirmed",
    )
    transparency = models.CharField(
        max_length=32,
        choices=TRANSPARENCY_OPTIONS,
        default="opaque",
    )

    # Google Calendar specific fields
    calendar_id = models.CharField(max_length=255, default="primary")
    html_link = models.URLField()
    created = models.DateTimeField()
    updated = models.DateTimeField()
    etag = models.CharField(max_length=255)

    # Attendees (stored as JSON string for simplicity)
    attendees = models.JSONField(default=list, blank=True)

    # Reminders
    reminders = models.JSONField(default=dict, blank=True)

    # Recurrence
    recurrence = models.JSONField(default=list, blank=True)

    # Owner
    owner_id = models.UUIDField(default=uuid.uuid4, editable=True)

    class Meta:
        ordering = ["start_time"]

    def __str__(self):
        return f"{self.summary} ({self.start_time})"
