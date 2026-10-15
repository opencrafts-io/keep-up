import uuid
from django.db import models
from safedelete.models import SafeDeleteModel
from safedelete.config import SOFT_DELETE_CASCADE


class Event(SafeDeleteModel):
    """
    Locally owned agenda event with an optional Google Tasks mirror.

    Agenda remains the source of truth. The linked task mirrors the title,
    description and start date through the existing asynchronous Tasks sync
    pipeline; richer event fields remain local.
    
    Attributes:
        id (str): Local event identifier
        summary (str): Event title/summary (max 1024 characters)
        description (str): Detailed event description (optional)
        location (str): Event location (optional, max 1024 characters)
        start_time (datetime): Event start date and time
        end_time (datetime): Event end date and time
        all_day (bool): Whether this is an all-day event
        timezone (str): Event timezone (defaults to UTC)
        status (str): Event status - confirmed, tentative, or cancelled
        transparency (str): Whether event blocks time on calendar (opaque/transparent)
        calendar_id (str): Google Calendar ID (defaults to 'primary')
        html_link (str): Legacy Google Calendar link, retained for API compatibility
        created (datetime): When the local event was created
        updated (datetime): When the local event was last updated
        etag (str): Legacy Google Calendar ETag, retained for API compatibility
        attendees (list): List of event attendees (stored as JSON)
        reminders (dict): Event reminder settings (stored as JSON)
        recurrence (list): Recurrence rules for repeating events (stored as JSON)
        owner_id (UUID): User who owns this event
    
    Example:
        # Create a birthday event
        from datetime import datetime, timezone
        import uuid
        
        birthday_event = Event.objects.create(
            id="birthday_john_2025",
            summary="John's Birthday",
            description="Celebrating John's 30th birthday!",
            location="123 Main St, New York, NY",
            start_time=datetime(2025, 8, 20, 18, 0, 0, tzinfo=timezone.utc),
            end_time=datetime(2025, 8, 20, 22, 0, 0, tzinfo=timezone.utc),
            all_day=False,
            timezone="America/New_York",
            status="confirmed",
            transparency="opaque",
            calendar_id="primary",
            html_link="https://calendar.google.com/event?eid=birthday_john",
            created=datetime.now(timezone.utc),
            updated=datetime.now(timezone.utc),
            etag='"birthday_john_etag"',
            attendees=[
                {"email": "john@example.com", "displayName": "John Doe"},
                {"email": "jane@example.com", "displayName": "Jane Smith"}
            ],
            reminders={
                "useDefault": False,
                "overrides": [
                    {"method": "email", "minutes": 24 * 60},  # 1 day before
                    {"method": "popup", "minutes": 60}  # 1 hour before
                ]
            },
            recurrence=["RRULE:FREQ=YEARLY"],  # Repeats yearly
            owner_id=uuid.UUID("user-uuid-here")
        )
    
    Note:
        - The model automatically handles soft deletes using django-safedelete
        - Events are ordered by start_time by default
        - The optional task relation carries Google Tasks sync state
        - Legacy Calendar metadata remains for stored data and API compatibility
    """
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

    # Local identifier; existing Google Calendar IDs remain valid after migration.
    id = models.CharField(
        max_length=255,
        primary_key=True,
        default=uuid.uuid4,
        help_text="Local agenda event ID"
    )

    # Event details
    summary = models.CharField(
        max_length=1024,
        help_text="Event title or summary"
    )
    description = models.TextField(
        blank=True, 
        null=True,
        help_text="Detailed event description"
    )
    location = models.CharField(
        max_length=1024, 
        blank=True, 
        null=True,
        help_text="Event location"
    )

    # Time information
    start_time = models.DateTimeField(
        help_text="Event start date and time"
    )
    end_time = models.DateTimeField(
        help_text="Event end date and time"
    )
    all_day = models.BooleanField(
        default=False,
        help_text="Whether this is an all-day event"
    )
    timezone = models.CharField(
        max_length=64, 
        default="UTC",
        help_text="Event timezone"
    )

    # Event metadata
    status = models.CharField(
        max_length=32,
        choices=EVENT_STATUSES,
        default="confirmed",
        help_text="Event status"
    )
    transparency = models.CharField(
        max_length=32,
        choices=TRANSPARENCY_OPTIONS,
        default="opaque",
        help_text="Whether event blocks time on calendar"
    )

    # Legacy Google Calendar metadata retained to avoid breaking existing clients.
    calendar_id = models.CharField(
        max_length=255, 
        default="primary",
        help_text="Legacy Google Calendar ID"
    )
    html_link = models.URLField(
        blank=True,
        default="",
        help_text="Legacy URL to view event in Google Calendar"
    )
    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)
    etag = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text="Legacy Google Calendar ETag"
    )

    # Attendees (stored as JSON string for simplicity)
    attendees = models.JSONField(
        default=list, 
        blank=True,
        help_text="List of event attendees"
    )

    # Reminders
    reminders = models.JSONField(
        default=dict, 
        blank=True,
        help_text="Event reminder settings"
    )

    # Recurrence
    recurrence = models.JSONField(
        default=list, 
        blank=True,
        help_text="Recurrence rules for repeating events"
    )

    # Owner
    owner_id = models.UUIDField(
        default=uuid.uuid4, 
        editable=False,
        help_text="User who owns this event"
    )

    # The Tasks model owns its own sync status and lifecycle. Keeping the link
    # here lets agenda reuse that worker without duplicating OAuth or retries.
    task = models.OneToOneField(
        "todos.Task",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="agenda_event",
    )

    class Meta:
        ordering = ["start_time"]
        verbose_name = "Calendar Event"
        verbose_name_plural = "Calendar Events"
        indexes = [
            models.Index(fields=['owner_id', 'start_time']),
            models.Index(fields=['start_time', 'end_time']),
        ]

    def __str__(self):
        """Return a string representation of the event."""
        return f"{self.summary} ({self.start_time})"

    def duration(self):
        """
        Calculate the duration of the event.
        
        Returns:
            timedelta: The duration between start_time and end_time
        """
        return self.end_time - self.start_time

    def is_recurring(self):
        """
        Check if the event is recurring.
        
        Returns:
            bool: True if the event has recurrence rules, False otherwise
        """
        return bool(self.recurrence)

    def get_attendee_emails(self):
        """
        Get a list of attendee email addresses.
        
        Returns:
            list: List of email addresses for all attendees
        """
        return [attendee.get('email') for attendee in self.attendees if attendee.get('email')]

    def add_attendee(self, email, display_name=None):
        """
        Add an attendee to the event.
        
        Args:
            email (str): Attendee's email address
            display_name (str, optional): Attendee's display name
        """
        attendee = {"email": email}
        if display_name:
            attendee["displayName"] = display_name
        self.attendees.append(attendee)
        self.save(update_fields=['attendees'])

    def is_all_day(self):
        """
        Check if this is an all-day event.
        
        Returns:
            bool: True if all_day is True, False otherwise
        """
        return self.all_day

    def get_recurrence_pattern(self):
        """
        Get the recurrence pattern as a human-readable string.
        
        Returns:
            str: Human-readable recurrence pattern or "No recurrence"
        """
        if not self.recurrence:
            return "No recurrence"
        
        # Parse common RRULE patterns
        for rule in self.recurrence:
            if "FREQ=DAILY" in rule:
                return "Daily"
            elif "FREQ=WEEKLY" in rule:
                return "Weekly"
            elif "FREQ=MONTHLY" in rule:
                return "Monthly"
            elif "FREQ=YEARLY" in rule:
                return "Yearly"
        
        return "Custom recurrence"
