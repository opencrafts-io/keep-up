"""
Celery tasks that push local changes to Google Tasks.

One-way: the local database is the source of truth and nothing is read back
except the ids, etags, and positions Google assigns. A user who has not linked
Google is a normal outcome, not a failure - their records are parked as
SKIPPED and revisited by the sweep.

Access tokens are fetched from the Verisafe broker per run and never stored.
"""

import logging
import random
from dataclasses import dataclass
from datetime import timedelta
from typing import Callable, List, Optional

from celery import shared_task
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from verisafe.exceptions import (
    BrokerCredentialsRejected,
    NeedsAuthorization,
    ProviderDown,
)
from verisafe.token_broker import CAPABILITY_TASKS, PROVIDER_GOOGLE, TokenBroker

from .google_tasks import (
    GoogleTasksClient,
    GoogleTasksError,
    remote_to_task_fields,
    task_to_body,
    to_updated_min,
)
from .models import GoogleSyncState, SyncStatus, Task, TaskList
from .services.task_list_service import TaskListService
from .services.task_service import TaskService

logger = logging.getLogger("keep_up")

MAX_RETRIES = 5
BASE_BACKOFF_SECONDS = 30
MAX_BACKOFF_SECONDS = 3600
# A rejected api key is our misconfiguration, not a per-record problem, so it
# backs off hard rather than burning retries in a tight loop.
CREDENTIALS_BACKOFF_SECONDS = 600

SYNCED = "synced"
SKIPPED = "skipped"
FAILED = "failed"
RETRY = "retry"
UNAVAILABLE = "unavailable"


@dataclass
class Outcome:
    """
    What a sync attempt decided, resolved after its transaction closes.

    Retries are raised outside the transaction on purpose: `self.retry` raises,
    and raising inside `atomic()` would roll back writes the retry depends on,
    such as clearing a stale external_id before re-inserting.
    """

    status: str
    retry_after: Optional[int] = None
    error: Optional[Exception] = None


def _backoff(retries: int, base: int = BASE_BACKOFF_SECONDS) -> int:
    """Exponential backoff with jitter, so retries don't align into spikes."""
    delay = min(base * (2**retries), MAX_BACKOFF_SECONDS)
    return int(delay * random.uniform(0.8, 1.2))


def _client_for(owner_id) -> GoogleTasksClient:
    """Fetch a brokered access token and wrap it in a client. Never cached."""
    token = TokenBroker().token(PROVIDER_GOOGLE, str(owner_id), [CAPABILITY_TASKS])
    return GoogleTasksClient(token.access_token)


def _classify(exc: GoogleTasksError) -> str:
    """Decide what a Google error means for the record that caused it."""
    if exc.status in (401, 403):
        # The token was accepted by the broker but rejected at use: revoked
        # between calls, or the scope is not really there.
        return "reauthorize"
    if exc.status == 404:
        # Whatever we tried to update is gone at Google. Recreate it.
        return "recreate"
    if exc.status == 429 or 500 <= exc.status < 600:
        return RETRY
    # 400 and friends: the request is wrong and will stay wrong.
    return FAILED


def _resolve(celery_task, outcome: Outcome, on_exhausted: Callable[[str], None]) -> str:
    """Raise the retry, or report the terminal status."""
    if outcome.status != RETRY:
        return outcome.status

    # Checked explicitly rather than catching MaxRetriesExceededError, because
    # retry(exc=...) re-raises the original exception once the budget is spent
    # and the record would never be marked.
    if celery_task.request.retries >= MAX_RETRIES:
        reason = f"gave up after {MAX_RETRIES} retries: {outcome.error}"
        logger.error(reason)
        on_exhausted(reason)
        return FAILED

    raise celery_task.retry(exc=outcome.error, countdown=outcome.retry_after)


