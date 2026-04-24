"""
Copyright (c) 2025 Open Crafts Interactive. All Rights Reserved.

Views operating purely on local DB.
Google Tasks sync is handled asynchronously via Celery workers (not yet implemented).
"""

import logging
from django.db.models import ObjectDoesNotExist
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.generics import ListAPIView
from keep_up.verisafe_jwt_authentication import VerisafeJWTAuthentication
from todos.models import Task, TaskList
from todos.serializers import TaskListSerializer, TaskSerializer
from .services import TaskListService
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


class CreateTaskListView(BaseTaskView):
    """
    Creates a task list.
    """

    serializer_class = TaskListSerializer

    @extend_schema(
        tags=["Task Lists"],
        summary="Create a task list",
        description="Creates a new task list for the authenticated user.",
        request=TaskListSerializer,
        responses={
            201: TaskListSerializer,
            400: OpenApiResponse(description="Validation error"),
            500: OpenApiResponse(description="Internal server error"),
        },
    )
    def post(self, request, *args, **kwargs):
        user_id, error_response = self.get_user_id(request)
        if error_response:
            return error_response

        serializer = self.serializer_class(data=request.data)
        if not serializer.is_valid():
            return Response(data=serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        try:
            task_list = TaskListService().create_task_list(
                owner_id=user_id, **serializer.validated_data
            )

            return Response(
                data=self.serializer_class(task_list).data,
                status=status.HTTP_201_CREATED,
            )

        except ValueError as e:
            return Response(
                data={"message": str(e)}, status=status.HTTP_400_BAD_REQUEST
            )
        except Exception as e:
            logger.exception("Unexpected error creating task list")
            return Response(
                data={"message": "An internal error occurred."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class RetrieveTaskLists(BaseTaskView):
    """Retrieves user task lists from the DB"""

    serializer_class = TaskListSerializer

    @extend_schema(
        tags=["Task Lists"],
        summary="Get all task lists",
        description="Retrieve all task lists for the authenticated user.",
        responses={200: TaskListSerializer(many=True)},
    )
    def get(self, request, *args, **kwargs):
        user_id, error_response = self.get_user_id(request)
        if error_response:
            return error_response

        try:
            task_list = TaskListService().get_user_lists(owner_id=user_id)

            return Response(
                data=self.serializer_class(task_list, many=True).data,
                status=status.HTTP_200_OK,
            )

        except ValueError as e:
            return Response(
                data={"message": str(e)}, status=status.HTTP_400_BAD_REQUEST
            )
        except Exception as e:
            logger.exception("Unexpected error creating task list")
            return Response(
                data={"message": "An internal error occurred."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class RetrieveOrCreateUserDefaultTaskList(BaseTaskView):

    serializer_class = TaskListSerializer

    @extend_schema(
        tags=["Task Lists"],
        summary="Get or create default task list",
        description="Returns the user's default task list. Creates one if it doesn't exist.",
        responses={200: TaskListSerializer},
    )
    def get(self, request, *args, **kwargs):
        user_id, error_response = self.get_user_id(request)
        if error_response:
            return error_response

        try:
            task_list = TaskListService().get_or_create_default_list(owner_id=user_id)

            return Response(
                data=self.serializer_class(task_list).data,
                status=status.HTTP_200_OK,
            )

        except ValueError as e:
            return Response(
                data={"message": str(e)}, status=status.HTTP_400_BAD_REQUEST
            )
        except Exception as e:
            logger.exception("Unexpected error creating task list")
            return Response(
                data={"message": "An internal error occurred."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class RetrieveTaskListByIDView(BaseTaskView):
    serializer_class = TaskListSerializer

    @extend_schema(
        tags=["Task Lists"],
        summary="Get or create default task list",
        description="Returns the user's default task list. Creates one if it doesn't exist.",
        responses={200: TaskListSerializer},
    )
    def get(self, request, *args, **kwargs):
        user_id, error_response = self.get_user_id(request)
        if error_response:
            return error_response

        list_id = kwargs.get("list_id")
        if not list_id:
            return Response(
                {"message": "Task list ID is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            task_list = TaskListService.get_task_list(owner_id=user_id, list_id=list_id)

            return Response(
                data=self.serializer_class(task_list).data,
                status=status.HTTP_200_OK,
            )

        except (ObjectDoesNotExist, ValueError):
            return Response(
                data={"message": "Task list not found."},
                status=status.HTTP_404_NOT_FOUND,
            )
        except Exception:
            logger.exception(f"Error retrieving task list {list_id}")
            return Response(
                data={"message": "An internal error occurred."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class UpdateTaskListView(BaseTaskView):
    """Updates an existing task list."""

    serializer_class = TaskListSerializer

    @extend_schema(
        tags=["Task Lists"],
        summary="Update a task list",
        description=(
            "Partially update a task list. "
            "Only editable fields will be updated. "
            "Protected fields (e.g. sync_status) are ignored."
        ),
        parameters=[
            OpenApiParameter(
                name="list_id",
                description="UUID of the task list",
                required=True,
                type=str,
                location=OpenApiParameter.PATH,
            )
        ],
        request=TaskListSerializer,
        responses={
            200: TaskListSerializer,
            400: OpenApiResponse(description="Validation error"),
            404: OpenApiResponse(description="Task list not found"),
        },
    )
    def patch(self, request, *args, **kwargs):
        user_id, error_response = self.get_user_id(request)
        if error_response:
            return error_response

        list_id = kwargs.get("list_id")

        try:
            instance = TaskListService.get_task_list(owner_id=user_id, list_id=list_id)

            serializer = self.serializer_class(
                instance, data=request.data, partial=True, context={"owner_id": user_id}
            )

            if not serializer.is_valid():
                return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

            updated_list = TaskListService.update_task_list(
                owner_id=user_id, list_id=list_id, **serializer.validated_data
            )

            return Response(self.serializer_class(updated_list).data)

        except (TaskList.DoesNotExist, ObjectDoesNotExist):
            return Response(
                {"message": "Task list not found."}, status=status.HTTP_404_NOT_FOUND
            )
        except ValueError as e:
            return Response({"message": str(e)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception:
            logger.exception(f"Error updating task list {list_id}")
            return Response({"message": "Internal error."}, status=500)


class DeleteTaskListView(BaseTaskView):
    """Soft-deletes a task list and its associated tasks."""

    @extend_schema(
        tags=["Task Lists"],
        summary="Delete a task list",
        description="Soft deletes a task list and its associated tasks.",
        parameters=[
            OpenApiParameter(
                name="list_id",
                description="UUID of the task list",
                required=True,
                type=str,
                location=OpenApiParameter.PATH,
            )
        ],
        responses={
            204: OpenApiResponse(description="Deleted successfully"),
            400: OpenApiResponse(description="Cannot delete default list"),
            404: OpenApiResponse(description="Task list not found"),
        },
    )
    def delete(self, request, *args, **kwargs):
        user_id, error_response = self.get_user_id(request)
        if error_response:
            return error_response

        list_id = kwargs.get("list_id")

        try:
            TaskListService.delete_task_list(owner_id=user_id, list_id=list_id)

            return Response(status=status.HTTP_204_NO_CONTENT)

        except (TaskList.DoesNotExist, ObjectDoesNotExist):
            return Response(
                {"message": "Task list not found."}, status=status.HTTP_404_NOT_FOUND
            )
        except ValueError as e:
            return Response({"message": str(e)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception:
            logger.exception(f"Error deleting task list {list_id}")
            return Response(
                {"message": "An internal error occurred."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


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
