from math import ceil
from django.test import TestCase
from django.utils import timezone
from todos.models import SyncStatus, Tag, Task
from .task_list_service import TaskListService
from .tag_service import TagService
from .task_service import TaskService
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


class TaskServiceTest(TestCase):
    def setUp(self) -> None:
        self.test_user = User.objects.create(name="Test User")
        self.user_id = str(self.test_user.user_id)
        self.task_service = TaskService()
        return super().setUp()

    def test_task_service_create_basic(self):
        time_due = timezone.now()
        created_task = self.task_service.create_task(
            self.user_id, "Hello there", "Hi there", time_due, "high"
        )

        self.assertIsNotNone(created_task)
        self.assertEqual(created_task.title, "Hello there")
        self.assertEqual(
            created_task.notes,
            "Hi there",
        )
        self.assertEqual(created_task.due, time_due)

    def test_task_service_cannot_create_task_with_empty_title(self):
        with self.assertRaises(ValueError):
            # Notes can be empty but title cannot be empty!!
            self.task_service.create_task(self.user_id, "", "")

    def test_task_service_creates_task_with_default_task_list(self):
        created_task = self.task_service.create_task(
            self.user_id,
            "Hi there!",
            "",
        )
        second_created_task = self.task_service.create_task(
            self.user_id, "Hi again", ""
        )
        self.assertIsNotNone(created_task.task_list)
        self.assertIsNotNone(second_created_task.task_list)

        self.assertEqual(second_created_task.task_list, created_task.task_list)

    def test_task_service_updates_task_successfully(self):
        created_task = self.task_service.create_task(
            self.user_id,
            "Hi there!",
            "",
        )

        updated_task = self.task_service.update_task(
            self.user_id,
            created_task.id,
            title="Hello, there!",
            notes="something",
        )

        self.assertEqual(updated_task.id, created_task.id)
        self.assertNotEqual(updated_task.title, created_task.title)
        self.assertEqual(updated_task.title, "Hello, there!")

    def test_task_service_updates_task_with_sanity(self):
        created_task = self.task_service.create_task(
            self.user_id,
            "Hi there!",
            "",
        )

        with self.assertRaises(ValueError):
            _ = self.task_service.update_task(
                self.user_id,
                created_task.id,
                title="",  # title cannot be empty
                notes="something",
            )

    def test_task_service_completes_task_successfully(self):
        created_task = self.task_service.create_task(
            self.user_id,
            "Hi there!",
            "",
        )

        self.assertEqual(created_task.status, "needsAction")

        completed_task = self.task_service.complete_task(self.user_id, created_task.id)

        self.assertEqual(completed_task.id, created_task.id)
        self.assertEqual(completed_task.title, created_task.title)
        self.assertEqual(completed_task.notes, created_task.notes)
        self.assertEqual(completed_task.status, "completed")

    def test_task_service_reopens_tasks_successfully(self):
        created_task = self.task_service.create_task(
            self.user_id,
            "Hi there!",
            "",
        )

        self.assertEqual(created_task.status, "needsAction")

        completed_task = self.task_service.complete_task(self.user_id, created_task.id)

        self.assertEqual(completed_task.id, created_task.id)
        self.assertEqual(completed_task.title, created_task.title)
        self.assertEqual(completed_task.notes, created_task.notes)
        self.assertEqual(completed_task.status, "completed")

        reopened_task = self.task_service.reopen_task(self.user_id, completed_task.id)
        self.assertEqual(reopened_task.id, created_task.id)
        self.assertEqual(reopened_task.title, created_task.title)
        self.assertEqual(reopened_task.notes, created_task.notes)
        self.assertEqual(reopened_task.status, "needsAction")

    def test_task_service_soft_deletes_tasks(self):
        created_task = self.task_service.create_task(
            self.user_id,
            "Hi there!",
            "",
        )

        self.task_service.delete_task(owner_id=self.user_id, task_id=created_task.id)

        retrieved_task = Task.objects.filter(id=created_task.id).first()

        self.assertIsNotNone(retrieved_task)

        self.assertEqual(retrieved_task.deleted, True)

        with self.assertRaises(Task.DoesNotExist):
            self.task_service.delete_task(
                owner_id=self.user_id, task_id=created_task.id
            )

    def test_task_service_retrieves_user_tasks(self):
        self.task_service.create_task(
            self.user_id,
            "Hi there!",
            "",
        )

        self.task_service.create_task(
            self.user_id,
            "Hi there!",
            "",
        )

        retrieved_tasks = self.task_service.get_user_tasks(self.user_id)

        self.assertIsNotNone(retrieved_tasks)
        self.assertEqual(len(retrieved_tasks), 2)

    def test_task_service_assigns_parent_with_sanity(self):
        task_1 = self.task_service.create_task(
            self.user_id,
            "Hi there!",
            "",
        )

        task_1_subtask = self.task_service.create_task(
            owner_id=self.user_id, title="Hi there!", notes="", parent=task_1
        )

        self.assertEqual(task_1_subtask.parent.id, task_1.id)

    def test_task_service_moves_to_task_list_successfully(self):
        created_task = self.task_service.create_task(
            self.user_id,
            "Hi there!",
            "",
        )

        school_task_list = TaskListService.create_task_list(
            owner_id=self.user_id, title="School"
        )

        self.assertNotEqual(created_task.task_list, school_task_list.id)

        with self.assertRaises(Task.DoesNotExist):
            moved_task = self.task_service.move_task_to_list(
                "74550d37-ccad-4591-bc1d-61614347afef",
                created_task.id,
                school_task_list.id,
            )

        moved_task = self.task_service.move_task_to_list(
            self.user_id, created_task.id, school_task_list.id
        )

        self.assertEqual(moved_task.title, created_task.title)
        self.assertEqual(moved_task.notes, created_task.notes)
        self.assertEqual(str(moved_task.owner_id), created_task.owner_id)
        self.assertNotEqual(moved_task.task_list, created_task.task_list)
