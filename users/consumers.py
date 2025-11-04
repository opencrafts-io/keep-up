import json
import uuid
import logging
from event_bus.consumer import BaseConsumer
from event_bus.registry import register
from .models import User


@register
class VerisafeUserCreatedEventConsumer(BaseConsumer):
    def __init__(self) -> None:
        self.queue_name = "io.opencrafts.keep_up.verisafe.user.created"
        self.exchange_name = "verisafe.exchange"
        self.exchange_type = "direct"
        self.routing_key = "verisafe.user.created"
        self.logger = logging.getLogger(f"{type(self).__name__}")

    def handle_message(self, body: str, routing_key=None):
        try:
            event = json.loads(body)
        except json.JSONDecodeError as e:
            self.logger.error(
                "Failed to decode message", extra={"body": body, "exception": str(e)}
            )
            return

        if not self.validate_event(event, "user.created"):
            return

        payload = event.get("user", {})
        user = None
        try:
            user, created = User.objects.update_or_create(
                user_id=uuid.UUID(payload["id"]),
                defaults={
                    "name": payload.get("name"),
                    "username": payload.get("username"),
                    "email": payload.get("email"),
                    "phone": payload.get("phone"),
                    "avatar_url": payload.get("avatar_url"),
                    "vibe_points": payload.get("vibe_points", 0),
                },
            )
            action = "created" if created else "updated"
            self.logger.info(
                f"User @{user.username} {action} successfully",
                extra={"user_id": str(user.user_id), "event": "user.created"},
            )

        except Exception as e:
            self.logger.exception(
                "Failed to create/update user",
                extra={"payload": payload, "exception": str(e)},
            )


@register
class VerisafeUserUpdatedEventConsumer(BaseConsumer):
    def __init__(self) -> None:
        self.queue_name = "io.opencrafts.keep_up.verisafe.user.updated"
        self.exchange_name = "verisafe.exchange"
        self.exchange_type = "direct"
        self.routing_key = "verisafe.user.updated"
        self.logger = logging.getLogger(f"{type(self).__name__}")

    def handle_message(self, body: str, routing_key=None):
        try:
            event = json.loads(body)
        except json.JSONDecodeError as e:
            self.logger.error(
                "Failed to decode message", extra={"body": body, "exception": str(e)}
            )
            return

        if not self.validate_event(event, "user.updated"):
            return

        payload = event.get("user", {})
        try:
            user, _ = User.objects.update_or_create(
                user_id=uuid.UUID(payload["id"]),
                defaults={
                    "name": payload.get("name"),
                    "username": payload.get("username"),
                    "email": payload.get("email"),
                    "phone": payload.get("phone"),
                    "avatar_url": payload.get("avatar_url"),
                    "vibe_points": payload.get("vibe_points", 0),
                },
            )
            self.logger.info(
                f"User @{user.username} updated successfully",
                extra={"user_id": str(user.user_id), "event": "user.updated"},
            )

        except Exception as e:
            self.logger.exception(
                "Failed to update user", extra={"payload": payload, "exception": str(e)}
            )


@register
class VerisafeUserDeletedEventConsumer(BaseConsumer):
    def __init__(self) -> None:
        self.queue_name = "io.opencrafts.keep_up.verisafe.user.deleted"
        self.exchange_name = "verisafe.exchange"
        self.exchange_type = "direct"
        self.routing_key = "verisafe.user.deleted"
        self.logger = logging.getLogger(f"{type(self).__name__}")

    def handle_message(self, body: str, routing_key=None):
        try:
            event = json.loads(body)
        except json.JSONDecodeError as e:
            self.logger.error(
                "Failed to decode message", extra={"body": body, "exception": str(e)}
            )
            return

        if not self.validate_event(event, "user.deleted"):
            return

        payload = event.get("user", {})
        user_id = payload.get("id")
        try:
            deleted_count, _ = User.objects.filter(user_id=uuid.UUID(user_id)).delete()
            if deleted_count:
                self.logger.info(
                    f"User {user_id} deleted successfully",
                    extra={"user_id": user_id, "event": "user.deleted"},
                )
            else:
                self.logger.warning(
                    f"User {user_id} not found for deletion",
                    extra={"user_id": user_id, "event": "user.deleted"},
                )
        except Exception as e:
            self.logger.exception(
                f"Failed to delete user {user_id}",
                extra={"payload": payload, "exception": str(e)},
            )
