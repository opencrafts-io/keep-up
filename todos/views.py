"""
Copyright (c) 2025 Open Crafts Interactive. All Rights Reserved.

Simplified views using the GoogleTasksService layer.
Much cleaner, more maintainable, and easier to test.
"""

import logging
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.generics import ListAPIView
from keep_up.verisafe_jwt_authentication import VerisafeJWTAuthentication
from todos.models import Task
from todos.serializers import TaskSerializer
from todos.services import GoogleTasksService
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
    """Creates a todo item in Google Tasks and local DB."""

    def post(self, request, *args, **kwargs):
        user_id, error_response = self.get_user_id(request)
        if error_response:
            return error_response

        service = GoogleTasksService(user_id)

        # Parse due date if provided
        due_str = request.data.get("due")
        due_date = parse_date_time_to_iso_format(due_str) if due_str else None

        task_data = {
            "title": request.data.get("title"),
            "notes": request.data.get("notes"),
            "parent": request.data.get("parent"),
            "due": due_date,
        }

        task, error = service.create_task(task_data)

        if error:
            return Response(
                data={"message": error},
                status=(
                    status.HTTP_400_BAD_REQUEST
                    if "required" in error.lower()
                    else status.HTTP_500_INTERNAL_SERVER_ERROR
                ),
            )

        serializer = TaskSerializer(task)
        return Response(data=serializer.data, status=status.HTTP_201_CREATED)


class UpdateTodoApiView(BaseTaskView):
    """Updates a todo item in Google Tasks and local DB."""

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

        service = GoogleTasksService(user_id)

        # Build update data from request
        update_data = {}
        if "title" in request.data:
            update_data["title"] = request.data["title"]
        if "notes" in request.data:
            update_data["notes"] = request.data["notes"]
        if "status" in request.data:
            update_data["status"] = request.data["status"]
        if "due" in request.data:
            due_str = request.data["due"]
            update_data["due"] = (
                parse_date_time_to_iso_format(due_str) if due_str else None
            )

        task, error = service.update_task(task_id, update_data)

        if error:
            return Response(
                data={"message": error},
                status=(
                    status.HTTP_404_NOT_FOUND
                    if "not found" in error.lower()
                    else status.HTTP_500_INTERNAL_SERVER_ERROR
                ),
            )

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

        service = GoogleTasksService(user_id)
        task, error = service.toggle_task_completion(task_id)

        if error:
            return Response(
                data={"message": error},
                status=(
                    status.HTTP_404_NOT_FOUND
                    if "not found" in error.lower()
                    else status.HTTP_500_INTERNAL_SERVER_ERROR
                ),
            )

        serializer = TaskSerializer(task)
        return Response(data=serializer.data, status=status.HTTP_200_OK)


class ListTodoApiView(ListAPIView):
    """
    Lists tasks from local DB.
    Optionally syncs with Google Tasks if requested.
    """

    authentication_classes = [VerisafeJWTAuthentication]
    serializer_class = TaskSerializer

    def get_queryset(self):
        """Return tasks for the authenticated user."""
        user_id = getattr(self.request, "user_id", None)
        if user_id:
            return Task.objects.filter(
                owner_id=user_id, deleted=False  # Don't show deleted tasks
            ).order_by("status", "due", "position")
        return Task.objects.none()

    def list(self, request, *args, **kwargs):
        """
        List tasks with optional sync.

        Add ?sync=true to URL to sync with Google Tasks before listing.
        """
        user_id = getattr(request, "user_id", None)
        if not user_id:
            logger.error("Failed to extract user_id from JWT claims")
            return Response(
                data={
                    "message": "We couldn't extract your user id from the provided token."
                },
                status=status.HTTP_403_FORBIDDEN,
            )

        # Check if sync is requested
        should_sync = request.query_params.get("sync", "false").lower() == "true"

        if should_sync:
            service = GoogleTasksService(user_id)
            count, error = service.sync_tasks()

            if error:
                logger.error(f"Sync failed: {error}")
                # Don't fail the request, just log the error
                # Still return local tasks

        # Use DRF's standard list behavior for pagination
        return super().list(request, *args, **kwargs)


class DeleteTaskAPIView(BaseTaskView):
    """Deletes a task from Google Tasks and local DB."""

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

        service = GoogleTasksService(user_id)
        success, error = service.delete_task(task_id)

        if error:
            return Response(
                data={"message": error},
                status=(
                    status.HTTP_404_NOT_FOUND
                    if "not found" in error.lower()
                    else status.HTTP_500_INTERNAL_SERVER_ERROR
                ),
            )

        return Response(
            data={"message": "Task deleted successfully"},
            status=status.HTTP_204_NO_CONTENT,
        )


class SyncTasksApiView(BaseTaskView):
    """
    Dedicated endpoint to trigger task synchronization.
    Useful for background jobs or manual sync triggers.
    """

    def post(self, request, *args, **kwargs):
        user_id, error_response = self.get_user_id(request)
        if error_response:
            return error_response

        service = GoogleTasksService(user_id)
        count, error = service.sync_tasks()

        if error:
            return Response(
                data={"message": error}, status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

        return Response(
            data={"message": "Sync completed successfully", "tasks_synced": count},
            status=status.HTTP_200_OK,
        )
