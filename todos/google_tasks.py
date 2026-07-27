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
from typing import Any, Dict, Optional

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from .models import Task, TaskStatus

logger = logging.getLogger("keep_up")

# Fields Google owns. Everything else on the model - priority, tags, color -
# is local only per the model docstring and is never sent.
GOOGLE_TASK_FIELDS = ("title", "notes", "status", "due", "completed")


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
