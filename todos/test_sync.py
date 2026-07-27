"""
Tests for the Google Tasks relay.

No network and no Google credentials: the broker and the API adapter are both
patched. Each row of the failure classification table has a test here.
"""

import uuid
from datetime import timedelta
from unittest.mock import MagicMock, patch

from celery.exceptions import Retry
from django.test import TestCase
from django.utils import timezone

from todos.models import SyncStatus, Task, TaskList, TaskStatus
from todos.google_tasks import GoogleTasksError, task_to_body
from todos.services.task_service import TaskService
from todos.tasks import sweep_stale_syncs, sync_task, sync_task_list

ACCESS_TOKEN = "ya29.a0AfH6SMB-super-secret-value"

REMOTE_LIST = {"id": "google-list-1", "etag": "list-etag-1"}
REMOTE_TASK = {"id": "google-task-1", "etag": "task-etag-1", "position": "00001"}


def brokered_token():
    token = MagicMock()
    token.access_token = ACCESS_TOKEN
    return token


class SyncTestCase(TestCase):
    """Shared plumbing: a user, a list, and patched collaborators."""

    def setUp(self):
        self.owner_id = uuid.uuid4()
        self.task_list = TaskList.objects.create(
            owner_id=self.owner_id,
            title="My Tasks",
            is_default=True,
            sync_status=SyncStatus.PENDING,
        )

        self.client = MagicMock()
        self.client.create_list.return_value = dict(REMOTE_LIST)
        self.client.update_list.return_value = dict(REMOTE_LIST)
        self.client.create_task.return_value = dict(REMOTE_TASK)
        self.client.update_task.return_value = dict(REMOTE_TASK)

        broker_patch = patch("todos.tasks.TokenBroker")
        self.broker_cls = broker_patch.start()
        self.broker_cls.return_value.token.return_value = brokered_token()
        self.addCleanup(broker_patch.stop)

        client_patch = patch("todos.tasks.GoogleTasksClient", return_value=self.client)
        self.client_cls = client_patch.start()
        self.addCleanup(client_patch.stop)

    def make_task(self, **kwargs):
        defaults = {
            "owner_id": self.owner_id,
            "task_list": self.task_list,
            "title": "Buy milk",
            "sync_status": SyncStatus.PENDING,
        }
        defaults.update(kwargs)
        return Task.objects.create(**defaults)

    def synced_list(self):
        self.task_list.external_id = "google-list-existing"
        self.task_list.sync_status = SyncStatus.SYNCED
        self.task_list.save()
        return self.task_list

    def capture_retry(self, celery_task=sync_task):
        """
        Intercept the retry decision.

        Celery's eager .apply() re-runs a retry inline, which would exercise
        its scheduler rather than this module's classification. Patching retry
        to raise keeps the assertion on the decision that was made.
        """
        return patch.object(celery_task, "retry", side_effect=Retry())


class EnqueueTests(SyncTestCase):
    """Writes queue a push, but only once the transaction commits."""

    def test_create_task_enqueues_one_sync_after_commit(self):
        with patch("todos.tasks.sync_task.delay") as delay:
            with self.captureOnCommitCallbacks(execute=True):
                task = TaskService.create_task(
                    owner_id=self.owner_id, title="Buy milk", task_list=self.task_list
                )

        delay.assert_called_once_with(str(task.id))

    def test_nothing_is_enqueued_before_commit(self):
        with patch("todos.tasks.sync_task.delay") as delay:
            TaskService.create_task(
                owner_id=self.owner_id, title="Buy milk", task_list=self.task_list
            )

        delay.assert_not_called()

    def test_update_enqueues_a_sync(self):
        task = self.make_task(sync_status=SyncStatus.SYNCED, external_id="g1")

        with patch("todos.tasks.sync_task.delay") as delay:
            with self.captureOnCommitCallbacks(execute=True):
                TaskService.update_task(
                    owner_id=self.owner_id, task_id=task.id, title="Buy oat milk"
                )

        delay.assert_called_once_with(str(task.id))

    def test_delete_enqueues_a_sync(self):
        task = self.make_task(sync_status=SyncStatus.SYNCED, external_id="g1")

        with patch("todos.tasks.sync_task.delay") as delay:
            with self.captureOnCommitCallbacks(execute=True):
                TaskService.delete_task(owner_id=self.owner_id, task_id=task.id)

        delay.assert_called_once_with(str(task.id))

    def test_ids_are_enqueued_as_strings(self):
        """UUIDs are not JSON serialisable, so the signature must be a str."""
        with patch("todos.tasks.sync_task.delay") as delay:
            with self.captureOnCommitCallbacks(execute=True):
                TaskService.create_task(
                    owner_id=self.owner_id, title="Buy milk", task_list=self.task_list
                )

        (arg,), _ = delay.call_args
        self.assertIsInstance(arg, str)


