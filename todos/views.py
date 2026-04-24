"""
Copyright (c) 2025 Open Crafts Interactive. All Rights Reserved.

Views operating purely on local DB.
Google Tasks sync is handled asynchronously via Celery workers (not yet implemented).
"""

import logging
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.generics import ListAPIView
from keep_up.verisafe_jwt_authentication import VerisafeJWTAuthentication
from todos.models import Task
from todos.serializers import TaskSerializer
from utils.parse_date_time_to_iso_format import parse_date_time_to_iso_format

logger = logging.getLogger("keep_up")


class BaseTaskView(APIView):
    """Base view with common authentication and user_id extraction."""

    authentication_classes = [VerisafeJWTAuthentication]

    def get_user_id(self, request) -> tuple[str | None, Response | None]:
        """
        Extract user_id from request and return error response if missing.

        Returns:
            Tuple of (user_id, error_response)
            If successful: (user_id, None)
            If failed: (None, Response object)
        """
        user_id = getattr(request, "user_id", None)
        if not user_id:
            logger.error("Failed to extract user_id from JWT claims")
            return None, Response(
                data={
                    "message": "We couldn't extract your user id from the provided token. "
                    "Please ensure the token is valid and contains the necessary user data."
                },
                status=status.HTTP_403_FORBIDDEN,
            )
        return user_id, None


class CreateTodoApiView(BaseTaskView):
    """Creates a todo item in local DB."""

    def post(self, request, *args, **kwargs):
        user_id, error_response = self.get_user_id(request)
        if error_response:
            return error_response

        title = request.data.get("title")
        if not title:
            return Response(
                data={"message": "Title is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        due_str = request.data.get("due")
        due_date = parse_date_time_to_iso_format(due_str) if due_str else None

        task = Task.objects.create(
            owner_id=user_id,
            title=title,
            notes=request.data.get("notes"),
            parent=request.data.get("parent"),
            due=due_date,
            # Google Tasks fields — populated later by Celery sync worker
            external_id="",
            etag="",
            self_link="",
            web_view_link="",
            position="",
            status="needsAction",
        )

        serializer = TaskSerializer(task)
        return Response(data=serializer.data, status=status.HTTP_201_CREATED)


class UpdateTodoApiView(BaseTaskView):
    """Updates a todo item in local DB."""

    def put(self, request, *args, **kwargs):
        user_id, error_response = self.get_user_id(request)
        if error_response:
            return error_response

        task_id = kwargs.get("task_id")
        if not task_id:
            return Response(
                data={"message": "Task ID is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            task = Task.objects.get(id=task_id, owner_id=user_id, deleted=False)
        except Task.DoesNotExist:
            return Response(
                data={"message": "Task not found"},
                status=status.HTTP_404_NOT_FOUND,
            )

        if "title" in request.data:
            task.title = request.data["title"]
        if "notes" in request.data:
            task.notes = request.data["notes"]
        if "status" in request.data:
            task.status = request.data["status"]
        if "due" in request.data:
            due_str = request.data["due"]
            task.due = parse_date_time_to_iso_format(due_str) if due_str else None

        task.save()

        serializer = TaskSerializer(task)
        return Response(data=serializer.data, status=status.HTTP_200_OK)


class CompleteTodoApiView(BaseTaskView):
    """Toggles task completion status."""

    def put(self, request, *args, **kwargs):
        user_id, error_response = self.get_user_id(request)
        if error_response:
            return error_response

        task_id = kwargs.get("task_id")
        if not task_id:
            return Response(
                data={"message": "Task ID is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            task = Task.objects.get(id=task_id, owner_id=user_id, deleted=False)
        except Task.DoesNotExist:
            return Response(
                data={"message": "Task not found"},
                status=status.HTTP_404_NOT_FOUND,
            )

        task.status = "completed" if task.status != "completed" else "needsAction"
        task.save()

        serializer = TaskSerializer(task)
        return Response(data=serializer.data, status=status.HTTP_200_OK)


class ListTodoApiView(ListAPIView):
    """Lists tasks from local DB."""

    authentication_classes = [VerisafeJWTAuthentication]
    serializer_class = TaskSerializer

    def get_queryset(self):
        user_id = getattr(self.request, "user_id", None)
        if user_id:
            return Task.objects.filter(owner_id=user_id, deleted=False).order_by(
                "status", "due", "position"
            )
        return Task.objects.none()

    def list(self, request, *args, **kwargs):
        user_id = getattr(request, "user_id", None)
        if not user_id:
            logger.error("Failed to extract user_id from JWT claims")
            return Response(
                data={
                    "message": "We couldn't extract your user id from the provided token."
                },
                status=status.HTTP_403_FORBIDDEN,
            )

        return super().list(request, *args, **kwargs)


class DeleteTaskAPIView(BaseTaskView):
    """Soft-deletes a task from local DB."""

    def delete(self, request, *args, **kwargs):
        user_id, error_response = self.get_user_id(request)
        if error_response:
            return error_response

        task_id = kwargs.get("task_id")
        if not task_id:
            return Response(
                data={"message": "Task ID is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            task = Task.objects.get(id=task_id, owner_id=user_id, deleted=False)
        except Task.DoesNotExist:
            return Response(
                data={"message": "Task not found"},
                status=status.HTTP_404_NOT_FOUND,
            )

        task.deleted = True
        task.save()

        return Response(
            data={"message": "Task deleted successfully"},
            status=status.HTTP_204_NO_CONTENT,
        )


class SyncTasksApiView(BaseTaskView):
    """
    Triggers a background sync of local tasks with Google Tasks.
    Sync is handled asynchronously via Celery workers.
    """

    def post(self, request, *args, **kwargs):
        user_id, error_response = self.get_user_id(request)
        if error_response:
            return error_response

        # TODO: dispatch Celery task here e.g. sync_google_tasks.delay(user_id)

        return Response(
            data={"message": "Sync queued successfully"},
            status=status.HTTP_202_ACCEPTED,
        )
