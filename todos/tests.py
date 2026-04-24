"""
Copyright (c) 2025 Open Crafts Interactive. All Rights Reserved.

Tests for todos views.
Authentication is mocked — VerisafeJWTAuthentication is patched to inject
request.user_id directly, keeping tests decoupled from JWT internals.
"""

import uuid
from unittest.mock import patch
from django.contrib.auth.models import AnonymousUser
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient
from todos.models import Task

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_task(owner_id, **kwargs):
    """Create a Task with sensible defaults for testing."""
    return Task.objects.create(
        owner_id=owner_id,
        title=kwargs.get("title", "Test Task"),
        notes=kwargs.get("notes", ""),
        status=kwargs.get("status", "needsAction"),
        due=kwargs.get("due", None),
        deleted=kwargs.get("deleted", False),
        external_id="",
        etag="",
        self_link="",
        web_view_link="",
        position="",
    )


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


def unauthed_patch():
    """Patch authenticate to simulate a missing/invalid token."""
    from rest_framework.exceptions import AuthenticationFailed

    def fake_authenticate(self, request):
        raise AuthenticationFailed("Wrong token format. Expected 'Bearer token'")

    return patch(
        "keep_up.verisafe_jwt_authentication.VerisafeJWTAuthentication.authenticate",
        new=fake_authenticate,
    )


# ---------------------------------------------------------------------------
# Base
# ---------------------------------------------------------------------------


class BaseTaskTestCase(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user_id = uuid.uuid4()
        self.other_user_id = uuid.uuid4()


# ---------------------------------------------------------------------------
# CreateTodoApiView
# ---------------------------------------------------------------------------


class CreateTodoApiViewTests(BaseTaskTestCase):

    def test_create_task_returns_201(self):
        with auth_patch(self.user_id):
            response = self.client.post(
                reverse("todos:create"),
                data={"title": "Buy groceries"},
                format="json",
            )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["title"], "Buy groceries")

    def test_create_task_persists_to_db(self):
        with auth_patch(self.user_id):
            self.client.post(
                reverse("todos:create"),
                data={"title": "Buy groceries"},
                format="json",
            )
        self.assertEqual(Task.objects.filter(owner_id=self.user_id).count(), 1)

    def test_create_task_defaults_status_to_needs_action(self):
        with auth_patch(self.user_id):
            response = self.client.post(
                reverse("todos:create"),
                data={"title": "Buy groceries"},
                format="json",
            )
        self.assertEqual(response.data["status"], "needsAction")

    def test_create_task_with_notes_and_due(self):
        with auth_patch(self.user_id):
            response = self.client.post(
                reverse("todos:create"),
                data={
                    "title": "Doctor appointment",
                    "notes": "Bring insurance card",
                    "due": "2025-06-01T09:00:00Z",
                },
                format="json",
            )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["notes"], "Bring insurance card")

    def test_create_task_missing_title_returns_400(self):
        with auth_patch(self.user_id):
            response = self.client.post(
                reverse("todos:create"),
                data={"notes": "No title here"},
                format="json",
            )
        self.assertEqual(response.status_code, 400)
        self.assertIn("message", response.data)

    def test_create_task_unauthenticated_returns_403(self):
        with unauthed_patch():
            response = self.client.post(
                reverse("todos:create"),
                data={"title": "Buy groceries"},
                format="json",
            )
        self.assertEqual(response.status_code, 403)


# ---------------------------------------------------------------------------
# UpdateTodoApiView
# ---------------------------------------------------------------------------