class PushHappyPathTests(SyncTestCase):
    def test_creates_task_in_google_and_writes_back(self):
        self.synced_list()
        task = self.make_task()

        result = sync_task.apply(args=[str(task.id)])
        task.refresh_from_db()

        self.assertEqual(result.result, "synced")
        self.assertEqual(task.external_id, "google-task-1")
        self.assertEqual(task.etag, "task-etag-1")
        self.assertEqual(task.position, "00001")
        self.assertEqual(task.sync_status, SyncStatus.SYNCED)
        self.assertIsNotNone(task.last_synced_at)

    def test_creates_the_list_first_when_it_has_never_synced(self):
        task = self.make_task()

        sync_task.apply(args=[str(task.id)])
        task.refresh_from_db()
        self.task_list.refresh_from_db()

        self.client.create_list.assert_called_once_with("My Tasks")
        self.assertEqual(self.task_list.external_id, "google-list-1")
        self.assertEqual(self.task_list.sync_status, SyncStatus.SYNCED)
        # The task went into the list that was just created.
        self.assertEqual(
            self.client.create_task.call_args[0][0], "google-list-1"
        )
        self.assertEqual(task.sync_status, SyncStatus.SYNCED)

    def test_list_and_task_share_one_brokered_token(self):
        self.make_task()

        sync_task.apply(args=[str(self.make_task().id)])

        self.broker_cls.return_value.token.assert_called_once()

    def test_subtask_syncs_its_parent_first(self):
        self.synced_list()
        parent = self.make_task(title="Groceries")
        child = self.make_task(title="Buy milk", parent=parent)

        self.client.create_task.side_effect = [
            {"id": "google-parent", "etag": "e", "position": "1"},
            {"id": "google-child", "etag": "e", "position": "2"},
        ]

        sync_task.apply(args=[str(child.id)])
        parent.refresh_from_db()
        child.refresh_from_db()

        self.assertEqual(parent.external_id, "google-parent")
        self.assertEqual(child.external_id, "google-child")
        # The child was inserted under its parent.
        self.assertEqual(
            self.client.create_task.call_args.kwargs["parent"], "google-parent"
        )

    def test_existing_external_id_patches_instead_of_inserting(self):
        self.synced_list()
        task = self.make_task(external_id="google-task-1")

        sync_task.apply(args=[str(task.id)])

        self.client.update_task.assert_called_once()
        self.client.create_task.assert_not_called()

    def test_local_only_fields_are_never_sent(self):
        self.synced_list()
        task = self.make_task(priority="high", notes="milk", due=timezone.now())

        body = task_to_body(task)

        self.assertNotIn("priority", body)
        self.assertNotIn("tags", body)
        self.assertNotIn("color", body)
        self.assertNotIn("position", body)
        self.assertEqual(body["title"], "Buy milk")
        self.assertEqual(body["notes"], "milk")

    def test_due_is_sent_as_a_date_at_midnight(self):
        due = timezone.now().replace(hour=15, minute=30)
        task = self.make_task(due=due)

        body = task_to_body(task)

        self.assertEqual(body["due"], f"{due.date().isoformat()}T00:00:00.000Z")

    def test_completed_task_sends_its_completion_time(self):
        now = timezone.now()
        task = self.make_task(status=TaskStatus.COMPLETED, completed=now)

        body = task_to_body(task)

        self.assertEqual(body["status"], TaskStatus.COMPLETED)
        self.assertEqual(body["completed"], now.isoformat())