def _broker_failure(celery_task, exc: Exception) -> Optional[Outcome]:
    """
    Translate a token broker failure into an outcome.

    Returns None when the exception is not a broker failure.
    """
    if isinstance(exc, NeedsAuthorization):
        return Outcome(SKIPPED, error=exc)
    if isinstance(exc, ProviderDown):
        return Outcome(
            RETRY, retry_after=_backoff(celery_task.request.retries), error=exc
        )
    if isinstance(exc, BrokerCredentialsRejected):
        logger.error(
            "Token broker rejected our service credentials - is the "
            "oauth-token-broker role assigned to this bot account? %s",
            exc,
        )
        return Outcome(RETRY, retry_after=CREDENTIALS_BACKOFF_SECONDS, error=exc)
    return None


# ---------------------------------------------------------------------------
# Task lists
# ---------------------------------------------------------------------------


@shared_task(bind=True, max_retries=MAX_RETRIES, ignore_result=True)
def sync_task_list(self, list_id: str) -> str:
    """Push one task list to Google Tasks."""
    outcome = _sync_task_list_once(self, list_id)
    return _resolve(
        self,
        outcome,
        lambda reason: TaskListService.mark_sync_failed(list_id, reason),
    )


def _sync_task_list_once(celery_task, list_id: str) -> Outcome:
    with transaction.atomic():
        task_list = (
            TaskList.objects.select_for_update(skip_locked=True)
            .filter(id=list_id)
            .first()
        )
        if task_list is None:
            logger.info(
                "Task list %s is gone or already being synced elsewhere", list_id
            )
            return Outcome(UNAVAILABLE)

        try:
            client = _client_for(task_list.owner_id)
        except Exception as exc:
            outcome = _broker_failure(celery_task, exc)
            if outcome is None:
                raise
            if outcome.status == SKIPPED:
                TaskListService.mark_skipped(task_list.id, str(exc))
            return outcome

        try:
            return _push_list(task_list, client)
        except GoogleTasksError as exc:
            return _handle_list_google_error(celery_task, task_list, exc)


def _push_list(task_list: TaskList, client: GoogleTasksClient) -> Outcome:
    if task_list.deleted:
        if task_list.external_id:
            client.delete_list(task_list.external_id)
        TaskListService.mark_synced(
            task_list.id, task_list.external_id, task_list.etag
        )
        # Google removes a list's tasks server-side, so settle them locally
        # rather than issuing a delete per task.
        task_list.tasks.update(
            sync_status=SyncStatus.SYNCED, last_synced_at=timezone.now()
        )
        return Outcome(SYNCED)

    if task_list.external_id:
        remote = client.update_list(task_list.external_id, task_list.title)
    else:
        remote = client.create_list(task_list.title)

    TaskListService.mark_synced(
        task_list.id,
        remote.get("id") or task_list.external_id,
        remote.get("etag", ""),
    )
    return Outcome(SYNCED)


def _handle_list_google_error(
    celery_task, task_list: TaskList, exc: GoogleTasksError
) -> Outcome:
    action = _classify(exc)

    if action == RETRY:
        return Outcome(
            RETRY, retry_after=_backoff(celery_task.request.retries), error=exc
        )

    if action == "reauthorize":
        # One more attempt gets a fresh token; a second failure means the
        # grant is genuinely gone.
        if celery_task.request.retries < 1:
            return Outcome(RETRY, retry_after=BASE_BACKOFF_SECONDS, error=exc)
        TaskListService.mark_skipped(task_list.id, str(exc))
        return Outcome(SKIPPED)

    if action == "recreate":
        task_list.external_id = ""
        task_list.save(update_fields=["external_id"])
        return Outcome(RETRY, retry_after=BASE_BACKOFF_SECONDS, error=exc)

    TaskListService.mark_sync_failed(task_list.id, str(exc))
    return Outcome(FAILED)


# ---------------------------------------------------------------------------
# Tasks
# ---------------------------------------------------------------------------