class UpdateTodoApiViewTests(BaseTaskTestCase):

    def test_update_title_returns_200(self):
        task = make_task(self.user_id, title="Old title")
        with auth_patch(self.user_id):
            response = self.client.put(
                reverse("todos:update", kwargs={"task_id": task.id}),
                data={"title": "New title"},
                format="json",
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["title"], "New title")

    def test_update_persists_to_db(self):
        task = make_task(self.user_id, title="Old title")
        with auth_patch(self.user_id):
            self.client.put(
                reverse("todos:update", kwargs={"task_id": task.id}),
                data={"title": "New title"},
                format="json",
            )
        task.refresh_from_db()
        self.assertEqual(task.title, "New title")

    def test_update_partial_fields_only_changes_provided(self):
        task = make_task(self.user_id, title="Keep me", notes="Keep me too")
        with auth_patch(self.user_id):
            self.client.put(
                reverse("todos:update", kwargs={"task_id": task.id}),
                data={"title": "Changed"},
                format="json",
            )
        task.refresh_from_db()
        self.assertEqual(task.title, "Changed")
        self.assertEqual(task.notes, "Keep me too")

    def test_update_nonexistent_task_returns_404(self):
        with auth_patch(self.user_id):
            response = self.client.put(
                reverse("todos:update", kwargs={"task_id": uuid.uuid4()}),
                data={"title": "Doesn't matter"},
                format="json",
            )
        self.assertEqual(response.status_code, 404)

    def test_update_another_users_task_returns_404(self):
        task = make_task(self.other_user_id)
        with auth_patch(self.user_id):
            response = self.client.put(
                reverse("todos:update", kwargs={"task_id": task.id}),
                data={"title": "Hijack"},
                format="json",
            )
        self.assertEqual(response.status_code, 404)

    def test_update_deleted_task_returns_404(self):
        task = make_task(self.user_id, deleted=True)
        with auth_patch(self.user_id):
            response = self.client.put(
                reverse("todos:update", kwargs={"task_id": task.id}),
                data={"title": "Ghost"},
                format="json",
            )
        self.assertEqual(response.status_code, 404)

    def test_update_unauthenticated_returns_403(self):
        task = make_task(self.user_id)
        with unauthed_patch():
            response = self.client.put(
                reverse("todos:update", kwargs={"task_id": task.id}),
                data={"title": "Nope"},
                format="json",
            )
        self.assertEqual(response.status_code, 403)


# ---------------------------------------------------------------------------
# CompleteTodoApiView
# ---------------------------------------------------------------------------


class CompleteTodoApiViewTests(BaseTaskTestCase):

    def test_toggle_needs_action_to_completed(self):
        task = make_task(self.user_id, status="needsAction")
        with auth_patch(self.user_id):
            response = self.client.put(
                reverse("todos:complete", kwargs={"task_id": task.id}),
                format="json",
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["status"], "completed")

    def test_toggle_completed_to_needs_action(self):
        task = make_task(self.user_id, status="completed")
        with auth_patch(self.user_id):
            response = self.client.put(
                reverse("todos:complete", kwargs={"task_id": task.id}),
                format="json",
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["status"], "needsAction")

    def test_toggle_persists_to_db(self):
        task = make_task(self.user_id, status="needsAction")
        with auth_patch(self.user_id):
            self.client.put(
                reverse("todos:complete", kwargs={"task_id": task.id}),
                format="json",
            )
        task.refresh_from_db()
        self.assertEqual(task.status, "completed")

    def test_complete_nonexistent_task_returns_404(self):
        with auth_patch(self.user_id):
            response = self.client.put(
                reverse("todos:complete", kwargs={"task_id": uuid.uuid4()}),
                format="json",
            )
        self.assertEqual(response.status_code, 404)

    def test_complete_another_users_task_returns_404(self):
        task = make_task(self.other_user_id)
        with auth_patch(self.user_id):
            response = self.client.put(
                reverse("todos:complete", kwargs={"task_id": task.id}),
                format="json",
            )
        self.assertEqual(response.status_code, 404)

    def test_complete_unauthenticated_returns_403(self):
        task = make_task(self.user_id)
        with unauthed_patch():
            response = self.client.put(
                reverse("todos:complete", kwargs={"task_id": task.id}),
                format="json",
            )
        self.assertEqual(response.status_code, 403)


# ---------------------------------------------------------------------------
# ListTodoApiView
# ---------------------------------------------------------------------------


