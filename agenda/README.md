# Agenda App Documentation

## Overview

The Agenda app provides calendar event management with Google Calendar integration. It allows users to create, read, update, and delete calendar events that are synchronized with their Google Calendar.

## Event Model

The `Event` model represents calendar events and integrates with Google Calendar API. It inherits from `SafeDeleteModel` to provide soft delete functionality.

### Key Features

- **Google Calendar Integration**: Full synchronization with Google Calendar API
- **Soft Deletes**: Events are preserved when deleted using django-safedelete
- **Recurring Events**: Support for recurring events with RRULE patterns
- **Attendees**: Multiple attendees with email and display names
- **Reminders**: Customizable reminder settings
- **Timezone Support**: Proper timezone handling

### Model Fields

| Field | Type | Description | Example |
|-------|------|-------------|---------|
| `id` | CharField | Google Calendar Event ID | `"event_123456"` |
| `summary` | CharField | Event title/summary | `"Team Meeting"` |
| `description` | TextField | Event description | `"Weekly team sync"` |
| `location` | CharField | Event location | `"Conference Room A"` |
| `start_time` | DateTimeField | Event start time | `2025-08-20T10:00:00Z` |
| `end_time` | DateTimeField | Event end time | `2025-08-20T11:00:00Z` |
| `all_day` | BooleanField | All-day event flag | `False` |
| `timezone` | CharField | Event timezone | `"America/New_York"` |
| `status` | CharField | Event status | `"confirmed"` |
| `transparency` | CharField | Calendar transparency | `"opaque"` |
| `attendees` | JSONField | Event attendees | `[{"email": "user@example.com"}]` |
| `reminders` | JSONField | Reminder settings | `{"useDefault": true}` |
| `recurrence` | JSONField | Recurrence rules | `["RRULE:FREQ=WEEKLY"]` |

## API Endpoints

### Create Event
```bash
POST /agenda/add
```

### List Events
```bash
GET /agenda/
```

### Update Event
```bash
PUT /agenda/update/<event_id>
```

### Delete Event
```bash
DELETE /agenda/delete/<event_id>
```

### Health Check
```bash
GET /agenda/ping
```

## Examples

### 1. Create a Simple Meeting

```bash
http POST "http://localhost:8000/agenda/add" \
  "Authorization: Bearer YOUR_JWT_TOKEN" \
  summary="Team Standup" \
  description="Daily team standup meeting" \
  location="Conference Room A" \
  start_time="2025-08-20T09:00:00Z" \
  end_time="2025-08-20T09:30:00Z" \
  timezone="America/New_York"
```

### 2. Create a Birthday Event

```bash
http POST "http://localhost:8000/agenda/add" \
  "Authorization: Bearer YOUR_JWT_TOKEN" \
  summary="John's Birthday" \
  description="Celebrating John's 30th birthday!" \
  location="123 Main St, New York, NY" \
  start_time="2025-08-20T18:00:00Z" \
  end_time="2025-08-20T22:00:00Z" \
  timezone="America/New_York" \
  attendees:='[{"email": "john@example.com", "displayName": "John Doe"}]' \
  reminders:='{"useDefault": false, "overrides": [{"method": "email", "minutes": 1440}]}' \
  recurrence:='["RRULE:FREQ=YEARLY"]'
```

### 3. Create an All-Day Event

```bash
http POST "http://localhost:8000/agenda/add" \
  "Authorization: Bearer YOUR_JWT_TOKEN" \
  summary="Company Holiday" \
  description="Office closed for holiday" \
  start_time="2025-12-25T00:00:00Z" \
  end_time="2025-12-25T23:59:59Z" \
  all_day=true \
  timezone="America/New_York"
```

### 4. Create a Recurring Weekly Meeting

```bash
http POST "http://localhost:8000/agenda/add" \
  "Authorization: Bearer YOUR_JWT_TOKEN" \
  summary="Weekly Team Meeting" \
  description="Weekly team sync and planning" \
  location="Zoom Meeting" \
  start_time="2025-08-20T14:00:00Z" \
  end_time="2025-08-20T15:00:00Z" \
  timezone="America/New_York" \
  attendees:='[{"email": "team@company.com", "displayName": "Team Members"}]' \
  reminders:='{"useDefault": true}' \
  recurrence:='["RRULE:FREQ=WEEKLY;BYDAY=WE"]'
```

### 5. List Events with Date Filtering

```bash
# List events for a specific date range
http GET "http://localhost:8000/agenda/" \
  "Authorization: Bearer YOUR_JWT_TOKEN" \
  start_date="2025-08-01" \
  end_date="2025-08-31"

# Sync with Google Calendar
http GET "http://localhost:8000/agenda/" \
  "Authorization: Bearer YOUR_JWT_TOKEN" \
  sync=true
```

