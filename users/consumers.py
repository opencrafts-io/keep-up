import json
import uuid
import logging
from event_bus.consumer import BaseConsumer
from event_bus.registry import register
from .models import User


@register
class VerisafeUserEventConsumer(BaseConsumer):
    def __init__(self) -> None:
        self.queue_name = "io.opencrafts.keep_up.verisafe.user.events"
        self.exchange_name = "verisafe.exchange"
        self.exchange_type = "fanout"
        self.routing_key = "verisafe.user.events"
        self.logger = logging.getLogger(f"{type(self).__name__}")

    def handle_message(self, body: str, routing_key=None):
        try:
            event = json.loads(body)
        except json.JSONDecodeError as e:
            self.logger.error(
                "Failed to decode message", extra={"body": body, "exception": str(e)}
            )
            return

        if not self.validate_event(event):
            return

        payload = event.get("user", {})
        metadata = event.get("meta", {})
        user = None
        try:

            match metadata.get("event_type"):
                case "user.created" | "user.updated":
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

                case "user.deleted":
                    user_id = payload.get("id")
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
             
                case _:
                        raise Exception("Failed to process user event due to incorrect event type")

        except Exception as e:
            self.logger.exception(
                "Failed to process user event",
                extra={"payload": payload, "exception": str(e)},
            )
