from datetime import datetime
import logging
import os
import uuid
from django.utils import timezone
from pythonjsonlogger.json import JsonFormatter
from rest_framework.views import APIView, Response, status
from rest_framework.generics import CreateAPIView
from keep_up.verisafe_jwt_authentication import VerisafeJWTAuthentication
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
            task = {
                "title": task_title,
                "notes": task_notes,
                "due": task_due.isoformat(),
                "status": "needsAction",
            }

            created_task = (
                service.tasks().insert(tasklist="@default", body=task).execute()
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
