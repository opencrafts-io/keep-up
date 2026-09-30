"""Local agenda lifecycle with optional Google Tasks mirrors."""

from datetime import date, datetime, time

from django.db import transaction
from django.utils import timezone

from agenda.models import Event
from todos.services.task_service import TaskService


class AgendaEventService:
    """Own agenda persistence and delegate task synchronization to todos."""

    @staticmethod
    @transaction.atomic
    def create_event(owner_id, **event_fields) -> Event:
        event = Event.objects.create(owner_id=owner_id, **event_fields)
        AgendaEventService._sync_task_mirror(event)
        return event

    @staticmethod
    def get_event(owner_id, event_id) -> Event:
        return Event.objects.select_related("task").get(
            owner_id=owner_id, id=event_id
        )

    @staticmethod
    def list_events(owner_id, start_date=None, end_date=None):
        events = Event.objects.filter(owner_id=owner_id).select_related("task")
        start = AgendaEventService._parse_filter_datetime(start_date)
        end = AgendaEventService._parse_filter_datetime(end_date, end_of_day=True)
        if start:
            events = events.filter(start_time__gte=start)
        if end:
            events = events.filter(end_time__lte=end)
        return events.order_by("start_time")

    @staticmethod
    def sync_existing_events(owner_id) -> int:
        """Queue mirrors for active legacy events that do not have one yet."""
        event_ids = list(
            Event.objects.filter(owner_id=owner_id, task__isnull=True)
            .exclude(status="cancelled")
            .values_list("id", flat=True)
        )
        queued = 0
        for event_id in event_ids:
            AgendaEventService.ensure_task_mirror(owner_id, event_id)
            queued += 1
        return queued

    @staticmethod
    @transaction.atomic
    def ensure_task_mirror(owner_id, event_id) -> Event:
        event = Event.objects.select_for_update().get(owner_id=owner_id, id=event_id)
        if not event.task_id:
            AgendaEventService._sync_task_mirror(event)
        return event

    @staticmethod
    @transaction.atomic
    def update_event(owner_id, event_id, **updates) -> Event:
        event = Event.objects.select_for_update().get(owner_id=owner_id, id=event_id)
        for field, value in updates.items():
            setattr(event, field, value)
        event.save()
        AgendaEventService._sync_task_mirror(event)
        return event

    @staticmethod
    @transaction.atomic
    def delete_event(owner_id, event_id) -> None:
        event = Event.objects.select_for_update().get(owner_id=owner_id, id=event_id)
        if event.task_id and not event.task.deleted:
            TaskService.delete_task(owner_id, str(event.task_id))
        event.delete()

    @staticmethod
    def _sync_task_mirror(event: Event) -> None:
        """Queue a local-first Task mirror; the todo worker handles Google."""
        task = event.task if event.task_id else None

        if event.status == "cancelled":
            if task and not task.deleted:
                TaskService.delete_task(event.owner_id, str(task.id))
            return

        if task and not task.deleted:
            TaskService.update_task(
                owner_id=str(event.owner_id),
                task_id=str(task.id),
                title=event.summary,
                notes=AgendaEventService._task_notes(event),
                due=event.start_time,
            )
            return

        task = TaskService.create_task(
            owner_id=str(event.owner_id),
            title=event.summary,
            notes=AgendaEventService._task_notes(event),
            due=event.start_time,
        )
        event.task = task
        event.save(update_fields=["task", "updated"])

    @staticmethod
    def _task_notes(event: Event) -> str:
        """Keep the local description intact and fit the Tasks notes field."""
        return (event.description or "")[:8192]

    @staticmethod
    def _parse_filter_datetime(value, end_of_day=False):
        if not value:
            return None
        try:
            if len(value) == 10:
                parsed_date = date.fromisoformat(value)
                parsed = datetime.combine(
                    parsed_date, time.max if end_of_day else time.min
                )
            else:
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except (AttributeError, TypeError, ValueError):
            return None
        if timezone.is_naive(parsed):
            return timezone.make_aware(parsed, timezone.get_current_timezone())
        return parsed