class DeleteTests(SyncTestCase):
    def test_delete_removes_it_from_google(self):
        self.synced_list()
        task = self.make_task(external_id="google-task-1", deleted=True)

        result = sync_task.apply(args=[str(task.id)])
        task.refresh_from_db()

        self.client.delete_task.assert_called_once_with(
            "google-list-existing", "google-task-1"
        )
        self.assertEqual(result.result, "synced")
        self.assertEqual(task.sync_status, SyncStatus.SYNCED)

    def test_delete_of_a_never_synced_task_costs_nothing(self):
        task = self.make_task(deleted=True)

        result = sync_task.apply(args=[str(task.id)])
        task.refresh_from_db()

        self.broker_cls.return_value.token.assert_not_called()
        self.client.delete_task.assert_not_called()
        self.assertEqual(result.result, "synced")
        self.assertEqual(task.sync_status, SyncStatus.SYNCED)

    def test_task_in_a_deleted_list_is_settled_without_a_call(self):
        self.synced_list()
        task = self.make_task(external_id="google-task-1")
        self.task_list.deleted = True
        self.task_list.save()

        result = sync_task.apply(args=[str(task.id)])
        task.refresh_from_db()

        self.client.delete_task.assert_not_called()
        self.assertEqual(result.result, "synced")

    def test_deleting_a_list_settles_its_tasks(self):
        self.synced_list()
        task = self.make_task(external_id="google-task-1")
        self.task_list.deleted = True
        self.task_list.sync_status = SyncStatus.PENDING
        self.task_list.save()

        result = sync_task_list.apply(args=[str(self.task_list.id)])
        task.refresh_from_db()

        self.client.delete_list.assert_called_once_with("google-list-existing")
        self.assertEqual(result.result, "synced")
        self.assertEqual(task.sync_status, SyncStatus.SYNCED)


class UnlinkedUserTests(SyncTestCase):
    """The headline requirement: no Google link is a no-op, not an error."""

    def setUp(self):
        super().setUp()
        from verisafe.exceptions import NeedsAuthorization

        self.broker_cls.return_value.token.side_effect = NeedsAuthorization(
            provider="google",
            capabilities=["tasks"],
            authorization_url="https://verisafe/oauth/google/authorize",
            reason="no_grant",
        )

    def test_task_is_skipped_not_failed(self):
        self.synced_list()
        task = self.make_task()

        result = sync_task.apply(args=[str(task.id)])
        task.refresh_from_db()

        self.assertEqual(result.result, "skipped")
        self.assertEqual(task.sync_status, SyncStatus.SKIPPED)
        self.assertNotEqual(task.sync_status, SyncStatus.FAILED)

    def test_no_google_call_is_attempted(self):
        task = self.make_task()

        sync_task.apply(args=[str(task.id)])

        self.client.create_task.assert_not_called()
        self.client.create_list.assert_not_called()

    def test_it_does_not_raise(self):
        task = self.make_task()

        result = sync_task.apply(args=[str(task.id)])

        self.assertEqual(result.state, "SUCCESS")

    def test_list_is_skipped_too(self):
        result = sync_task_list.apply(args=[str(self.task_list.id)])
        self.task_list.refresh_from_db()

        self.assertEqual(result.result, "skipped")
        self.assertEqual(self.task_list.sync_status, SyncStatus.SKIPPED)


