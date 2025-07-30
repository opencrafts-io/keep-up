from datetime import datetime
import logging
import os
import uuid
from django.utils import timezone
from pythonjsonlogger.json import JsonFormatter
from rest_framework.views import APIView, Response, status
from rest_framework.generics import (
    CreateAPIView,
    DestroyAPIView,
    UpdateAPIView,
    get_object_or_404,
)
from keep_up.verisafe_jwt_authentication import VerisafeJWTAuthentication
from todos.models import Task
from verisafe.retrieve_user_socials import retrieve_user_social_accounts
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from .serializers import TaskSerializer

# Create your views here.
logger = logging.getLogger()
logger.setLevel(logging.ERROR)


handler = logging.StreamHandler()
handler.setFormatter(JsonFormatter())
logger.addHandler(handler)


class PingAPIView(APIView):
    authentication_classes = [VerisafeJWTAuthentication]

    def get(self, request, *args, **kwargs):
        """
        An endpoint that checks the heartbeat of the program
        """
        return Response(data={"message": "He is risen"}, status=status.HTTP_200_OK)


class CreateTodoApiView(CreateAPIView):
    """
    Creates a todo item
    """

    authentication_classes = [VerisafeJWTAuthentication]

    def post(self, request, *args, **kwargs):
        user_id: str | None = getattr(request, "user_id", None)

        if not user_id:
            logger.error(
                "Failed to extract user_id from JWT claims",
                extra={"user_id": user_id},
            )
            return Response(
                data={
                    "message": "We couldn't extract your user id from the provided token."
                    "Please ensure the token is valid and contains the necessary user data."
                },
                status=status.HTTP_403_FORBIDDEN,
            )

        # Retrieve user socials
        socials = retrieve_user_social_accounts(user_id)

        if isinstance(socials, str):
            return Response(
                data={
                    "message": socials,
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        # get google social login
        google_social = None
        for social in socials:
            if social["provider"] == "google":
                google_social = social
                break

        if not google_social:
            logger.error(
                "No Google social account found for user.", extra={"user_id": user_id}
            )
            return Response(
                data={
                    "message": "No Google social account linked to this user please consider linking your google account"
                },
                status=status.HTTP_404_NOT_FOUND,
            )

        creds = Credentials(
            token=google_social["access_token"],
            client_id=os.getenv("GOOGLE_CLIENT_ID"),
            client_secret=os.getenv("GOOGLE_CLIENT_SECRET"),
            refresh_token=google_social["refresh_token"],
            token_uri="https://oauth2.googleapis.com/token",
        )

        try:
            # Build the Google Tasks API service
            service = build("tasks", "v1", credentials=creds)

            # Create the task
            task_title = request.data.get("title", "Your academia task")
            task_notes = request.data.get(
                "notes", "This is a task created via the Google Tasks API."
            )
            task_due = request.data.get("due", timezone.now())
            task_parent = request.data.get("parent", None)
            task = {
                "title": task_title,
                "notes": task_notes,
                "due": task_due.isoformat(),
                "status": "needsAction",
                "parent": task_parent,
            }

            created_task = (
                service.tasks()
                .insert(tasklist="@default", body=task, parent=task_parent)
                .execute()
            )

            # Prepare data for serializer
            task_data = {
                "id": created_task["id"],
                "kind": created_task["kind"],
                "etag": created_task["etag"],
                "title": created_task["title"],
                "updated": created_task["updated"],
                "self_link": created_task["selfLink"],
                "parent": created_task.get("parent", ""),
                "position": created_task.get("position", ""),
                "notes": created_task["notes"],
                "status": created_task["status"],
                "due": created_task["due"],
                "completed": created_task.get("completed", None),
                "deleted": False,
                "hidden": False,
                "web_view_link": created_task["webViewLink"],
                "owner_id": uuid.UUID(user_id),
            }

            # Use the TaskSerializer to validate and save the task to the database
            serializer = TaskSerializer(data=task_data)
            if serializer.is_valid():
                task_instance = serializer.save()
                logger.info(
                    f"Task successfully created and saved in database: {task_instance.id}"
                )
                return Response(
                    data=serializer.data,
                    status=status.HTTP_201_CREATED,
                )
            else:
                return Response(
                    data={
                        "message": "Failed to create task in the database.",
                        "error": serializer.errors,
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )
        except Exception as e:
            logger.error(
                f"Error creating Google Task: {str(e)}", extra={"user_id": user_id}
            )
            return Response(
                data={"message": f"Error creating Google task: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class UpdateTodoApiView(APIView):
    """
    Updates a todo item specified by its ID.
    Optionally, syncs the update with Google Tasks.
    """

    authentication_classes = [VerisafeJWTAuthentication]

    def put(self, request, *args, **kwargs):
        task_id = kwargs.get("task_id")  # Task ID passed as a URL parameter
        user_id: str | None = getattr(request, "user_id", None)

        if not user_id:
            logger.error(
                "Failed to extract user_id from JWT claims", extra={"user_id": user_id}
            )
            return Response(
                data={
                    "message": "We couldn't extract your user id from the provided token. "
                    "Please ensure the token is valid and contains the necessary user data."
                },
                status=status.HTTP_403_FORBIDDEN,
            )

        # Retrieve the task from the database
        task = get_object_or_404(Task, id=task_id, owner_id=user_id)

        # Retrieve user socials to get Google credentials
        socials = retrieve_user_social_accounts(user_id)

        if isinstance(socials, str):
            return Response(
                data={"message": socials},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        google_social = None

        # Retrieve the Google social login credentials
        for social in socials:
            if social["provider"] == "google":
                google_social = social
                break

        if not google_social:
            logger.error(
                "No Google social account found for user.", extra={"user_id": user_id}
            )
            return Response(
                data={"message": "No Google social account linked to this user."},
                status=status.HTTP_404_NOT_FOUND,
            )

        # Use the access_token provided by the social account (no refresh)
        creds = Credentials(
            token=google_social["access_token"],
            token_uri="https://oauth2.googleapis.com/token",
            refresh_token=google_social["refresh_token"],
            client_id=os.getenv("GOOGLE_CLIENT_ID"),
            client_secret=os.getenv("GOOGLE_CLIENT_SECRET"),
        )

        try:
            # Build the Google Tasks API service
            service = build("tasks", "v1", credentials=creds)

            # Prepare the updated task data
            updated_title = request.data.get("title", task.title)
            updated_notes = request.data.get("notes", task.notes)
            updated_due = request.data.get("due", task.due)
            updated_status = request.data.get("status", task.status)

            updated_task = {
                "id": task.id,
                "title": updated_title,
                "notes": updated_notes,
                "due": updated_due.isoformat(),  # Ensure the due date is in ISO format
                "status": updated_status,  # Default to 'needsAction' if None
            }

            print(task_id, task.id)

            # Update the task on Google Tasks
            updated_google_task = (
                service.tasks()
                .update(tasklist="@default", task=task.id, body=updated_task)
                .execute()
            )

            # Update the task in your database
            task.title = updated_title
            task.notes = updated_notes
            task.due = updated_due
            task.status = updated_status
            task.save()

            logger.info(
                f"Task {task_id} successfully updated in both the database and Google Tasks."
            )

            return Response(
                data={"message": "Task updated successfully."},
                status=status.HTTP_200_OK,
            )

        except Exception as e:
            logger.error(
                f"Error updating Google Task or database task: {str(e)}",
                extra={"task_id": task_id, "user_id": user_id},
            )
            return Response(
                data={"message": f"Error updating task: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class CompleteTodoApiView(UpdateAPIView):
    """
    Marks a todo item as complete in Google Tasks.
    """

    authentication_classes = [VerisafeJWTAuthentication]

    def put(self, request, *args, **kwargs):
        user_id: str | None = getattr(request, "user_id", None)
        task_id = kwargs.get("task_id")

        if not user_id:
            logger.error(
                "Failed to extract user_id from JWT claims",
                extra={"user_id": user_id},
            )
            return Response(
                data={
                    "message": "We couldn't extract your user id from the provided token. "
                    "Please ensure the token is valid and contains the necessary user data."
                },
                status=status.HTTP_403_FORBIDDEN,
            )

        if not task_id:
            return Response(
                data={"message": "Task ID is required to complete a task."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Retrieve user socials
        socials = retrieve_user_social_accounts(user_id)

        if isinstance(socials, str):
            return Response(
                data={
                    "message": socials,
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        # Get Google social login
        google_social = None
        for social in socials:
            if social["provider"] == "google":
                google_social = social
                break

        if not google_social:
            logger.error(
                "No Google social account found for user.", extra={"user_id": user_id}
            )
            return Response(
                data={
                    "message": "No Google social account linked to this user. Please consider linking your Google account."
                },
                status=status.HTTP_404_NOT_FOUND,
            )

        creds = Credentials(
            token=google_social["access_token"],
            client_id=os.getenv("GOOGLE_CLIENT_ID"),
            client_secret=os.getenv("GOOGLE_CLIENT_SECRET"),
            refresh_token=google_social["refresh_token"],
            token_uri="https://oauth2.googleapis.com/token",
        )

        local_task_instance = None
        try:
            local_task_instance = Task.objects.get(id=task_id)
            logger.debug(
                f"DEBUG: Retrieved local_task_instance (ID: {local_task_instance.id}, Title: {local_task_instance.title})"
            )
        except Task.DoesNotExist:
            logger.error(
                f"Local database Task with ID {task_id} not found for update. It might need to be created first or there's a data sync issue."
            )
            return Response(
                data={"message": "Task not found in local database for update."},
                status=status.HTTP_404_NOT_FOUND,
            )

        try:
            # Build the Google Tasks API service
            service = build("tasks", "v1", credentials=creds)

            # First, get the existing task to ensure it exists and get its current state
            try:
                task_to_update = (
                    service.tasks().get(tasklist="@default", task=task_id).execute()
                )
            except Exception as e:
                logger.error(
                    f"Task with ID {task_id} not found or inaccessible: {str(e)}"
                )
                return Response(
                    data={
                        "message": f"Task with ID {task_id} not found or inaccessible."
                    },
                    status=status.HTTP_404_NOT_FOUND,
                )

            # Update the task's status to 'completed' and set completion date
            # if its not yet completed otherwise set it as incomplete
            if local_task_instance.status == "completed":
                print("there")
                task_to_update["status"] = "needsAction"
                task_to_update["completed"] = None
            else:
                print("Here")
                task_to_update["status"] = "completed"
                task_to_update["completed"] = timezone.now().isoformat()

            # Execute the update
            updated_task = (
                service.tasks()
                .update(tasklist="@default", task=task_id, body=task_to_update)
                .execute()
            )

            # Prepare data for serializer (similar to your creation logic)
            task_data = {
                "id": updated_task["id"],
                "kind": updated_task["kind"],
                "etag": updated_task["etag"],
                "title": updated_task["title"],
                "updated": updated_task["updated"],
                "self_link": updated_task["selfLink"],
                "parent": updated_task.get("parent", ""),
                "position": updated_task.get("position", ""),
                "notes": updated_task.get(
                    "notes", ""
                ),  # Notes might not always be present
                "status": updated_task["status"],
                "due": updated_task.get("due", None),  # Due might not always be present
                "completed": updated_task.get("completed", None),
                "deleted": False,
                "hidden": False,
                "web_view_link": updated_task["webViewLink"],
                "owner_id": uuid.UUID(user_id),
            }

            # Use the TaskSerializer to validate and save the task's updated status to the database
            serializer = TaskSerializer(
                instance=local_task_instance, data=task_data, partial=True
            )  # Use partial=True for updates
            if serializer.is_valid():
                task_instance = serializer.save()
                logger.info(
                    f"Task with ID {task_instance.id} successfully completed and updated in database."
                )
                return Response(
                    data=serializer.data,
                    status=status.HTTP_200_OK,  # 200 OK for successful update
                )
            else:
                return Response(
                    data={
                        "message": "Failed to update task status in the database.",
                        "error": serializer.errors,
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

        except Exception as e:
            logger.error(
                f"Error completing Google Task with ID {task_id}: {str(e)}",
                extra={"user_id": user_id},
            )
            return Response(
                data={"message": f"Error completing Google task: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class DeleteTaskAPIView(DestroyAPIView):
    authentication_classes = [VerisafeJWTAuthentication]

    def delete(self, request, *args, **kwargs):
        task_id = kwargs.get("task_id")  # Task ID passed as a URL parameter
        user_id: str | None = getattr(request, "user_id", None)

        if not user_id:
            logger.error(
                "Failed to extract user_id from JWT claims", extra={"user_id": user_id}
            )
            return Response(
                data={
                    "message": "We couldn't extract your user id from the provided token. "
                    "Please ensure the token is valid and contains the necessary user data."
                },
                status=status.HTTP_403_FORBIDDEN,
            )

        # Retrieve the task from the database
        task = get_object_or_404(Task, id=task_id, owner_id=user_id)

        # Retrieve user socials to get Google credentials
        socials = retrieve_user_social_accounts(user_id)

        if isinstance(socials, str):
            return Response(
                data={"message": socials},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        google_social = None

        # Retrieve the Google social login credentials
        for social in socials:
            if social["provider"] == "google":
                google_social = social
                break

        if not google_social:
            logger.error(
                "No Google social account found for user.", extra={"user_id": user_id}
            )
            return Response(
                data={"message": "No Google social account linked to this user."},
                status=status.HTTP_404_NOT_FOUND,
            )

        # Use the access_token provided by the social account (no refresh)
        creds = Credentials(
            token=google_social["access_token"],
            client_id=os.getenv("GOOGLE_CLIENT_ID"),
            client_secret=os.getenv("GOOGLE_CLIENT_SECRET"),
            refresh_token=google_social["refresh_token"],
            token_uri="https://oauth2.googleapis.com/token",
        )

        try:
            # Build the Google Tasks API service
            service = build("tasks", "v1", credentials=creds)

            # Optionally delete the task from Google Tasks if it exists there
            google_task_id = (
                task.id
            )  # Assuming the `task.id` corresponds to the Google Task ID
            service.tasks().delete(tasklist="@default", task=google_task_id).execute()

            # Delete the task from the database
            task.delete()

            logger.info(
                f"Task {task.title} successfully deleted from both the database and Google Tasks."
            )
            return Response(
                data={"message": "Task deleted successfully."},
                status=status.HTTP_204_NO_CONTENT,
            )

        except Exception as e:
            logger.error(
                f"Error deleting Google Task or database task: {str(e)}",
                extra={"task_id": task_id, "user_id": user_id},
            )
            return Response(
                data={"message": f"Error deleting task: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