class ListTodoApiViewTests(BaseTaskTestCase):

    def test_list_returns_200(self):
        with auth_patch(self.user_id):
            response = self.client.get(reverse("todos:list"))
        self.assertEqual(response.status_code, 200)

    def test_list_returns_only_users_tasks(self):
        make_task(self.user_id, title="Mine")
        make_task(self.other_user_id, title="Not mine")
        with auth_patch(self.user_id):
            response = self.client.get(reverse("todos:list"))
        titles = [t["title"] for t in response.data["results"]]
        self.assertIn("Mine", titles)
        self.assertNotIn("Not mine", titles)

    def test_list_excludes_deleted_tasks(self):
        make_task(self.user_id, title="Active")
        make_task(self.user_id, title="Deleted", deleted=True)
        with auth_patch(self.user_id):
            response = self.client.get(reverse("todos:list"))
        titles = [t["title"] for t in response.data["results"]]
        self.assertIn("Active", titles)
        self.assertNotIn("Deleted", titles)

    def test_list_empty_when_no_tasks(self):
        with auth_patch(self.user_id):
            response = self.client.get(reverse("todos:list"))
        self.assertEqual(response.data["results"], [])

    def test_list_unauthenticated_returns_403(self):
        with unauthed_patch():
            response = self.client.get(reverse("todos:list"))
        self.assertEqual(response.status_code, 403)


# ---------------------------------------------------------------------------
# DeleteTaskAPIView
# ---------------------------------------------------------------------------


class DeleteTaskAPIViewTests(BaseTaskTestCase):

    def test_delete_returns_204(self):
        task = make_task(self.user_id)
        with auth_patch(self.user_id):
            response = self.client.delete(
                reverse("todos:delete", kwargs={"task_id": task.id})
            )
        self.assertEqual(response.status_code, 204)

    def test_delete_soft_deletes_task(self):
        task = make_task(self.user_id)
        with auth_patch(self.user_id):
            self.client.delete(reverse("todos:delete", kwargs={"task_id": task.id}))
        task.refresh_from_db()
        self.assertTrue(task.deleted)

    def test_delete_task_no_longer_in_list(self):
        task = make_task(self.user_id, title="Soon gone")
        with auth_patch(self.user_id):
            self.client.delete(reverse("todos:delete", kwargs={"task_id": task.id}))
            response = self.client.get(reverse("todos:list"))
        titles = [t["title"] for t in response.data["results"]]
        self.assertNotIn("Soon gone", titles)

    def test_delete_nonexistent_task_returns_404(self):
        with auth_patch(self.user_id):
            response = self.client.delete(
                reverse("todos:delete", kwargs={"task_id": uuid.uuid4()})
            )
        self.assertEqual(response.status_code, 404)

    def test_delete_another_users_task_returns_404(self):
        task = make_task(self.other_user_id)
        with auth_patch(self.user_id):
            response = self.client.delete(
                reverse("todos:delete", kwargs={"task_id": task.id})
            )
        self.assertEqual(response.status_code, 404)

    def test_delete_already_deleted_task_returns_404(self):
        task = make_task(self.user_id, deleted=True)
        with auth_patch(self.user_id):
            response = self.client.delete(
                reverse("todos:delete", kwargs={"task_id": task.id})
            )
        self.assertEqual(response.status_code, 404)

    def test_delete_unauthenticated_returns_403(self):
        task = make_task(self.user_id)
        with unauthed_patch():
            response = self.client.delete(
                reverse("todos:delete", kwargs={"task_id": task.id})
            )
        self.assertEqual(response.status_code, 403)


# ---------------------------------------------------------------------------
# SyncTasksApiView
# ---------------------------------------------------------------------------


class SyncTasksApiViewTests(BaseTaskTestCase):

    def test_sync_returns_202(self):
        with auth_patch(self.user_id):
            response = self.client.post(reverse("todos:sync"))
        self.assertEqual(response.status_code, 202)

    def test_sync_returns_queued_message(self):
        with auth_patch(self.user_id):
            response = self.client.post(reverse("todos:sync"))
        self.assertEqual(response.data["message"], "Sync queued successfully")

    def test_sync_unauthenticated_returns_403(self):
        with unauthed_patch():
            response = self.client.post(reverse("todos:sync"))
        self.assertEqual(response.status_code, 403)