class BrokerFailureTests(SyncTestCase):
    def test_provider_down_retries_and_leaves_the_record_pending(self):
        from verisafe.exceptions import ProviderDown

        self.broker_cls.return_value.token.side_effect = ProviderDown("upstream")
        task = self.make_task()

        with self.capture_retry() as retry:
            sync_task.apply(args=[str(task.id)])
        task.refresh_from_db()

        retry.assert_called_once()
        self.assertGreater(retry.call_args.kwargs["countdown"], 0)
        self.assertEqual(task.sync_status, SyncStatus.PENDING)

    def test_our_bad_credentials_never_mark_the_record_failed(self):
        """The deployment is broken, not the row. Burying it in per-record
        failure counts is how it goes unnoticed."""
        from verisafe.exceptions import BrokerCredentialsRejected

        self.broker_cls.return_value.token.side_effect = BrokerCredentialsRejected(
            "missing oauth-token-broker role"
        )
        task = self.make_task()

        with self.capture_retry() as retry:
            sync_task.apply(args=[str(task.id)])
        task.refresh_from_db()

        retry.assert_called_once()
        self.assertEqual(task.sync_status, SyncStatus.PENDING)
        self.assertNotEqual(task.sync_status, SyncStatus.FAILED)

    def test_bad_credentials_back_off_harder_than_a_flaky_provider(self):
        """Our misconfiguration is not fixed by hammering the broker."""
        from verisafe.exceptions import BrokerCredentialsRejected, ProviderDown

        task = self.make_task()

        self.broker_cls.return_value.token.side_effect = ProviderDown("upstream")
        with self.capture_retry() as retry:
            sync_task.apply(args=[str(task.id)])
        transient_delay = retry.call_args.kwargs["countdown"]

        self.broker_cls.return_value.token.side_effect = BrokerCredentialsRejected("x")
        with self.capture_retry() as retry:
            sync_task.apply(args=[str(task.id)])
        credentials_delay = retry.call_args.kwargs["countdown"]

        self.assertGreater(credentials_delay, transient_delay)

    def test_retries_are_given_up_on_eventually(self):
        from verisafe.exceptions import ProviderDown

        self.broker_cls.return_value.token.side_effect = ProviderDown("upstream")
        task = self.make_task()

        with self.capture_retry() as retry:
            result = sync_task.apply(args=[str(task.id)], retries=99)
        task.refresh_from_db()

        retry.assert_not_called()
        self.assertEqual(result.result, "failed")
        self.assertEqual(task.sync_status, SyncStatus.FAILED)


class GoogleFailureTests(SyncTestCase):
    def setUp(self):
        super().setUp()
        self.synced_list()

    def test_400_marks_failed_without_retrying(self):
        self.client.create_task.side_effect = GoogleTasksError(400, "bad body")
        task = self.make_task()

        result = sync_task.apply(args=[str(task.id)])
        task.refresh_from_db()

        self.assertEqual(result.result, "failed")
        self.assertEqual(task.sync_status, SyncStatus.FAILED)

    def test_429_retries(self):
        self.client.create_task.side_effect = GoogleTasksError(429, "slow down")
        task = self.make_task()

        with self.capture_retry() as retry:
            sync_task.apply(args=[str(task.id)])
        task.refresh_from_db()

        retry.assert_called_once()
        self.assertEqual(task.sync_status, SyncStatus.PENDING)

    def test_503_retries(self):
        self.client.create_task.side_effect = GoogleTasksError(503, "unavailable")
        task = self.make_task()

        with self.capture_retry() as retry:
            sync_task.apply(args=[str(task.id)])

        retry.assert_called_once()

    def test_404_on_update_clears_the_stale_id_and_retries(self):
        self.client.update_task.side_effect = GoogleTasksError(404, "gone")
        task = self.make_task(external_id="google-task-1")

        with self.capture_retry() as retry:
            sync_task.apply(args=[str(task.id)])
        task.refresh_from_db()

        retry.assert_called_once()
        # Cleared, so the retry inserts rather than patching a dead id. This
        # write has to survive the retry, which is why retries are raised
        # outside the transaction.
        self.assertEqual(task.external_id, "")

    def test_403_retries_once_then_skips(self):
        self.client.create_task.side_effect = GoogleTasksError(403, "forbidden")
        task = self.make_task()

        with self.capture_retry() as retry:
            sync_task.apply(args=[str(task.id)])
        retry.assert_called_once()

        second = sync_task.apply(args=[str(task.id)], retries=1)
        task.refresh_from_db()

        self.assertEqual(second.result, "skipped")
        self.assertEqual(task.sync_status, SyncStatus.SKIPPED)

    def test_the_access_token_is_never_logged(self):
        task = self.make_task()

        with self.assertLogs("keep_up", level="DEBUG") as logs:
            sync_task.apply(args=[str(task.id)])

        self.assertTrue(logs.output)
        for line in logs.output:
            self.assertNotIn(ACCESS_TOKEN, line)


