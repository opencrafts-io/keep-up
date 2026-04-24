from django.test import TestCase
from todos.models import SyncStatus
from .task_list_service import TaskListService
from users.models import User


class TaskListServiceTest(TestCase):
    def setUp(self) -> None:
        self.test_user = User.objects.create(name="Test User")
        self.task_list_service = TaskListService()
        return super().setUp()

    def test_create_task_list(self):
        task_list = self.task_list_service.create_task_list(
            self.test_user.user_id, "school", is_default=False
        )

        self.assertEqual(task_list.owner_id, self.test_user.user_id)
        self.assertEqual(task_list.title, "school")
        self.assertEqual(task_list.is_default, False)
        self.assertEqual(task_list.sync_status, SyncStatus.PENDING)
        self.assertIsNotNone(task_list.created_at)

    def test_update_task_list(self):
        task_list = self.task_list_service.create_task_list(
            self.test_user.user_id, "school", is_default=False
        )

        with self.assertRaises(ValueError):
            updated_task = self.task_list_service.update_task_list(
                owner_id=self.test_user.user_id,
                list_id=task_list.id,
                external_id="some-random_id",
                etag="some-random_etag",
                sync_status=SyncStatus.SYNCED,
            )

            self.assertIsNone(
                updated_task,
                "updated_task should be none since the method should raise an error",
            )

        updated_task = self.task_list_service.update_task_list(
            owner_id=self.test_user.user_id,
            list_id=task_list.id,
            title="random title",
            color="#FFB6AB",
            is_default=True,
        )

        self.assertEqual(updated_task.title, "random title")
        self.assertEqual(updated_task.color, "#FFB6AB")
        self.assertEqual(updated_task.owner_id, self.test_user.user_id)
        self.assertEqual(updated_task.is_default, True)

    def test_retieve_user_task_lists(self):
        self.task_list_service.create_task_list(
            self.test_user.user_id, "school", is_default=False
        )
        self.task_list_service.create_task_list(
            self.test_user.user_id, "home", is_default=False
        )

        user_lists = self.task_list_service.get_user_lists(
            owner_id=self.test_user.user_id
        )

        self.assertEqual(len(user_lists), 2)

    def test_retrieve_user_list_by_id(self):
        school_tasks = self.task_list_service.create_task_list(
            self.test_user.user_id, "school", is_default=False
        )
        self.task_list_service.create_task_list(
            self.test_user.user_id, "home", is_default=False
        )

        retrieved_school_task = self.task_list_service.get_task_list(
            self.test_user.user_id, school_tasks.id
        )

        self.assertIsNotNone(retrieved_school_task)
        self.assertEqual(retrieved_school_task.title, "school")
        self.assertEqual(retrieved_school_task.id, school_tasks.id)

    def test_delete_user_task_list(self):
        school_tasks = self.task_list_service.create_task_list(
            self.test_user.user_id, "school", is_default=False
        )
        home_tasks = self.task_list_service.create_task_list(
            self.test_user.user_id, "home", is_default=False
        )

        self.task_list_service.delete_task_list(self.test_user.user_id, school_tasks.id)
        retrieved = self.task_list_service.get_user_lists(self.test_user.user_id)
        self.assertIsNotNone(retrieved)
        self.assertEqual(len(retrieved), 1)
        self.assertEqual(retrieved[0].id, home_tasks.id)

    def test_get_or_create_user_default_list(self):
        default_list = self.task_list_service.get_or_create_default_list(
            self.test_user.user_id
        )

        self.assertIsNotNone(default_list)
        self.assertEqual(default_list.title, "My Tasks")
        self.assertEqual(default_list.is_default, True)

    def test_task_list_marked_synced(self):
        default_list = self.task_list_service.get_or_create_default_list(
            self.test_user.user_id
        )

        synced_default_list = self.task_list_service.mark_synced(
            default_list.id,
            "09b9c40b-5037-4ae1-8d44-33ae58701196",
            "some-random-garbage",
        )

        self.assertIsNotNone(synced_default_list)
        self.assertEqual(synced_default_list.sync_status, SyncStatus.SYNCED)
        self.assertNotEqual(synced_default_list.updated_at, default_list.updated_at)

    def test_task_list_marked_sync_failed(self):
        default_list = self.task_list_service.get_or_create_default_list(
            self.test_user.user_id
        )
        synced_default_list = self.task_list_service.mark_sync_failed(
            default_list.id,
            reason="The google api service is unreachable at the moment",
        )

        self.assertIsNotNone(synced_default_list)
        self.assertEqual(synced_default_list.sync_status, SyncStatus.FAILED)