@shared_task(bind=True, max_retries=MAX_RETRIES, ignore_result=True)
def sync_task(self, task_id: str) -> str:
    """Push one task to Google Tasks, creating its list first if needed."""
    outcome = _sync_task_once(self, task_id)
    return _resolve(
        self,
        outcome,
        lambda reason: TaskService.mark_sync_failed(task_id, reason),
    )


def _sync_task_once(celery_task, task_id: str) -> Outcome:
    with transaction.atomic():
        task = (
            # of=("self",) locks the task row only. Postgres refuses FOR
            # UPDATE on the nullable side of an outer join, which is what
            # select_related on task_list and parent produces.
            Task.objects.select_for_update(skip_locked=True, of=("self",))
            .select_related("task_list", "parent")
            .filter(id=task_id)
            .first()
        )
        if task is None:
            logger.info("Task %s is gone or already being synced elsewhere", task_id)
            return Outcome(UNAVAILABLE)

        # Nothing was ever pushed and nothing ever will be: settle it without
        # spending a broker call.
        if task.deleted and not task.external_id:
            TaskService.mark_synced(task.id, "", "")
            return Outcome(SYNCED)

        try:
            client = _client_for(task.owner_id)
        except Exception as exc:
            outcome = _broker_failure(celery_task, exc)
            if outcome is None:
                raise
            if outcome.status == SKIPPED:
                TaskService.mark_skipped(task.id, str(exc))
            return outcome

        try:
            return _push_task(task, client)
        except GoogleTasksError as exc:
            return _handle_task_google_error(celery_task, task, exc)


def _push_task(task: Task, client: GoogleTasksClient) -> Outcome:
    task_list = task.task_list

    if task_list is None:
        TaskService.mark_sync_failed(task.id, "task has no list to sync into")
        return Outcome(FAILED)

    if task.deleted:
        if task.external_id and task_list.external_id and not task_list.deleted:
            client.delete_task(task_list.external_id, task.external_id)
        TaskService.mark_synced(task.id, task.external_id, task.etag)
        return Outcome(SYNCED)

    if task_list.deleted:
        # Deleting the list at Google takes this task with it.
        TaskService.mark_synced(task.id, task.external_id, task.etag)
        return Outcome(SYNCED)

    list_external_id = _ensure_list_synced(task_list, client)

    parent_external_id = None
    if task.parent and not task.parent.deleted:
        parent_external_id = _ensure_task_synced(task.parent, client, list_external_id)

    body = task_to_body(task)

    if task.external_id:
        remote = client.update_task(list_external_id, task.external_id, body)
    else:
        remote = client.create_task(list_external_id, body, parent=parent_external_id)

    TaskService.mark_synced(
        task.id,
        remote.get("id") or task.external_id,
        remote.get("etag", ""),
        remote.get("position", ""),
    )
    return Outcome(SYNCED)


def _ensure_list_synced(task_list: TaskList, client: GoogleTasksClient) -> str:
    """
    Create the list at Google if it isn't there yet, reusing this run's client.

    Doing it inline rather than chaining a separate job removes the window
    where a task could race ahead of the list it belongs to.
    """
    if task_list.external_id:
        return task_list.external_id

    remote = client.create_list(task_list.title)
    external_id = remote.get("id", "")
    TaskListService.mark_synced(task_list.id, external_id, remote.get("etag", ""))
    task_list.external_id = external_id
    return external_id


def _ensure_task_synced(
    parent: Task, client: GoogleTasksClient, list_external_id: str
) -> str:
    """Create a subtask's parent first. Google allows only one level, so this
    never recurses."""
    if parent.external_id:
        return parent.external_id

    remote = client.create_task(list_external_id, task_to_body(parent))
    external_id = remote.get("id", "")
    TaskService.mark_synced(
        parent.id, external_id, remote.get("etag", ""), remote.get("position", "")
    )
    parent.external_id = external_id
    return external_id


