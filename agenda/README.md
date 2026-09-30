# Agenda

Agenda stores calendar-style events locally. Its API views handle HTTP and
serialization; `AgendaEventService` owns event persistence and lifecycle.

## Google Tasks sync

New active agenda events have a linked local `todos.Task`. Agenda maps the
event summary to the task title, description to notes, and start time to the
due date. Task notes are capped at 8,192 characters; the full description
remains on the local event. The existing todo worker sends those task fields
to Google Tasks when the user's Google account has granted the Tasks capability.

Google Tasks stores due dates without the time of day. Location, end time,
timezone, attendees, reminders, and recurrence remain on the agenda event and
are not copied to Google Tasks. Agenda is the source of truth; Google Tasks
changes are not pulled back into the agenda event.

The mirror uses the normal task sync states: `pending`, `synced`, `skipped`, or
`failed`. An account without a Tasks grant remains usable locally. Its task is
parked as `skipped` and can be retried by the existing sync sweep after the
user connects Google. Agenda responses include the mirror's `sync_status`.

## Endpoints

All endpoints require a Verisafe JWT. The authenticated account owns the
events; request data cannot assign an event to another owner.

| Method | Path | Behavior |
| --- | --- | --- |
| `POST` | `/agenda/add` | Create locally and queue a Tasks mirror |
| `GET` | `/agenda/` | List local events; accepts `start_date`, `end_date`, `page`, `page_size`, and `sync=true` |
| `PUT` or `PATCH` | `/agenda/update/<event_id>` | Update locally and queue mirror changes |
| `DELETE` | `/agenda/delete/<event_id>` | Soft-delete locally and queue mirror deletion |

Example create request:

```json
{
  "summary": "Project review",
  "description": "Review progress and next steps",
  "location": "Conference Room B",
  "start_time": "2026-10-05T14:00:00Z",
  "end_time": "2026-10-05T15:00:00Z",
  "timezone": "Africa/Nairobi"
}
```

Event changes are committed before a background sync is queued. A temporary
broker or Google outage does not discard the local event; the existing todo
sync retry and sweep handle delivery.

For events already stored before this migration, call `GET /agenda/?sync=true`
once to create any missing local task mirrors and queue them for Google Tasks.
The endpoint is idempotent: events that already have a mirror are left alone.
