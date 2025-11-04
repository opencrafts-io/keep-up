from time import time
import pika
from django.conf import settings
import logging


class BaseConsumer:

    def __init__(self) -> None:
        self.queue_name = None
        self.exchange_name = None
        self.exchange_type = "topic"
        self.routing_key = "#"
        self.logger = logging.getLogger("BaseConsumerLogger")

    def validate_event(
        self,
        event: dict,
        expected_type: str,
        source_service: str = "io.opencrafts.verisafe",
    ) -> bool:
        """Validate event metadata."""
        metadata = event.get("meta", {})
        if metadata.get("event_type") != expected_type:
            self.logger.error(
                f"[{str(type(self).__name__)}] Wrong event_type: expected {expected_type}, got {metadata.get('event_type')}",
                extra={
                    "abort": True,
                    "user_id": event.get("payload", {}).get("user_id"),
                },
            )
            return False
        if metadata.get("source_service_id") != source_service:
            self.logger.error(
                f"[{str(type(self).__name__)}] Wrong source_service_id: expected {source_service}, got {metadata.get('source_service_id')}",
                extra={
                    "abort": True,
                    "user_id": event.get("payload", {}).get("user_id"),
                },
            )
            return False
        return True

    def handle_message(self, body: str, routing_key: str):
        """Override this in subclasses."""
        raise NotImplementedError

    def start(self):

        if not self.queue_name:
            raise ValueError(f"{self.__class__.__name__} must define queue_name")
        creds = pika.PlainCredentials(
            settings.RABBITMQ_USER, settings.RABBITMQ_PASSWORD
        )
        conn = pika.BlockingConnection(
            pika.ConnectionParameters(
                host=settings.RABBITMQ_HOST,
                port=settings.RABBITMQ_PORT,
                virtual_host=settings.RABBITMQ_VHOST,
                credentials=creds,
            )
        )
        ch = conn.channel()

        # Declare exchange if specified
        if self.exchange_name:
            ch.exchange_declare(
                exchange=self.exchange_name,
                exchange_type=self.exchange_type,
                durable=True,
            )

        # Declare queue
        ch.queue_declare(queue=self.queue_name, durable=True)

        # Bind queue to exchange if exchange_name is set
        if self.exchange_name:
            ch.queue_bind(
                queue=self.queue_name,
                exchange=self.exchange_name,
                routing_key=self.routing_key,
            )

        def callback(ch, method, properties, body):
            self.handle_message(body.decode(), method.routing_key)

        ch.basic_consume(
            queue=self.queue_name,
            on_message_callback=callback,
            auto_ack=True,
        )
        self.logger.info(
            f"[{str(type(self).__name__)}] Listening for events on queue {self.queue_name}, bound to exchange {self.exchange_name}",
        )
        ch.start_consuming()
