import re
import uuid
from django.utils.formats import reset_format_cache
from rest_framework.test import APITestCase
from django.urls import reverse
from todos.models import Tag, Task, TaskList
from todos.services.task_service import TaskService
from users.models import User
from unittest.mock import patch
from django.contrib.auth.models import AnonymousUser


def auth_patch(user_id):
    """
    Patch VerisafeJWTAuthentication.authenticate to inject user_id onto the
    request without requiring a real JWT token.
    """

    def fake_authenticate(self, request):
        request.user_id = str(user_id)
        return (AnonymousUser(), None)

    return patch(
        "keep_up.verisafe_jwt_authentication.VerisafeJWTAuthentication.authenticate",
        new=fake_authenticate,
    )


class TaskListTests(APITestCase):
    def setUp(self) -> None:
        self.test_user = User.objects.create(name="Test User")
        return super().setUp()

    def test_task_list_create_view(self):

        with auth_patch(self.test_user.user_id):
            response = self.client.post(
                reverse("todos:tasklist-create"),
                data={
                    "title": "School Tasks",
                    "color": "#2E428B",
                },
            )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["color"], "#2E428B")
        self.assertEqual(response.data["is_default"], False)

    def test_get_user_task_lists(self):
        with auth_patch(self.test_user.user_id):
            response = self.client.get(
                reverse("todos:tasklist-retrieve"),
            )

        self.assertEqual(response.status_code, 200)
        self.assertIsNotNone(response.data)

    def test_get_user_default_task_list(self):
        with auth_patch(self.test_user.user_id):
            response = self.client.get(
                reverse("todos:tasklist-default"),
            )

        self.assertEqual(response.status_code, 200)
        self.assertIsNotNone(response.data)

    def test_get_task_list_by_id_success(self):
        with auth_patch(self.test_user.user_id):
            response = self.client.post(
                reverse("todos:tasklist-create"),
                data={
                    "title": "School Tasks",
                    "color": "#2E428B",
                },
            )

        list_id = response.data["id"]

        url = reverse("todos:tasklist-detail", kwargs={"list_id": str(list_id)})

        with auth_patch(self.test_user.user_id):
            response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["id"], str(list_id))
        self.assertEqual(response.data["title"], "School Tasks")

    def test_get_task_list_by_id_not_found(self):
        url = reverse(
            "todos:tasklist-detail",
            kwargs={"list_id": "d062511c-3e7e-4b37-b883-985c02b03009"},
        )

        with auth_patch(self.test_user.user_id):
            response = self.client.get(url)

        self.assertEqual(response.status_code, 404)

    def test_update_task_list_success(self):
        with auth_patch(self.test_user.user_id):
            create_res = self.client.post(
                reverse("todos:tasklist-create"),
                data={"title": "Old Title", "color": "#000000"},
                format="json",
            )

        list_id = create_res.data["id"]
        update_url = reverse("todos:tasklist-update", kwargs={"list_id": list_id})

        update_data = {"title": "New Updated Title"}

        with auth_patch(self.test_user.user_id):
            response = self.client.patch(update_url, data=update_data, format="json")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["title"], "New Updated Title")
        self.assertEqual(response.data["color"], "#000000")

    def test_update_task_list_protected_fields(self):
        with auth_patch(self.test_user.user_id):
            create_res = self.client.post(
                reverse("todos:tasklist-create"), data={"title": "Protected Test"}
            )

        list_id = create_res.data["id"]
        update_url = reverse("todos:tasklist-update", kwargs={"list_id": list_id})

        bad_data = {"sync_status": "synced"}

        with auth_patch(self.test_user.user_id):
            response = self.client.patch(
                update_url,
                data=bad_data,
                format="json",
            )

        self.assertEqual(response.status_code, 200)

        self.assertNotEqual(response.data.get("sync_status"), "synced")

    def test_delete_task_list_success(self):
        with auth_patch(self.test_user.user_id):
            create_res = self.client.post(
                reverse("todos:tasklist-create"),
                data={"title": "Temporary List", "is_default": False},
                format="json",
            )
        list_id = create_res.data["id"]
        delete_url = reverse("todos:tasklist-delete", kwargs={"list_id": list_id})

        with auth_patch(self.test_user.user_id):
            response = self.client.delete(delete_url)

        self.assertEqual(response.status_code, 204)

        self.assertTrue(TaskList.objects.get(id=list_id).deleted)

    def test_delete_default_list_fails(self):
        with auth_patch(self.test_user.user_id):
            create_res = self.client.post(
                reverse("todos:tasklist-create"),
                data={"title": "Primary List", "is_default": True},
                format="json",
            )
        list_id = create_res.data["id"]
        delete_url = reverse("todos:tasklist-delete", kwargs={"list_id": list_id})

        with auth_patch(self.test_user.user_id):
            response = self.client.delete(delete_url)

        self.assertEqual(response.status_code, 400)
        self.assertIn("Cannot delete the default list", response.data["message"])

        self.assertFalse(TaskList.objects.get(id=list_id).deleted)


