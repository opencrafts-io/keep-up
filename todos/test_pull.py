"""
Tests for pulling changes back from Google Tasks.

No network: the broker and the API adapter are patched. The conflict rule,
the watermark arithmetic, and the loop-breaker each have a test.
"""

import uuid
from datetime import timedelta
from unittest.mock import MagicMock, patch

from celery.exceptions import Retry
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

from todos.google_tasks import (
    GoogleTasksClient,
    GoogleTasksError,
    remote_to_task_fields,
)
from todos.models import (
    GoogleSyncState,
    SyncStatus,
    Task,
    TaskList,
    TaskPriority,
    TaskStatus,
)
from todos.tasks import pull_all_users, pull_from_google

ACCESS_TOKEN = "ya29.a0AfH6SMB-super-secret-value"


def remote_list(external_id="google-list-1", title="My Tasks", etag="list-etag"):
    return {"id": external_id, "title": title, "etag": etag}


def remote_task(external_id="google-task-1", **overrides):
    task = {
        "id": external_id,
        "title": "Buy milk",
        "status": "needsAction",
        "etag": "task-etag",
        "position": "00001",
        "updated": "2026-07-27T10:00:00.000Z",
    }
    task.update(overrides)
    return task


class PullTestCase(TestCase):
    def setUp(self):
        self.owner_id = uuid.uuid4()

        self.client = MagicMock()
        self.client.list_lists.return_value = [remote_list()]
        self.client.list_tasks.return_value = []

        token = MagicMock()
        token.access_token = ACCESS_TOKEN
        broker_patch = patch("todos.tasks.TokenBroker")
        self.broker_cls = broker_patch.start()
        self.broker_cls.return_value.token.return_value = token
        self.addCleanup(broker_patch.stop)

        client_patch = patch("todos.tasks.GoogleTasksClient", return_value=self.client)
        client_patch.start()
        self.addCleanup(client_patch.stop)

    def pull(self, **kwargs):
        return pull_from_google.apply(args=[str(self.owner_id)], **kwargs)

    def local_list(self, external_id="google-list-1", **kwargs):
        defaults = {
            "owner_id": self.owner_id,
            "title": "My Tasks",
            "external_id": external_id,
            "sync_status": SyncStatus.SYNCED,
        }
        defaults.update(kwargs)
        return TaskList.objects.create(**defaults)

    def local_task(self, task_list, external_id="google-task-1", **kwargs):
        defaults = {
            "owner_id": self.owner_id,
            "task_list": task_list,
            "title": "Buy milk",
            "external_id": external_id,
            "sync_status": SyncStatus.SYNCED,
        }
        defaults.update(kwargs)
        return Task.objects.create(**defaults)

    def watermark(self, moment):
        GoogleSyncState.objects.update_or_create(
            owner_id=self.owner_id, defaults={"last_pulled_at": moment}
        )


class ImportTests(PullTestCase):
    def test_imports_a_list_it_has_never_seen(self):
        result = self.pull()

        self.assertEqual(result.result, "synced")
        task_list = TaskList.objects.get(owner_id=self.owner_id)
        self.assertEqual(task_list.external_id, "google-list-1")
        self.assertEqual(task_list.title, "My Tasks")
        self.assertEqual(task_list.sync_status, SyncStatus.SYNCED)
        self.assertFalse(task_list.is_default)

    def test_imports_a_task_it_has_never_seen(self):
        self.client.list_tasks.return_value = [remote_task()]

        self.pull()

        task = Task.objects.get(owner_id=self.owner_id)
        self.assertEqual(task.external_id, "google-task-1")
        self.assertEqual(task.title, "Buy milk")
        self.assertEqual(task.position, "00001")
        self.assertEqual(task.sync_status, SyncStatus.SYNCED)
        self.assertEqual(task.task_list.external_id, "google-list-1")

    def test_imported_tasks_keep_local_only_defaults(self):
        """Google has no priority or tags, so a pull must not blank them."""
        self.client.list_tasks.return_value = [remote_task()]

        self.pull()

        task = Task.objects.get(owner_id=self.owner_id)
        self.assertEqual(task.priority, TaskPriority.NONE)
        self.assertEqual(task.tags.count(), 0)

    def test_a_task_deleted_remotely_and_unknown_locally_is_ignored(self):
        self.client.list_tasks.return_value = [remote_task(deleted=True)]

        self.pull()

        self.assertFalse(Task.objects.filter(owner_id=self.owner_id).exists())

    def test_completed_task_carries_its_timestamps(self):
        self.client.list_tasks.return_value = [
            remote_task(
                status="completed", completed="2026-07-27T09:30:00.000Z"
            )
        ]

        self.pull()

        task = Task.objects.get(owner_id=self.owner_id)
        self.assertEqual(task.status, TaskStatus.COMPLETED)
        self.assertIsNotNone(task.completed)


