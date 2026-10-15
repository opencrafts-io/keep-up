"""
Adapter over the Google Tasks API.

The only module in this project that imports googleapiclient. Everything above
it works with plain dicts and GoogleTasksError, which is what lets the sync
worker be tested without touching HTTP or holding a credential.

Credentials are built from a brokered access token alone: there is no refresh
token here by design, because Verisafe owns refresh. A token is used for one
worker run and discarded.
"""

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from .models import Task, TaskStatus

logger = logging.getLogger("keep_up")

# Fields Google owns. Everything else on the model - priority, tags, color -
# is local only per the model docstring and is never sent.
GOOGLE_TASK_FIELDS = ("title", "notes", "status", "due", "completed")

# tasks.list defaults to 20 per page and caps at 100; tasklists.list caps at
# 1000. Both are paged to exhaustion.
MAX_TASKS_PER_PAGE = 100
MAX_LISTS_PER_PAGE = 1000


class GoogleTasksError(Exception):
    """
    An error response from the Google Tasks API.

    Carries the HTTP status so callers can decide whether to retry, park the
    record, or give up, without importing googleapiclient themselves.
    """

    def __init__(self, status: int, message: str) -> None:
        self.status = status
        super().__init__(f"Google Tasks API error {status}: {message}")


def task_to_body(task: Task) -> Dict[str, Any]:
    """
    Map a local Task onto a Google Tasks resource body.

    Google stores only the date part of `due` and ignores the time, so it is
    sent as midnight UTC rather than pretending the time survives.
    """
    body: Dict[str, Any] = {"title": task.title, "status": task.status}

    if task.notes:
        body["notes"] = task.notes

    if task.due:
        body["due"] = f"{task.due.date().isoformat()}T00:00:00.000Z"

    if task.status == TaskStatus.COMPLETED and task.completed:
        body["completed"] = task.completed.isoformat()

    return body


def parse_google_datetime(value: Optional[str]) -> Optional[datetime]:
    """Parse an RFC 3339 timestamp, tolerating the trailing Z."""
    if not value:
        return None

    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        logger.warning("Unparseable timestamp from Google Tasks: %r", value)
        return None

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)

    return parsed


def to_updated_min(moment: datetime) -> str:
    """Format a watermark the way the Tasks API expects it."""
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def remote_to_task_fields(remote: Dict[str, Any]) -> Dict[str, Any]:
    """
    Map a Google task resource onto local model fields.

    The inverse of task_to_body. Local-only fields are absent on purpose: a
    pull must never blank priority, tags, or colour, because Google has no
    concept of them and would otherwise appear to clear them on every poll.
    """
    return {
        "title": remote.get("title") or "",
        "notes": remote.get("notes") or None,
        "status": remote.get("status") or TaskStatus.NEEDS_ACTION,
        "due": parse_google_datetime(remote.get("due")),
        "completed": parse_google_datetime(remote.get("completed")),
        "position": remote.get("position") or "",
        "hidden": bool(remote.get("hidden", False)),
        "deleted": bool(remote.get("deleted", False)),
        "etag": remote.get("etag") or "",
    }


class GoogleTasksClient:
    """
    Thin wrapper over the tasks v1 service.

    Deletes are idempotent: a 404 means the remote object is already gone,
    which is the outcome the caller wanted.
    """

    def __init__(self, access_token: str, service: Optional[Any] = None) -> None:
        self._service = service or build(
            "tasks",
            "v1",
            credentials=Credentials(token=access_token),
            cache_discovery=False,
        )

    # --- task lists ---------------------------------------------------------

    def create_list(self, title: str) -> Dict[str, Any]:
        return self._execute(self._service.tasklists().insert(body={"title": title}))

    def update_list(self, external_id: str, title: str) -> Dict[str, Any]:
        return self._execute(
            self._service.tasklists().patch(
                tasklist=external_id, body={"title": title}
            )
        )

    def delete_list(self, external_id: str) -> None:
        self._execute(
            self._service.tasklists().delete(tasklist=external_id), missing_ok=True
        )

    # --- tasks --------------------------------------------------------------

    def create_task(
        self,
        list_external_id: str,
        body: Dict[str, Any],
        parent: Optional[str] = None,
    ) -> Dict[str, Any]:
        kwargs: Dict[str, Any] = {"tasklist": list_external_id, "body": body}
        if parent:
            # Google takes the parent as a query parameter, not a body field.
            kwargs["parent"] = parent
        return self._execute(self._service.tasks().insert(**kwargs))

    def update_task(
        self,
        list_external_id: str,
        task_external_id: str,
        body: Dict[str, Any],
    ) -> Dict[str, Any]:
        return self._execute(
            self._service.tasks().patch(
                tasklist=list_external_id, task=task_external_id, body=body
            )
        )

    def delete_task(self, list_external_id: str, task_external_id: str) -> None:
        self._execute(
            self._service.tasks().delete(
                tasklist=list_external_id, task=task_external_id
            ),
            missing_ok=True,
        )

    # --- reads ---------------------------------------------------------------

    def list_lists(self) -> List[Dict[str, Any]]:
        """
        Every task list the user has.

        tasklists.list takes no incremental filter, so this is always a full
        enumeration.
        """
        items: List[Dict[str, Any]] = []
        page_token = None

        while True:
            kwargs: Dict[str, Any] = {"maxResults": MAX_LISTS_PER_PAGE}
            if page_token:
                kwargs["pageToken"] = page_token

            response = self._execute(self._service.tasklists().list(**kwargs))
            items.extend(response.get("items") or [])

            page_token = response.get("nextPageToken")
            if not page_token:
                return items

    def list_tasks(
        self,
        list_external_id: str,
        updated_min: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Tasks in one list, optionally only those touched since updated_min.

        Deleted and hidden tasks are requested explicitly: without those flags
        a deletion at Google is invisible, and there is no other way to learn
        about it.
        """
        items: List[Dict[str, Any]] = []
        page_token = None

        while True:
            kwargs: Dict[str, Any] = {
                "tasklist": list_external_id,
                "maxResults": MAX_TASKS_PER_PAGE,
                "showDeleted": True,
                "showHidden": True,
                "showCompleted": True,
            }
            if updated_min:
                kwargs["updatedMin"] = updated_min
            if page_token:
                kwargs["pageToken"] = page_token

            response = self._execute(self._service.tasks().list(**kwargs))
            items.extend(response.get("items") or [])

            page_token = response.get("nextPageToken")
            if not page_token:
                return items

    # --- plumbing -----------------------------------------------------------

    @staticmethod
    def _execute(request: Any, missing_ok: bool = False) -> Dict[str, Any]:
        try:
            return request.execute() or {}
        except HttpError as exc:
            status = _status_of(exc)
            if missing_ok and status == 404:
                logger.info("Google Tasks object already gone, treating as deleted")
                return {}
            raise GoogleTasksError(status, str(exc)) from exc


def _status_of(exc: HttpError) -> int:
    """Read the status off an HttpError across googleapiclient versions."""
    status = getattr(exc, "status_code", None)
    if status is None:
        status = getattr(getattr(exc, "resp", None), "status", None)
    return int(status or 0)
