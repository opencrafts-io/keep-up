"""
Copyright (c) 2025 Open Crafts Interactive. All Rights Reserved.

Service layer for google tasks operations
Separates business logic from views for better testability and reusability.
"""

from datetime import datetime
import logging
import os
from typing import Any, Dict, Optional, Tuple
import uuid
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import HttpError
from todos.models import Task
from todos.serializers import TaskSerializer
from verisafe.retrieve_user_socials import retrieve_user_social_accounts


class GoogleTasksService:

    def __init__(self, user_id: str) -> None:
        self.user_id: str = user_id
        self._service: Optional[str] = None
        self._credentials: Optional[Credentials] = None
        self.logger = logging.getLogger("keep_up")

    def get_credentials(self) -> Tuple[Optional[Credentials], Optional[str]]:
        """
        Retrieve Google OAuth credentials for the user.

        Returns:
            Tuple of (Credentials, error_message)
            If successful: (Credentials object, None)
            If failed: (None, error message string)
        """
        if self._credentials:
            return self._credentials, None

        socials = retrieve_user_social_accounts(self.user_id)

        if isinstance(socials, str):
            return None, socials

        google_social = next((s for s in socials if s["provider"] == "google"), None)

        if not google_social:
            return None, "No Google social account linked to this user"

        try:
            self._credentials = Credentials(
                token=google_social["access_token"],
                client_id=os.getenv("GOOGLE_CLIENT_ID"),
                client_secret=os.getenv("GOOGLE_CLIENT_SECRET"),
                refresh_token=google_social["refresh_token"],
                token_uri="https://oauth2.googleapis.com/token",
            )
            return self._credentials, None
        except Exception as e:
            self.logger.error(
                f"Error creating credentials: {e}", extra={"user_id": self.user_id}
            )
            return None, f"Failed to create credentials: {str(e)}"

    def get_service(self):
        """Get or create Google Tasks API service instance."""
        if self._service:
            return self._service, None

        creds, error = self.get_credentials()
        if error:
            return None, error

        try:
            self._service = build("tasks", "v1", credentials=creds)
            return self._service, None
        except Exception as e:
            self.logger.error(f"Error building Google Tasks service: {e}")
            return None, f"Failed to connect to Google Tasks: {str(e)}"

    def create_task(
        self, task_data: Dict[str, Any]
    ) -> Tuple[Optional[Task], Optional[str]]:
        """
        Create a task in Google Tasks and local DB.

        Args:
            task_data: Dict with keys: title (required), notes, parent, due

        Returns:
            Tuple of (Task instance, error_message)
        """
        service, error = self.get_service()
        if error:
            return None, error

        title = task_data.get("title")
        if not title:
            return None, "Task title is required"

        try:
            # Prepare Google Tasks payload
            google_task = {
                "title": title,
                "status": "needsAction",
            }

            if task_data.get("notes"):
                google_task["notes"] = task_data["notes"]

            if task_data.get("due"):
                google_task["due"] = task_data["due"]

            if task_data.get("parent"):
                google_task["parent"] = task_data["parent"]

            # Create in Google Tasks
            created_task = (
                service.task()
                .insert(
                    tasklist="@default",
                    body=google_task,
                    parent=task_data.get("parent"),
                )
                .execute()
            )

            # Save to local DB
            local_task = self._save_task_to_db(created_task)
            if not local_task:
                return None, "Failed to save task to database"

            self.logger.info(f"Task created: {local_task.id}")
            return local_task, None

        except HttpError as e:
            error_msg = f"Google API error: {e.resp.status}"
            self.logger.error(error_msg, extra={"user_id": self.user_id})
            return None, error_msg
        except Exception as e:
            self.logger.error(
                f"Error creating task: {e}", extra={"user_id": self.user_id}
            )
            return None, str(e)


    def update_task(self, task_id: str, update_data: Dict[str, Any]) -> Tuple[Optional[Task], Optional[str]]:
        """
        Update a task in both Google Tasks and local DB.
        
        Args:
            task_id: The external_id of the task (Google Task ID)
            update_data: Dict with fields to update (title, notes, due, status)
        
        Returns:
            Tuple of (updated Task instance, error_message)
        """
        # Get local task
        try:
            local_task = Task.objects.get(external_id=task_id, owner_id=self.user_id)
        except Task.DoesNotExist:
            return None, "Task not found"
        
        service, error = self.get_service()
        if error:
            return None, error
        
        try:
            # Build update payload
            google_update = {"id": task_id}
            
            if "title" in update_data:
                google_update["title"] = update_data["title"]
            if "notes" in update_data:
                google_update["notes"] = update_data["notes"]
            if "status" in update_data:
                google_update["status"] = update_data["status"]
            if "due" in update_data:
                due = update_data["due"]
                google_update["due"] = due.isoformat() if hasattr(due, 'isoformat') else due
            
            # Update in Google Tasks
            updated_task = service.tasks().update(
                tasklist="@default",
                task=task_id,
                body=google_update
            ).execute()
            
            # Update local DB
            local_task = self._save_task_to_db(updated_task, instance=local_task)
            if not local_task:
                return None, "Failed to update task in database"
            
            self.logger.info(f"Task updated: {task_id}")
            return local_task, None
            
        except HttpError as e:
            error_msg = f"Google API error: {e.resp.status}"
            self.logger.error(error_msg, extra={"user_id": self.user_id, "task_id": task_id})
            return None, error_msg
        except Exception as e:
            self.logger.error(f"Error updating task: {e}", extra={"task_id": task_id})
            return None, str(e)
    
    def toggle_task_completion(self, task_id: str) -> Tuple[Optional[Task], Optional[str]]:
        """
        Toggle task completion status.
        
        Args:
            task_id: The external_id of the task
        
        Returns:
            Tuple of (updated Task instance, error_message)
        """
        try:
            local_task = Task.objects.get(external_id=task_id, owner_id=self.user_id)
        except Task.DoesNotExist:
            return None, "Task not found"
        
        service, error = self.get_service()
        if error:
            return None, error
        
        try:
            # Get current task from Google
            google_task = service.tasks().get(
                tasklist="@default",
                task=task_id
            ).execute()
            
            # Toggle completion
            if local_task.status == "completed":
                google_task["status"] = "needsAction"
                google_task["completed"] = None
            else:
                google_task["status"] = "completed"
                google_task["completed"] = datetime.now().astimezone().isoformat()
            
            # Update in Google
            updated_task = service.tasks().update(
                tasklist="@default",
                task=task_id,
                body=google_task
            ).execute()
            
            # Update local DB
            local_task = self._save_task_to_db(updated_task, instance=local_task)
            return local_task, None
            
        except HttpError as e:
            return None, f"Google API error: {e.resp.status}"
        except Exception as e:
            self.logger.error(f"Error toggling task completion: {e}")
            return None, str(e)


    def delete_task(self, task_id: str) -> Tuple[bool, Optional[str]]:
        """
        Delete a task from both Google Tasks and local DB.
        
        Args:
            task_id: The external_id of the task
        
        Returns:
            Tuple of (success boolean, error_message)
        """
        try:
            local_task = Task.objects.get(external_id=task_id, owner_id=self.user_id)
        except Task.DoesNotExist:
            return False, "Task not found"
        
        service, error = self.get_service()
        if error:
            return False, error
        
        try:
            # Delete from Google Tasks
            service.tasks().delete(
                tasklist="@default",
                task=task_id
            ).execute()
            
            # Delete from local DB
            local_task.delete()
            
            self.logger.info(f"Task deleted: {task_id}")
            return True, None
            
        except HttpError as e:
            error_msg = f"Google API error: {e.resp.status}"
            self.logger.error(error_msg, extra={"task_id": task_id})
            return False, error_msg
        except Exception as e:
            self.logger.error(f"Error deleting task: {e}")
            return False, str(e)

    def sync_tasks(self) -> Tuple[int, Optional[str]]:
        """
        Sync all tasks from Google Tasks to local DB.
        Uses pagination to handle large task lists efficiently.

        Returns:
            Tuple of (number of tasks synced, error_message)
        """
        service, error = self.get_service()
        if error:
            return 0, error

        try:
            # Fetch all tasks from Google with pagination
            all_google_tasks = []
            page_token = None

            while True:
                response = (
                    service.tasks()
                    .list(
                        tasklist="@default",
                        showCompleted=True,
                        showHidden=True,
                        maxResults=100,
                        pageToken=page_token,
                    )
                    .execute()
                )

                all_google_tasks.extend(response.get("items", []))
                page_token = response.get("nextPageToken")

                if not page_token:
                    break

            self.logger.info(f"Retrieved {len(all_google_tasks)} tasks from Google")

            # Sync to local DB
            google_task_ids = set()
            for google_task in all_google_tasks:
                task_id = google_task["id"]
                google_task_ids.add(task_id)
                self._save_task_to_db(google_task)

            # Mark deleted tasks
            current_local_ids = set(
                Task.objects.filter(owner_id=self.user_id, deleted=False).values_list(
                    "external_id", flat=True
                )
            )

            ids_to_delete = current_local_ids - google_task_ids
            if ids_to_delete:
                deleted_count = Task.objects.filter(
                    external_id__in=ids_to_delete, owner_id=self.user_id
                ).update(deleted=True)
                self.logger.info(f"Marked {deleted_count} tasks as deleted")

            return len(all_google_tasks), None

        except HttpError as e:
            error_msg = f"Google API error during sync: {e.resp.status}"
            self.logger.error(error_msg, extra={"user_id": self.user_id})
            return 0, error_msg
        except Exception as e:
            self.logger.exception(f"Error syncing tasks: {e}")
            return 0, str(e)

    def _save_task_to_db(
        self, google_task: Dict, instance: Optional[Task] = None
    ) -> Optional[Task]:
        """
        Save or update a Google Task in the local database.

        Args:
            google_task: Task data from Google API
            instance: Existing Task instance to update (if any)

        Returns:
            Task instance or None if failed
        """
        task_data = {
            "external_id": google_task["id"],
            "kind": google_task.get("kind"),
            "etag": google_task.get("etag"),
            "title": google_task.get("title"),
            "updated": google_task.get("updated"),
            "self_link": google_task.get("selfLink"),
            "parent": google_task.get("parent"),
            "position": google_task.get("position"),
            "notes": google_task.get("notes"),
            "status": google_task.get("status"),
            "due": google_task.get("due"),
            "completed": google_task.get("completed"),
            "deleted": google_task.get("deleted", False),
            "hidden": google_task.get("hidden", False),
            "web_view_link": google_task.get("webViewLink"),
            "owner_id": uuid.UUID(self.user_id),
        }

        try:
            if instance:
                # Update existing
                serializer = TaskSerializer(
                    instance=instance, data=task_data, partial=True
                )
            else:
                # Create new or get existing
                try:
                    instance = Task.objects.get(
                        external_id=google_task["id"], owner_id=self.user_id
                    )
                    serializer = TaskSerializer(
                        instance=instance, data=task_data, partial=True
                    )
                except Task.DoesNotExist:
                    serializer = TaskSerializer(data=task_data)

            if serializer.is_valid():
                return serializer.save()
            else:
                self.logger.error(f"Serializer errors: {serializer.errors}")
                return None

        except Exception as e:
            self.logger.error(f"Error saving task to DB: {e}")
            return None