def _handle_task_google_error(
    celery_task, task: Task, exc: GoogleTasksError
) -> Outcome:
    action = _classify(exc)

    if action == RETRY:
        return Outcome(
            RETRY, retry_after=_backoff(celery_task.request.retries), error=exc
        )

    if action == "reauthorize":
        if celery_task.request.retries < 1:
            return Outcome(RETRY, retry_after=BASE_BACKOFF_SECONDS, error=exc)
        TaskService.mark_skipped(task.id, str(exc))
        return Outcome(SKIPPED)

    if action == "recreate":
        task.external_id = ""
        task.save(update_fields=["external_id"])
        return Outcome(RETRY, retry_after=BASE_BACKOFF_SECONDS, error=exc)

    TaskService.mark_sync_failed(task.id, str(exc))
    return Outcome(FAILED)


# ---------------------------------------------------------------------------
# Pull
# ---------------------------------------------------------------------------


@shared_task(bind=True, max_retries=MAX_RETRIES, ignore_result=True)
def pull_from_google(self, owner_id: str) -> str:
    """Bring one user's Google Tasks changes back into the local database."""
    outcome = _pull_once(self, owner_id)
    return _resolve(
        self,
        outcome,
        lambda reason: _record_pull_error(owner_id, reason),
    )


def _pull_once(celery_task, owner_id: str) -> Outcome:
    """
    Read Google and reconcile, without wrapping the whole thing in one
    transaction: a pull can span many pages, and holding a transaction across
    all of them would sit on locks for the duration. Each upsert stands alone,
    and the watermark only advances on full success, so a pull that dies
    halfway is simply redone.
    """
    state, _ = GoogleSyncState.objects.get_or_create(owner_id=owner_id)
    first_pull = state.last_pulled_at is None

    # Captured before any read. Advancing to the finish time instead would
    # lose every change made while the pull was running.
    started_at = timezone.now()
    updated_min = (
        None
        if first_pull
        else to_updated_min(
            state.last_pulled_at
            - timedelta(minutes=settings.GOOGLE_PULL_OVERLAP_MINUTES)
        )
    )

    try:
        client = _client_for(owner_id)
    except Exception as exc:
        outcome = _broker_failure(celery_task, exc)
        if outcome is None:
            raise
        if outcome.status == SKIPPED:
            _record_pull_error(owner_id, str(exc))
        return outcome

    try:
        remote_lists = client.list_lists()

        seen_external_ids = set()
        for remote_list in remote_lists:
            external_id = remote_list.get("id")
            if not external_id:
                continue
            seen_external_ids.add(external_id)

            task_list = _upsert_remote_list(owner_id, remote_list)

            try:
                remote_tasks = client.list_tasks(external_id, updated_min=updated_min)
            except GoogleTasksError as exc:
                if exc.status == 404:
                    # Deleted between the enumeration and this read.
                    logger.info("Task list %s vanished mid-pull", external_id)
                    continue
                raise

            _upsert_remote_tasks(owner_id, task_list, remote_tasks)

        if not first_pull:
            _soft_delete_missing_lists(owner_id, seen_external_ids)

    except GoogleTasksError as exc:
        return _handle_pull_google_error(celery_task, owner_id, exc)

    GoogleSyncState.objects.filter(owner_id=owner_id).update(
        last_pulled_at=started_at, last_attempted_at=started_at, last_error=""
    )
    logger.info(
        "Pulled %s task lists for account %s",
        len(remote_lists),
        owner_id,
        extra={"account_id": str(owner_id), "first_pull": first_pull},
    )
    return Outcome(SYNCED)