## Python Code Examples

### Create Event Programmatically

```python
from datetime import datetime, timezone
from agenda.models import Event
import uuid

# Create a simple meeting
meeting = Event.objects.create(
    id="meeting_123",
    summary="Project Review",
    description="Review project progress and next steps",
    location="Conference Room B",
    start_time=datetime(2025, 8, 20, 14, 0, 0, tzinfo=timezone.utc),
    end_time=datetime(2025, 8, 20, 15, 0, 0, tzinfo=timezone.utc),
    all_day=False,
    timezone="America/New_York",
    status="confirmed",
    transparency="opaque",
    calendar_id="primary",
    html_link="https://calendar.google.com/event?eid=meeting_123",
    created=datetime.now(timezone.utc),
    updated=datetime.now(timezone.utc),
    etag='"meeting_123_etag"',
    attendees=[
        {"email": "alice@company.com", "displayName": "Alice Johnson"},
        {"email": "bob@company.com", "displayName": "Bob Smith"}
    ],
    reminders={
        "useDefault": False,
        "overrides": [
            {"method": "popup", "minutes": 15}
        ]
    },
    recurrence=[],
    owner_id=uuid.UUID("your-user-uuid")
)
```

### Create Birthday Event Function

```python
def create_birthday_event(user_id, person_name, birth_date, location=None):
    """
    Create a recurring birthday event.
    
    Args:
        user_id (str): User's UUID
        person_name (str): Name of the person
        birth_date (datetime): Birthday date
        location (str, optional): Event location
    """
    event_data = {
        "id": f"birthday_{person_name.lower().replace(' ', '_')}",
        "summary": f"{person_name}'s Birthday",
        "description": f"Happy Birthday {person_name}! 🎉",
        "location": location or "Birthday Celebration",
        "start_time": birth_date.replace(hour=18, minute=0, second=0, microsecond=0),
        "end_time": birth_date.replace(hour=22, minute=0, second=0, microsecond=0),
        "all_day": False,
        "timezone": "UTC",
        "status": "confirmed",
        "transparency": "opaque",
        "calendar_id": "primary",
        "html_link": f"https://calendar.google.com/event?eid=birthday_{person_name.lower().replace(' ', '_')}",
        "created": datetime.now(timezone.utc),
        "updated": datetime.now(timezone.utc),
        "etag": f"\"birthday_{person_name.lower().replace(' ', '_')}\"",
        "attendees": [
            {"email": f"{person_name.lower().replace(' ', '.')}@example.com", "displayName": person_name}
        ],
        "reminders": {
            "useDefault": False,
            "overrides": [
                {"method": "email", "minutes": 24 * 60},  # 1 day before
                {"method": "popup", "minutes": 60}  # 1 hour before
            ]
        },
        "recurrence": ["RRULE:FREQ=YEARLY"],  # Repeats yearly
        "owner_id": uuid.UUID(user_id)
    }
    
    return Event.objects.create(**event_data)

# Usage
birthday = create_birthday_event(
    user_id="your-user-uuid",
    person_name="John Doe",
    birth_date=datetime(1995, 8, 20, tzinfo=timezone.utc),
    location="123 Main St, New York, NY"
)
```

## Recurrence Rules (RRULE)

Common recurrence patterns:

- **Daily**: `RRULE:FREQ=DAILY`
- **Weekly**: `RRULE:FREQ=WEEKLY;BYDAY=MO` (every Monday)
- **Monthly**: `RRULE:FREQ=MONTHLY;BYMONTHDAY=15` (15th of every month)
- **Yearly**: `RRULE:FREQ=YEARLY;BYMONTH=8;BYMONTHDAY=20` (August 20th every year)
- **Weekdays**: `RRULE:FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR` (weekdays only)

## Reminder Settings

```python
# Default reminders
reminders = {"useDefault": True}

# Custom reminders
reminders = {
    "useDefault": False,
    "overrides": [
        {"method": "email", "minutes": 24 * 60},  # 1 day before
        {"method": "popup", "minutes": 60},        # 1 hour before
        {"method": "popup", "minutes": 15}         # 15 minutes before
    ]
}
```

## Error Handling

The API returns appropriate HTTP status codes:

- `200`: Success
- `201`: Event created
- `400`: Bad request (missing required fields)
- `403`: Forbidden (invalid token)
- `404`: Event not found
- `500`: Internal server error

## Authentication

All endpoints require a valid JWT token in the Authorization header:

```
Authorization: Bearer YOUR_JWT_TOKEN
```

The token must be associated with a user who has linked their Google account.