class UpdateTests(PullTestCase):
    def test_applies_remote_changes_to_a_known_task(self):
        task_list = self.local_list()
        task = self.local_task(task_list)
        self.client.list_tasks.return_value = [
            remote_task(title="Buy oat milk", status="completed", position="00009")
        ]

        self.pull()
        task.refresh_from_db()

        self.assertEqual(task.title, "Buy oat milk")
        self.assertEqual(task.status, TaskStatus.COMPLETED)
        self.assertEqual(task.position, "00009")
        self.assertEqual(task.sync_status, SyncStatus.SYNCED)
        self.assertIsNotNone(task.last_synced_at)

    def test_remote_deletion_soft_deletes_locally(self):
        task_list = self.local_list()
        task = self.local_task(task_list)
        self.client.list_tasks.return_value = [remote_task(deleted=True)]

        self.pull()
        task.refresh_from_db()

        self.assertTrue(task.deleted)
        self.assertEqual(task.sync_status, SyncStatus.SYNCED)

    def test_a_task_moved_between_lists_follows(self):
        origin = self.local_list("google-list-1")
        destination = self.local_list("google-list-2", title="Work")
        task = self.local_task(origin)

        self.client.list_lists.return_value = [
            remote_list("google-list-1"),
            remote_list("google-list-2", title="Work"),
        ]
        self.client.list_tasks.side_effect = [[], [remote_task()]]

        self.pull()
        task.refresh_from_db()

        self.assertEqual(task.task_list_id, destination.id)

    def test_remote_list_rename_applies(self):
        task_list = self.local_list()
        self.client.list_lists.return_value = [remote_list(title="Renamed")]

        self.pull()
        task_list.refresh_from_db()

        self.assertEqual(task_list.title, "Renamed")


class ConflictTests(PullTestCase):
    """A pending record has an unpushed local edit; the push wins."""

    def test_pending_task_is_left_alone(self):
        task_list = self.local_list()
        task = self.local_task(
            task_list, title="My local edit", sync_status=SyncStatus.PENDING
        )
        self.client.list_tasks.return_value = [remote_task(title="Remote title")]

        self.pull()
        task.refresh_from_db()

        self.assertEqual(task.title, "My local edit")
        self.assertEqual(task.sync_status, SyncStatus.PENDING)

    def test_pending_task_survives_a_remote_deletion(self):
        task_list = self.local_list()
        task = self.local_task(task_list, sync_status=SyncStatus.PENDING)
        self.client.list_tasks.return_value = [remote_task(deleted=True)]

        self.pull()
        task.refresh_from_db()

        self.assertFalse(task.deleted)

    def test_pending_list_keeps_its_local_title(self):
        task_list = self.local_list(title="My local name", sync_status=SyncStatus.PENDING)
        self.client.list_lists.return_value = [remote_list(title="Remote name")]

        self.pull()
        task_list.refresh_from_db()

        self.assertEqual(task_list.title, "My local name")
        self.assertEqual(task_list.sync_status, SyncStatus.PENDING)

    def test_tasks_in_a_pending_list_are_still_pulled(self):
        self.local_list(sync_status=SyncStatus.PENDING)
        self.client.list_tasks.return_value = [remote_task()]

        self.pull()

        self.assertTrue(Task.objects.filter(external_id="google-task-1").exists())