def _upsert_remote_list(owner_id: str, remote: dict) -> TaskList:
    """
    Reconcile one remote list, returning the local row either way.

    A pending list keeps its local title: the queued push will carry that
    title to Google. Its tasks are still pulled, which is why this returns the
    row rather than skipping outright.
    """
    external_id = remote["id"]
    task_list = TaskList.objects.filter(
        owner_id=owner_id, external_id=external_id
    ).first()

    if task_list is None:
        return TaskList.objects.create(
            owner_id=owner_id,
            external_id=external_id,
            title=remote.get("title") or "Untitled list",
            etag=remote.get("etag") or "",
            sync_status=SyncStatus.SYNCED,
            last_synced_at=timezone.now(),
        )

    if task_list.sync_status == SyncStatus.PENDING:
        return task_list

    task_list.title = remote.get("title") or task_list.title
    task_list.etag = remote.get("etag") or ""
    task_list.sync_status = SyncStatus.SYNCED
    task_list.last_synced_at = timezone.now()
    task_list.save(
        update_fields=["title", "etag", "sync_status", "last_synced_at", "updated_at"]
    )
    return task_list


def _upsert_remote_tasks(
    owner_id: str, task_list: TaskList, remote_tasks: List[dict]
) -> None:
    parent_links = {}

    for remote in remote_tasks:
        if not remote.get("id"):
            continue
        task = _upsert_remote_task(owner_id, task_list, remote)
        if task is not None and remote.get("parent"):
            parent_links[task.id] = remote["parent"]

    # Second pass: a subtask can arrive on an earlier page than its parent, so
    # parents are only linkable once every task in the batch exists.
    _link_parents(owner_id, parent_links)


def _upsert_remote_task(
    owner_id: str, task_list: TaskList, remote: dict
) -> Optional[Task]:
    external_id = remote["id"]
    fields = remote_to_task_fields(remote)
    task = Task.objects.filter(owner_id=owner_id, external_id=external_id).first()

    if task is None:
        if fields["deleted"]:
            # Deleted at Google and never known here. Nothing to represent.
            return None
        return Task.objects.create(
            owner_id=owner_id,
            external_id=external_id,
            task_list=task_list,
            sync_status=SyncStatus.SYNCED,
            last_synced_at=timezone.now(),
            **fields,
        )

    if task.sync_status == SyncStatus.PENDING:
        # Local edit not yet pushed. The push wins; leave every field alone.
        return None

    for name, value in fields.items():
        setattr(task, name, value)
    # A task moved between lists at Google arrives on the new list's page.
    task.task_list = task_list
    task.sync_status = SyncStatus.SYNCED
    task.last_synced_at = timezone.now()
    task.save(
        update_fields=[
            *fields.keys(),
            "task_list",
            "sync_status",
            "last_synced_at",
            "updated_at",
        ]
    )
    return task


def _link_parents(owner_id: str, parent_links: dict) -> None:
    if not parent_links:
        return

    parents_by_external = {
        parent.external_id: parent
        for parent in Task.objects.filter(
            owner_id=owner_id, external_id__in=set(parent_links.values())
        )
    }

    for task_id, parent_external_id in parent_links.items():
        parent = parents_by_external.get(parent_external_id)
        if parent is None or parent.id == task_id:
            continue
        # queryset.update leaves updated_at alone, so linking a parent does
        # not make the row look freshly edited to the sweep.
        Task.objects.filter(id=task_id).exclude(parent_id=parent.id).update(
            parent=parent
        )


def _soft_delete_missing_lists(owner_id: str, seen_external_ids: set) -> None:
    """
    Treat a synced list that stopped appearing as deleted at Google.

    tasklists.list has no showDeleted, so absence is the only signal there is.
    Because this is inference rather than a reported fact, it only runs after
    a complete enumeration, never touches pending lists (local creations
    Google has not seen), and never runs on a first pull.
    """
    stale = (
        TaskList.objects.filter(
            owner_id=owner_id, deleted=False, sync_status=SyncStatus.SYNCED
        )
        .exclude(external_id="")
        .exclude(external_id__in=seen_external_ids)
    )

    for task_list in stale:
        task_list.deleted = True
        task_list.save(update_fields=["deleted", "updated_at"])
        # Deleting a list at Google took its tasks with it, so there is
        # nothing left to push for them.
        task_list.tasks.filter(deleted=False).update(
            deleted=True, sync_status=SyncStatus.SYNCED
        )
        logger.info(
            "Task list %s no longer exists at Google, soft-deleted locally",
            task_list.id,
            extra={"account_id": str(owner_id), "external_id": task_list.external_id},
        )


