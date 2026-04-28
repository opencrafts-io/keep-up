from django.test import TestCase
from todos.models import SyncStatus, Tag
from .task_list_service import TaskListService
from .tag_service import TagService
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

    def test_retrieve_user_task_lists(self):
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


class TagServiceTest(TestCase):
    def setUp(self) -> None:
        self.test_user = User.objects.create(name="Test User")
        self.user_id = str(self.test_user.user_id)
        self.tag_service = TagService()
        return super().setUp()

    def test_create_tag_success(self):
        tag = self.tag_service.create_tag(self.user_id, "Art", color="#E16E92")
        self.assertEqual(tag.name, "Art")
        self.assertEqual(tag.color, "#E16E92")
        self.assertEqual(Tag.objects.count(), 1)

    def test_create_tag_empty_name_fails(self):
        with self.assertRaises(ValueError) as cm:
            self.tag_service.create_tag(self.user_id, "  ")
        self.assertEqual(str(cm.exception), "Tag name cannot be empty.")

    def test_create_tag_duplicate_name_fails(self):
        self.tag_service.create_tag(self.user_id, "Work")

        with self.assertRaises(ValueError) as cm:
            self.tag_service.create_tag(self.user_id, "Work")
        self.assertIn("already exists", str(cm.exception))

    def test_update_tag_success(self):
        tag = self.tag_service.create_tag(self.user_id, "Old Name")

        updated_tag = self.tag_service.update_tag(
            self.user_id, tag.id, name="New Name", color="#000000"
        )

        self.assertEqual(updated_tag.name, "New Name")
        self.assertEqual(updated_tag.color, "#000000")

    def test_update_tag_duplicate_name_fails(self):
        self.tag_service.create_tag(self.user_id, "Tag1")
        tag2 = self.tag_service.create_tag(self.user_id, "Tag2")

        with self.assertRaises(ValueError):
            self.tag_service.update_tag(self.user_id, tag2.id, name="Tag1")

    def test_update_tag_wrong_owner_fails(self):
        other_user = User.objects.create(name="Other")
        tag = self.tag_service.create_tag(self.user_id, "My Tag")

        with self.assertRaises(Tag.DoesNotExist):
            self.tag_service.update_tag(str(other_user.user_id), tag.id, name="Steal")

    def test_delete_tag_success(self):
        tag = self.tag_service.create_tag(self.user_id, "DeleteMe")
        self.assertEqual(Tag.objects.count(), 1)

        self.tag_service.delete_tag(self.user_id, tag.id)
        self.assertEqual(Tag.objects.count(), 0)

    def test_get_user_tags_ordering(self):
        self.tag_service.create_tag(self.user_id, "C")
        self.tag_service.create_tag(self.user_id, "A")
        self.tag_service.create_tag(self.user_id, "B")

        tags = self.tag_service.get_user_tags(self.user_id)
        names = [t.name for t in tags]

        self.assertEqual(names, ["A", "B", "C"])