class TagApiTests(APITestCase):
    def setUp(self):
        self.user_id = "8acbe501-43d6-48e3-a02f-7201a7447e91"
        self.list_url = reverse("todos:tag-list-create")

    def test_create_tag_api_success(self):
        payload = {"name": "Urgent", "color": "#FF0000"}

        with auth_patch(self.user_id):
            response = self.client.post(self.list_url, data=payload, format="json")

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["name"], "Urgent")

    def test_create_duplicate_tag_returns_400(self):
        with auth_patch(self.user_id):
            self.client.post(self.list_url, data={"name": "Work"}, format="json")
            response = self.client.post(
                self.list_url, data={"name": "Work"}, format="json"
            )

        self.assertEqual(response.status_code, 400)
        self.assertIn("already exists", response.data["message"])

    def test_update_tag_api_success(self):
        tag = Tag.objects.create(owner_id=self.user_id, name="OldName")
        url = reverse("todos:tag-detail", kwargs={"tag_id": tag.id})

        with auth_patch(self.user_id):
            response = self.client.patch(url, data={"name": "NewName"}, format="json")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["name"], "NewName")

    def test_delete_tag_api_success(self):
        tag = Tag.objects.create(owner_id=self.user_id, name="To Delete")
        url = reverse("todos:tag-detail", kwargs={"tag_id": tag.id})

        with auth_patch(self.user_id):
            response = self.client.delete(url)

        self.assertEqual(response.status_code, 204)
        self.assertFalse(Tag.objects.filter(id=tag.id).exists())

    def test_get_tags_api_success(self):
        Tag.objects.create(owner_id=self.user_id, name="A-Tag")
        Tag.objects.create(owner_id=self.user_id, name="B-Tag")

        with auth_patch(self.user_id):
            response = self.client.get(self.list_url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 2)


