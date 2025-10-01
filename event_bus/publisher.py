import pika
from django.conf import settings
import threading


def _get_connection():
    creds = pika.PlainCredentials(settings.RABBITMQ_USER, settings.RABBITMQ_PASSWORD)
    return pika.BlockingConnection(
        pika.ConnectionParameters(
            host=settings.RABBITMQ_HOST,
            port=settings.RABBITMQ_PORT,
            virtual_host=settings.RABBITMQ_VHOST,
            credentials=creds,
        )
    )


def _publish(exchange: str, queue_name: str, message: str):
    conn = _get_connection()
    ch = conn.channel()
    ch.queue_declare(queue=queue_name, durable=True)
    ch.basic_publish(
        exchange=exchange,
        routing_key=queue_name,
        body=message,
        properties=pika.BasicProperties(delivery_mode=2),
    )
    conn.close()


def publish(exchange: str, queue_name: str, message: str):
    thread = threading.Thread(
        target=_publish,
        args=(exchange, queue_name, message),
        daemon=True,  # thread won't block Django shutdown
    )
    thread.start()