class MissingRecordTests(SyncTestCase):
    def test_a_deleted_row_is_not_an_error(self):
        result = sync_task.apply(args=[str(uuid.uuid4())])

        self.assertEqual(result.result, "unavailable")
        self.assertEqual(result.state, "SUCCESS")

    def test_a_task_with_no_list_fails_cleanly(self):
        task = self.make_task(task_list=None)

        result = sync_task.apply(args=[str(task.id)])
        task.refresh_from_db()

        self.assertEqual(result.result, "failed")
        self.assertEqual(task.sync_status, SyncStatus.FAILED)


class SweepTests(SyncTestCase):
    def age(self, instance, minutes=60):
        """Push updated_at into the past; .update() skips auto_now."""
        stale = timezone.now() - timedelta(minutes=minutes)
        type(instance).objects.filter(id=instance.id).update(updated_at=stale)

    def test_re_enqueues_stale_pending_records(self):
        task = self.make_task()
        self.age(task)
        self.age(self.task_list)

        with patch("todos.tasks.sync_task.delay") as task_delay, patch(
            "todos.tasks.sync_task_list.delay"
        ) as list_delay:
            counts = sweep_stale_syncs()

        task_delay.assert_called_once_with(str(task.id))
        list_delay.assert_called_once_with(str(self.task_list.id))
        self.assertEqual(counts, {"task_lists": 1, "tasks": 1})

    def test_leaves_fresh_records_alone(self):
        self.make_task()

        with patch("todos.tasks.sync_task.delay") as task_delay:
            sweep_stale_syncs()

        task_delay.assert_not_called()

    def test_retries_failed_records(self):
        task = self.make_task(sync_status=SyncStatus.FAILED)
        self.age(task)

        with patch("todos.tasks.sync_task.delay") as task_delay:
            sweep_stale_syncs()

        task_delay.assert_called_once_with(str(task.id))

    def test_skipped_records_are_left_alone_by_default(self):
        task = self.make_task(sync_status=SyncStatus.SKIPPED)
        self.age(task)

        with patch("todos.tasks.sync_task.delay") as task_delay:
            sweep_stale_syncs()

        task_delay.assert_not_called()

    def test_backfill_picks_up_skipped_records(self):
        """A user who links Google later gets their backlog pushed."""
        task = self.make_task(sync_status=SyncStatus.SKIPPED)
        self.age(task)

        with patch("todos.tasks.sync_task.delay") as task_delay:
            sweep_stale_syncs(include_skipped=True)

        task_delay.assert_called_once_with(str(task.id))

    def test_synced_records_are_never_swept(self):
        task = self.make_task(sync_status=SyncStatus.SYNCED)
        self.age(task)

        with patch("todos.tasks.sync_task.delay") as task_delay:
            sweep_stale_syncs()

        task_delay.assert_not_called()