class TaskApiTests(APITestCase):
    def setUp(self) -> None:
        self.test_user = User.objects.create(name="Test User")
        return super().setUp()

    def test_task_create_view(self):
        with auth_patch(self.test_user.user_id):
            response = self.client.post(
                reverse("todos:task-create"),
                data={
                    "notes": "Hi there",
                    "title": "Hello there!",
                },
            )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["notes"], "Hi there")
        self.assertEqual(response.data["title"], "Hello there!")

    def test_task_list_view(self):
        TaskService.create_task(
            self.test_user.user_id,
            "Task 1",
            "Notes 1",
        )
        TaskService.create_task(
            self.test_user.user_id,
            "Task 2",
            "Notes 2",
        )

        with auth_patch(self.test_user.user_id):
            response = self.client.get(reverse("todos:task-list"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 2)
        self.assertEqual(response.data[0]["title"], "Task 1")
        self.assertEqual(response.data[1]["title"], "Task 2")

    def test_task_retrieve_view(self):
        task = TaskService.create_task(
            self.test_user.user_id,
            "Test Task",
            "Test Notes",
        )

        with auth_patch(self.test_user.user_id):
            response = self.client.get(
                reverse("todos:task-retrieve", kwargs={"task_id": task.id})
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["id"], str(task.id))
        self.assertEqual(response.data["title"], "Test Task")
        self.assertEqual(response.data["notes"], "Test Notes")

    def test_task_retrieve_view_not_found(self):
        with auth_patch(self.test_user.user_id):
            response = self.client.get(
                reverse("todos:task-retrieve", kwargs={"task_id": uuid.uuid4()})
            )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.data["message"], "Task not found")

    def test_task_update_view(self):
        task = TaskService.create_task(
            self.test_user.user_id,
            "Original Title",
            "Original Notes",
        )

        with auth_patch(self.test_user.user_id):
            response = self.client.patch(
                reverse("todos:task-update", kwargs={"task_id": task.id}),
                data={
                    "title": "Updated Title",
                    "notes": "Updated Notes",
                },
                format="json",
            )

            print(response.json())

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["title"], "Updated Title")
        self.assertEqual(response.data["notes"], "Updated Notes")

    def test_task_update_view_empty_title(self):
        task = TaskService.create_task(
            self.test_user.user_id,
            "Original Title",
            "Original Notes",
        )

        with auth_patch(self.test_user.user_id):
            response = self.client.patch(
                reverse("todos:task-update", kwargs={"task_id": task.id}),
                data={
                    "title": "",
                },
                format="json",
            )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["message"], "Task title cannot be empty.")

    def test_task_delete_view(self):
        task = TaskService.create_task(
            self.test_user.user_id,
            "Task to Delete",
            "Notes",
        )

        with auth_patch(self.test_user.user_id):
            response = self.client.delete(
                reverse("todos:task-delete", kwargs={"task_id": task.id})
            )

        self.assertEqual(response.status_code, 204)

        # Verify task is soft-deleted
        deleted_task = Task.objects.get(id=task.id)
        self.assertTrue(deleted_task.deleted)

    def test_task_complete_view(self):
        task = TaskService.create_task(self.test_user.user_id, "Complete Me", "")

        with auth_patch(self.test_user.user_id):
            response = self.client.post(
                reverse("todos:task-complete", kwargs={"task_id": task.id})
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["status"], "completed")
        self.assertIsNotNone(response.data["completed"])

    def test_task_reopen_view(self):
        task = TaskService.create_task(self.test_user.user_id, "Reopen Me", "")
        TaskService.complete_task(self.test_user.user_id, task.id)

        with auth_patch(self.test_user.user_id):
            response = self.client.post(
                reverse("todos:task-reopen", kwargs={"task_id": task.id})
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["status"], "needsAction")
        self.assertIsNone(response.data["completed"])

    def test_task_move_to_list_view(self):
        new_list = TaskList.objects.create(
            owner_id=self.test_user.user_id, title="New List"
        )
        task = TaskService.create_task(self.test_user.user_id, "Move Me", "")

        with auth_patch(self.test_user.user_id):
            response = self.client.post(
                reverse("todos:task-move", kwargs={"task_id": task.id}),
                data={"task_list_id": str(new_list.id)},
                format="json",
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(str(response.data["task_list"]), str(new_list.id))

    def test_convert_to_subtask_view(self):
        parent = TaskService.create_task(self.test_user.user_id, "Parent Task", "")
        child = TaskService.create_task(self.test_user.user_id, "Child Task", "")

        with auth_patch(self.test_user.user_id):
            response = self.client.post(
                reverse("todos:task-convert-to-subtask", kwargs={"task_id": child.id}),
                data={"parent_task_id": str(parent.id)},
                format="json",
            )

        print(response.json())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["parent"], str(parent.id))

    def test_promote_subtask_view(self):
        parent = TaskService.create_task(self.test_user.user_id, "Parent Task", "")
        child = Task.objects.create(
            owner_id=self.test_user.user_id,
            title="Subtask",
            parent=parent,
            task_list=parent.task_list,
        )

        with auth_patch(self.test_user.user_id):
            response = self.client.post(
                reverse("todos:task-promote", kwargs={"task_id": child.id})
            )

        self.assertEqual(response.status_code, 200)

    def test_task_move_missing_payload(self):
        task = TaskService.create_task(self.test_user.user_id, "No Payload", "")

        with auth_patch(self.test_user.user_id):
            response = self.client.post(
                reverse("todos:task-move", kwargs={"task_id": task.id}),
                data={},  # Missing task_list_id
                format="json",
            )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["message"], "task_list_id is required")
