from rest_framework.test import APITestCase
from django.urls import reverse
from todos.models import TaskList
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
        print(response.data)

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
