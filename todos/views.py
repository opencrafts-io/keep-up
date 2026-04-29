"""
Copyright (c) 2025 Open Crafts Interactive. All Rights Reserved.

Views operating purely on local DB.
Google Tasks sync is handled asynchronously via Celery workers (not yet implemented).
"""

import logging
from django.db.models import ObjectDoesNotExist
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, OpenApiRequest, OpenApiResponse, extend_schema
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.generics import ListAPIView
from keep_up.verisafe_jwt_authentication import VerisafeJWTAuthentication
from todos.models import Task, TaskList
from todos.serializers import TagSerializer, TaskListSerializer, TaskSerializer
from .services import TaskListService, TagService, TaskService
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

        serializer = self.serializer_class(
            data=request.data, context={"owner_id": user_id}
        )
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
            logger.exception("Unexpected error retrieving task lists")
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
        summary="Get a task list by ID",
        description="Retrieve a specific task list owned by the authenticated user.",
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
            200: TaskListSerializer,
            400: OpenApiResponse(description="Missing list_id"),
            404: OpenApiResponse(description="Task list not found"),
        },
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


class TagListCreateView(BaseTaskView):
    serializer_class = TagSerializer

    @extend_schema(
        tags=["Tags"],
        summary="List user tags",
        description="Retrieves all tags created by the authenticated user.",
        responses={200: TagSerializer(many=True)},
    )
    def get(self, request):
        user_id, error_response = self.get_user_id(request)
        if error_response:
            return error_response

        tags = TagService.get_user_tags(user_id)
        return Response(self.serializer_class(tags, many=True).data)

    @extend_schema(
        tags=["Tags"],
        summary="Create a tag",
        description="Creates a new local tag. Names must be unique per user.",
        request=TagSerializer,
        responses={
            201: TagSerializer,
            400: OpenApiResponse(description="Validation error or duplicate name"),
        },
    )
    def post(self, request):
        user_id, error_response = self.get_user_id(request)
        if error_response:
            return error_response

        serializer = self.serializer_class(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        try:
            tag = TagService.create_tag(owner_id=user_id, **serializer.validated_data)
            return Response(
                self.serializer_class(tag).data, status=status.HTTP_201_CREATED
            )
        except ValueError as e:
            return Response({"message": str(e)}, status=status.HTTP_400_BAD_REQUEST)


class TagDetailView(BaseTaskView):
    serializer_class = TagSerializer

    @extend_schema(
        tags=["Tags"],
        summary="Update a tag",
        request=TagSerializer,
        responses={200: TagSerializer, 404: OpenApiResponse(description="Not found")},
    )
    def patch(self, request, tag_id):
        user_id, error_response = self.get_user_id(request)
        if error_response:
            return error_response

        serializer = self.serializer_class(data=request.data, partial=True)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        try:
            tag = TagService.update_tag(user_id, tag_id, **serializer.validated_data)
            return Response(self.serializer_class(tag).data)
        except Tag.DoesNotExist:
            return Response(
                {"message": "Tag not found"}, status=status.HTTP_404_NOT_FOUND
            )
        except ValueError as e:
            return Response({"message": str(e)}, status=status.HTTP_400_BAD_REQUEST)

    @extend_schema(
        tags=["Tags"],
        summary="Delete a tag",
        responses={204: OpenApiResponse(description="Deleted successfully")},
    )
    def delete(self, request, tag_id):
        user_id, error_response = self.get_user_id(request)
        if error_response:
            return error_response

        try:
            TagService.delete_tag(user_id, tag_id)
            return Response(status=status.HTTP_204_NO_CONTENT)
        except Tag.DoesNotExist:
            return Response(
                {"message": "Tag not found"}, status=status.HTTP_404_NOT_FOUND
            )


class CreateTaskView(BaseTaskView):
    """Creates a todo item in local DB."""

    serializer_class = TaskSerializer

    @extend_schema(
        tags=["Task"],
        summary="Create a task",
        request=TaskSerializer,
        responses={200: TaskSerializer},
    )
    def post(self, request, *args, **kwargs):
        user_id, error_response = self.get_user_id(request)
        if error_response:
            return error_response

        serializer = self.serializer_class(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        try:
            validated_data = serializer.validated_data.copy()
            if "parent_id" in validated_data:
                validated_data["parent"] = validated_data.pop("parent_id")

            created_task = TaskService.create_task(
                owner_id=user_id,
                **validated_data,
            )
            return Response(
                data=self.serializer_class(created_task).data,
                status=status.HTTP_201_CREATED,
            )
        except ValueError as e:
            return Response({"message": str(e)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            return Response({"message": str(e)}, status=status.HTTP_400_BAD_REQUEST)


class RetrieveTaskView(BaseTaskView):
    """Retrieves a single task by ID."""

    serializer_class = TaskSerializer

    @extend_schema(
        tags=["Task"],
        summary="Retrieve a task",
        responses={200: TaskSerializer},
    )
    def get(self, request, task_id, *args, **kwargs):
        user_id, error_response = self.get_user_id(request)
        if error_response:
            return error_response

        try:
            task = TaskService.get_task(user_id, task_id)
            return Response(
                data=self.serializer_class(task).data,
                status=status.HTTP_200_OK,
            )
        except Task.DoesNotExist:
            return Response(
                {"message": "Task not found"},
                status=status.HTTP_404_NOT_FOUND,
            )
        except Exception as e:
            return Response(
                {"message": str(e)},
                status=status.HTTP_400_BAD_REQUEST,
            )


class UpdateTaskView(BaseTaskView):
    """Updates a task."""

    serializer_class = TaskSerializer

    @extend_schema(
        tags=["Task"],
        summary="Update a task",
        request=TaskSerializer,
        responses={200: TaskSerializer},
    )
    def patch(self, request, task_id, *args, **kwargs):
        user_id, error_response = self.get_user_id(request)
        if error_response:
            return error_response

        try:
            validated_data = request.data.copy()

            # Rename parent_id to parent for update_task
            if "parent_id" in validated_data:
                parent_id = validated_data.pop("parent_id")
                if parent_id:
                    validated_data["parent"] = Task.objects.get(id=parent_id)
                else:
                    validated_data["parent"] = None

            updated_task = TaskService.update_task(
                owner_id=user_id, task_id=task_id, **validated_data
            )
            return Response(
                data=self.serializer_class(updated_task).data,
                status=status.HTTP_200_OK,
            )
        except Task.DoesNotExist:
            return Response(
                {"message": "Task not found"},
                status=status.HTTP_404_NOT_FOUND,
            )
        except ValueError as e:
            return Response(
                {"message": str(e)},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except Exception as e:
            return Response(
                {"message": str(e)},
                status=status.HTTP_400_BAD_REQUEST,
            )

class DeleteTaskView(BaseTaskView):
    """Deletes a task (soft delete)."""

    @extend_schema(
        tags=["Task"],
        summary="Delete a task",
        responses={204: None},
    )
    def delete(self, request, task_id, *args, **kwargs):
        user_id, error_response = self.get_user_id(request)
        if error_response:
            return error_response

        try:
            TaskService.delete_task(user_id, task_id)
            return Response(status=status.HTTP_204_NO_CONTENT)
        except Task.DoesNotExist:
            return Response(
                {"message": "Task not found"},
                status=status.HTTP_404_NOT_FOUND,
            )
        except Exception as e:
            return Response(
                {"message": str(e)},
                status=status.HTTP_400_BAD_REQUEST,
            )


class ListTasksView(BaseTaskView):
    """Lists all tasks for the authenticated user, optionally filtered by task list."""

    serializer_class = TaskSerializer

    @extend_schema(
        tags=["Task"],
        summary="List user tasks",
        parameters=[
            OpenApiParameter(
                name="task_list_id",
                description="Optional TaskList ID to filter by",
                required=False,
                type=OpenApiTypes.UUID,
            ),
        ],
        responses={200: TaskSerializer(many=True)},
    )
    def get(self, request, *args, **kwargs):
        user_id, error_response = self.get_user_id(request)
        if error_response:
            return error_response

        try:
            task_list_id = request.query_params.get("task_list_id")
            tasks = TaskService.get_user_tasks(user_id, task_list_id)
            return Response(
                data=self.serializer_class(tasks, many=True).data,
                status=status.HTTP_200_OK,
            )
        except Exception as e:
            return Response(
                {"message": str(e)},
                status=status.HTTP_400_BAD_REQUEST,
            )


class CompleteTaskView(BaseTaskView):
    """Marks a task as completed."""

    serializer_class = TaskSerializer

    @extend_schema(
        tags=["Task"],
        summary="Complete a task",
        responses={200: TaskSerializer},
    )
    def post(self, request, task_id, *args, **kwargs):
        user_id, error_response = self.get_user_id(request)
        if error_response:
            return error_response

        try:
            task = TaskService.complete_task(user_id, task_id)
            return Response(
                data=self.serializer_class(task).data,
                status=status.HTTP_200_OK,
            )
        except Task.DoesNotExist:
            return Response(
                {"message": "Task not found"},
                status=status.HTTP_404_NOT_FOUND,
            )
        except Exception as e:
            return Response(
                {"message": str(e)},
                status=status.HTTP_400_BAD_REQUEST,
            )


class ReopenTaskView(BaseTaskView):
    """Marks a completed task as needs_action."""

    serializer_class = TaskSerializer

    @extend_schema(
        tags=["Task"],
        summary="Reopen a task",
        responses={200: TaskSerializer},
    )
    def post(self, request, task_id, *args, **kwargs):
        user_id, error_response = self.get_user_id(request)
        if error_response:
            return error_response

        try:
            task = TaskService.reopen_task(user_id, task_id)
            return Response(
                data=self.serializer_class(task).data,
                status=status.HTTP_200_OK,
            )
        except Task.DoesNotExist:
            return Response(
                {"message": "Task not found"},
                status=status.HTTP_404_NOT_FOUND,
            )
        except Exception as e:
            return Response(
                {"message": str(e)},
                status=status.HTTP_400_BAD_REQUEST,
            )


class MoveTaskToListView(BaseTaskView):
    """Moves a task to a different task list."""

    serializer_class = TaskSerializer

    @extend_schema(
        tags=["Task"],
        summary="Move a task to a list",
        request=OpenApiRequest(
            request={
                "type": "object",
                "properties": {"task_list_id": {"type": "string", "format": "uuid"}},
            }
        ),
        responses={200: TaskSerializer},
    )
    def post(self, request, task_id, *args, **kwargs):
        user_id, error_response = self.get_user_id(request)
        if error_response:
            return error_response

        task_list_id = request.data.get("task_list_id")
        if not task_list_id:
            return Response(
                {"message": "task_list_id is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            task = TaskService.move_task_to_list(user_id, task_id, task_list_id)
            return Response(
                data=self.serializer_class(task).data,
                status=status.HTTP_200_OK,
            )
        except Task.DoesNotExist:
            return Response(
                {"message": "Task or task list not found"},
                status=status.HTTP_404_NOT_FOUND,
            )
        except ValueError as e:
            return Response(
                {"message": str(e)},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except Exception as e:
            return Response(
                {"message": str(e)},
                status=status.HTTP_400_BAD_REQUEST,
            )


class ConvertToSubtaskView(BaseTaskView):
    """Converts a task to a subtask by assigning a parent."""

    serializer_class = TaskSerializer

    @extend_schema(
        tags=["Task"],
        summary="Convert task to subtask",
        request=OpenApiRequest(
            request={
                "type": "object",
                "properties": {"parent_task_id": {"type": "string", "format": "uuid"}},
            }
        ),
        responses={200: TaskSerializer},
    )
    def post(self, request, task_id, *args, **kwargs):
        user_id, error_response = self.get_user_id(request)
        if error_response:
            return error_response

        parent_task_id = request.data.get("parent_task_id")
        if not parent_task_id:
            return Response(
                {"message": "parent_task_id is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            task = TaskService.convert_to_subtask(user_id, task_id, parent_task_id)
            return Response(
                data=self.serializer_class(task).data,
                status=status.HTTP_200_OK,
            )
        except Task.DoesNotExist:
            return Response(
                {"message": "Task or parent task not found"},
                status=status.HTTP_404_NOT_FOUND,
            )
        except ValueError as e:
            return Response(
                {"message": str(e)},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except Exception as e:
            return Response(
                {"message": str(e)},
                status=status.HTTP_400_BAD_REQUEST,
            )


class PromoteSubtaskView(BaseTaskView):
    """Promotes a subtask to a top-level task."""

    serializer_class = TaskSerializer

    @extend_schema(
        tags=["Task"],
        summary="Promote subtask to top-level task",
        responses={200: TaskSerializer},
    )
    def post(self, request, task_id, *args, **kwargs):
        user_id, error_response = self.get_user_id(request)
        if error_response:
            return error_response

        try:
            task = TaskService.promote_subtask(user_id, task_id)
            return Response(
                data=self.serializer_class(task).data,
                status=status.HTTP_200_OK,
            )
        except Task.DoesNotExist:
            return Response(
                {"message": "Task not found"},
                status=status.HTTP_404_NOT_FOUND,
            )
        except Exception as e:
            return Response(
                {"message": str(e)},
                status=status.HTTP_400_BAD_REQUEST,
            )