class LoopBreakerTests(PullTestCase):
    def test_a_pull_never_enqueues_a_push(self):
        """Routing pull writes through the services would ping-pong forever."""
        self.local_list()
        self.client.list_tasks.return_value = [remote_task(title="Changed")]

        with patch("todos.tasks.sync_task.delay") as push, patch(
            "todos.tasks.sync_task_list.delay"
        ) as push_list:
            self.pull()

        push.assert_not_called()
        push_list.assert_not_called()

    def test_pulled_records_are_not_left_pending(self):
        self.local_list()
        self.client.list_tasks.return_value = [remote_task()]

        self.pull()

        self.assertFalse(
            Task.objects.filter(
                owner_id=self.owner_id, sync_status=SyncStatus.PENDING
            ).exists()
        )


class WatermarkTests(PullTestCase):
    def test_first_pull_sends_no_updated_min(self):
        self.pull()

        self.assertIsNone(self.client.list_tasks.call_args.kwargs["updated_min"])

    def test_later_pull_sends_the_watermark_minus_the_overlap(self):
        last = timezone.now() - timedelta(hours=1)
        self.watermark(last)

        self.pull()

        sent = self.client.list_tasks.call_args.kwargs["updated_min"]
        expected = (last - timedelta(minutes=2)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        self.assertEqual(sent, expected)

    def test_watermark_advances_to_the_time_the_pull_started(self):
        before = timezone.now()

        self.pull()

        state = GoogleSyncState.objects.get(owner_id=self.owner_id)
        self.assertIsNotNone(state.last_pulled_at)
        self.assertGreaterEqual(state.last_pulled_at, before)
        self.assertLessEqual(state.last_pulled_at, timezone.now())

    def test_watermark_does_not_advance_when_the_pull_fails(self):
        self.watermark(None)
        self.client.list_lists.side_effect = GoogleTasksError(400, "bad request")

        self.pull()

        state = GoogleSyncState.objects.get(owner_id=self.owner_id)
        self.assertIsNone(state.last_pulled_at)
        self.assertIn("400", state.last_error)


class ParentTests(PullTestCase):
    def test_subtask_arriving_before_its_parent_is_still_linked(self):
        self.local_list()
        self.client.list_tasks.return_value = [
            remote_task("google-child", title="Buy milk", parent="google-parent"),
            remote_task("google-parent", title="Groceries"),
        ]

        self.pull()

        child = Task.objects.get(external_id="google-child")
        parent = Task.objects.get(external_id="google-parent")
        self.assertEqual(child.parent_id, parent.id)

    def test_a_missing_parent_is_not_fatal(self):
        self.local_list()
        self.client.list_tasks.return_value = [
            remote_task("google-child", parent="never-seen")
        ]

        result = self.pull()

        self.assertEqual(result.result, "synced")
        self.assertIsNone(Task.objects.get(external_id="google-child").parent_id)


class ListDeletionTests(PullTestCase):
    def test_a_list_that_stopped_appearing_is_soft_deleted(self):
        self.watermark(timezone.now() - timedelta(hours=1))
        task_list = self.local_list("google-list-gone")
        task = self.local_task(task_list)
        self.client.list_lists.return_value = [remote_list("google-list-1")]

        self.pull()
        task_list.refresh_from_db()
        task.refresh_from_db()

        self.assertTrue(task_list.deleted)
        self.assertTrue(task.deleted)
        self.assertEqual(task.sync_status, SyncStatus.SYNCED)

    def test_never_on_a_first_pull(self):
        """Local and remote have not met yet; absence proves nothing."""
        task_list = self.local_list("google-list-gone")
        self.client.list_lists.return_value = [remote_list("google-list-1")]

        self.pull()
        task_list.refresh_from_db()

        self.assertFalse(task_list.deleted)

    def test_never_for_a_pending_list(self):
        """A local creation Google has not been told about yet."""
        self.watermark(timezone.now() - timedelta(hours=1))
        task_list = self.local_list(
            "google-list-gone", sync_status=SyncStatus.PENDING
        )
        self.client.list_lists.return_value = [remote_list("google-list-1")]

        self.pull()
        task_list.refresh_from_db()

        self.assertFalse(task_list.deleted)

    def test_never_when_the_enumeration_failed(self):
        self.watermark(timezone.now() - timedelta(hours=1))
        task_list = self.local_list("google-list-gone")
        self.client.list_lists.side_effect = GoogleTasksError(503, "unavailable")

        with patch.object(pull_from_google, "retry", side_effect=Retry()):
            self.pull()
        task_list.refresh_from_db()

        self.assertFalse(task_list.deleted)


class PullFailureTests(PullTestCase):
    def test_unlinked_user_is_skipped_without_raising(self):
        from verisafe.exceptions import NeedsAuthorization

        self.broker_cls.return_value.token.side_effect = NeedsAuthorization(
            provider="google",
            capabilities=["tasks"],
            authorization_url="https://verisafe/authorize",
            reason="no_grant",
        )

        result = self.pull()

        self.assertEqual(result.result, "skipped")
        self.assertEqual(result.state, "SUCCESS")
        self.client.list_lists.assert_not_called()

    def test_provider_down_retries(self):
        from verisafe.exceptions import ProviderDown

        self.broker_cls.return_value.token.side_effect = ProviderDown("upstream")

        with patch.object(pull_from_google, "retry", side_effect=Retry()) as retry:
            self.pull()

        retry.assert_called_once()

    def test_a_list_deleted_mid_pull_is_not_fatal(self):
        self.client.list_tasks.side_effect = GoogleTasksError(404, "gone")

        result = self.pull()

        self.assertEqual(result.result, "synced")

    def test_rate_limit_retries(self):
        self.client.list_lists.side_effect = GoogleTasksError(429, "slow down")

        with patch.object(pull_from_google, "retry", side_effect=Retry()) as retry:
            self.pull()

        retry.assert_called_once()

    def test_the_access_token_is_never_logged(self):
        self.client.list_tasks.return_value = [remote_task()]

        with self.assertLogs("keep_up", level="DEBUG") as logs:
            self.pull()

        self.assertTrue(logs.output)
        for line in logs.output:
            self.assertNotIn(ACCESS_TOKEN, line)


class PullAllUsersTests(PullTestCase):
    def test_queues_users_with_a_synced_record(self):
        self.local_list()

        with patch("todos.tasks.pull_from_google.apply_async") as queued:
            counts = pull_all_users()

        self.assertEqual(counts, {"users": 1})
        self.assertEqual(
            queued.call_args.kwargs["args"], [str(self.owner_id)]
        )

    def test_ignores_users_who_never_linked_google(self):
        self.local_list(sync_status=SyncStatus.SKIPPED, external_id="")

        with patch("todos.tasks.pull_from_google.apply_async") as queued:
            counts = pull_all_users()

        queued.assert_not_called()
        self.assertEqual(counts, {"users": 0})

    def test_a_user_is_queued_once_even_with_many_synced_records(self):
        task_list = self.local_list()
        self.local_task(task_list)

        with patch("todos.tasks.pull_from_google.apply_async") as queued:
            pull_all_users()

        self.assertEqual(queued.call_count, 1)

    def test_pulls_are_spread_across_the_window(self):
        self.local_list()

        with patch("todos.tasks.pull_from_google.apply_async") as queued:
            pull_all_users()

        self.assertGreaterEqual(queued.call_args.kwargs["countdown"], 0)
        self.assertLessEqual(queued.call_args.kwargs["countdown"], 300)


class ClientReadTests(TestCase):
    """
    The adapter itself, with the Google service mocked rather than the client.

    tasks.list caps at 100 per page, so paging is not optional.
    """

    def service_returning(self, *pages):
        service = MagicMock()
        service.tasks.return_value.list.return_value.execute.side_effect = pages
        service.tasklists.return_value.list.return_value.execute.side_effect = pages
        return service

    def client_for(self, service):
        return GoogleTasksClient(access_token="unused", service=service)

    def test_list_tasks_follows_every_page(self):
        service = self.service_returning(
            {"items": [remote_task("a")], "nextPageToken": "page-2"},
            {"items": [remote_task("b")], "nextPageToken": "page-3"},
            {"items": [remote_task("c")]},
        )

        items = self.client_for(service).list_tasks("google-list-1")

        self.assertEqual([i["id"] for i in items], ["a", "b", "c"])

    def test_list_tasks_asks_for_deleted_and_hidden(self):
        service = self.service_returning({"items": []})

        self.client_for(service).list_tasks("google-list-1")

        kwargs = service.tasks.return_value.list.call_args.kwargs
        self.assertTrue(kwargs["showDeleted"])
        self.assertTrue(kwargs["showHidden"])
        self.assertTrue(kwargs["showCompleted"])
        self.assertEqual(kwargs["tasklist"], "google-list-1")
        self.assertNotIn("updatedMin", kwargs)
        self.assertNotIn("pageToken", kwargs)

    def test_list_tasks_passes_the_watermark_through(self):
        service = self.service_returning({"items": []})

        self.client_for(service).list_tasks(
            "google-list-1", updated_min="2026-07-27T10:00:00.000Z"
        )

        kwargs = service.tasks.return_value.list.call_args.kwargs
        self.assertEqual(kwargs["updatedMin"], "2026-07-27T10:00:00.000Z")

    def test_list_lists_follows_every_page(self):
        service = self.service_returning(
            {"items": [remote_list("a")], "nextPageToken": "page-2"},
            {"items": [remote_list("b")]},
        )

        items = self.client_for(service).list_lists()

        self.assertEqual([i["id"] for i in items], ["a", "b"])

    def test_an_empty_response_is_not_an_error(self):
        service = self.service_returning({})

        self.assertEqual(self.client_for(service).list_tasks("google-list-1"), [])


class ConstraintTests(PullTestCase):
    def test_one_external_id_per_owner(self):
        """Two overlapping pulls must not both insert the same remote task."""
        task_list = self.local_list()
        self.local_task(task_list, external_id="google-task-1")

        with self.assertRaises(IntegrityError), transaction.atomic():
            self.local_task(task_list, external_id="google-task-1")

    def test_unsynced_records_are_exempt(self):
        """The constraint is partial: blank external_ids do not collide."""
        task_list = self.local_list()
        self.local_task(task_list, external_id="")
        self.local_task(task_list, external_id="")

        self.assertEqual(
            Task.objects.filter(owner_id=self.owner_id, external_id="").count(), 2
        )


class FieldMappingTests(TestCase):
    def test_maps_every_google_owned_field(self):
        fields = remote_to_task_fields(
            remote_task(notes="with oats", due="2026-08-01T00:00:00.000Z")
        )

        self.assertEqual(fields["title"], "Buy milk")
        self.assertEqual(fields["notes"], "with oats")
        self.assertEqual(fields["status"], "needsAction")
        self.assertEqual(fields["position"], "00001")
        self.assertEqual(fields["etag"], "task-etag")
        self.assertEqual(fields["due"].date().isoformat(), "2026-08-01")
        self.assertFalse(fields["deleted"])
        self.assertFalse(fields["hidden"])

    def test_never_maps_local_only_fields(self):
        fields = remote_to_task_fields(remote_task())

        for local_only in ("priority", "tags", "color", "task_list", "owner_id"):
            self.assertNotIn(local_only, fields)

    def test_unparseable_timestamp_becomes_none(self):
        fields = remote_to_task_fields(remote_task(due="not a date"))

        self.assertIsNone(fields["due"])

    def test_hidden_is_carried(self):
        fields = remote_to_task_fields(remote_task(hidden=True))

        self.assertTrue(fields["hidden"])