def _handle_pull_google_error(
    celery_task, owner_id: str, exc: GoogleTasksError
) -> Outcome:
    action = _classify(exc)

    if action == RETRY:
        return Outcome(
            RETRY, retry_after=_backoff(celery_task.request.retries), error=exc
        )

    if action == "reauthorize":
        if celery_task.request.retries < 1:
            return Outcome(RETRY, retry_after=BASE_BACKOFF_SECONDS, error=exc)
        _record_pull_error(owner_id, str(exc))
        return Outcome(SKIPPED)

    _record_pull_error(owner_id, str(exc))
    return Outcome(FAILED)


def _record_pull_error(owner_id: str, reason: str) -> None:
    GoogleSyncState.objects.filter(owner_id=owner_id).update(
        last_attempted_at=timezone.now(), last_error=reason[:512]
    )


@shared_task(ignore_result=True)
def pull_all_users() -> dict:
    """
    Queue a pull for every user known to have linked Google.

    Eligibility is self-maintaining: a record reaching synced is proof the
    push succeeded, which is proof the user granted access. Users who never
    linked are never polled and cost nothing.
    """
    owner_ids = set(
        TaskList.objects.filter(sync_status=SyncStatus.SYNCED)
        .values_list("owner_id", flat=True)
        .distinct()
    ) | set(
        Task.objects.filter(sync_status=SyncStatus.SYNCED)
        .values_list("owner_id", flat=True)
        .distinct()
    )

    batch = list(owner_ids)[: settings.GOOGLE_SYNC_SWEEP_BATCH_SIZE]
    jitter = settings.GOOGLE_PULL_JITTER_SECONDS

    for owner_id in batch:
        # Spread across the window so every user does not hit the broker and
        # Google on the same second.
        pull_from_google.apply_async(
            args=[str(owner_id)], countdown=random.randint(0, jitter)
        )

    logger.info("Queued a Google Tasks pull for %s accounts", len(batch))
    return {"users": len(batch)}


# ---------------------------------------------------------------------------
# Sweep
# ---------------------------------------------------------------------------


@shared_task(ignore_result=True)
def sweep_stale_syncs(include_skipped: bool = False) -> dict:
    """
    Re-enqueue records the relay has not settled.

    Covers enqueues lost to a worker restart and anything that previously
    failed. With include_skipped it also revisits records parked because the
    owner had not linked Google, which is how linking later backfills.

    Batches are bounded so a large backlog drains over several runs instead of
    stampeding the broker and Google in one go.
    """
    stale_before = timezone.now() - timedelta(
        minutes=settings.GOOGLE_SYNC_STALE_AFTER_MINUTES
    )
    batch_size = settings.GOOGLE_SYNC_SWEEP_BATCH_SIZE

    statuses = [SyncStatus.PENDING, SyncStatus.FAILED]
    if include_skipped:
        statuses.append(SyncStatus.SKIPPED)

    list_ids = list(
        TaskList.objects.filter(
            sync_status__in=statuses, updated_at__lt=stale_before
        )
        .order_by("updated_at")
        .values_list("id", flat=True)[:batch_size]
    )
    for list_id in list_ids:
        sync_task_list.delay(str(list_id))

    task_ids = list(
        Task.objects.filter(sync_status__in=statuses, updated_at__lt=stale_before)
        .order_by("updated_at")
        .values_list("id", flat=True)[:batch_size]
    )
    for task_id in task_ids:
        sync_task.delay(str(task_id))

    counts = {"task_lists": len(list_ids), "tasks": len(task_ids)}
    logger.info(
        "Sweep re-enqueued %s task lists and %s tasks (include_skipped=%s)",
        counts["task_lists"],
        counts["tasks"],
        include_skipped,
    )
    return counts
