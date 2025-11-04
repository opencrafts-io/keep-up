# event_bus/management/commands/start_consumers.py
from django.core.management.base import BaseCommand
from event_bus.registry import CONSUMERS
import threading


class Command(BaseCommand):
    help = "Start all registered RabbitMQ consumers"

    def handle(self, *args, **options):
        threads = []

        if not CONSUMERS:
            self.stdout.write(self.style.WARNING("No consumers registered! Shutting down.."))
            return

        self.stdout.write(self.style.SUCCESS(f"Starting {len(CONSUMERS)} consumers..."))

        for consumer_cls in CONSUMERS:
            consumer = consumer_cls()
            t = threading.Thread(target=consumer.start, daemon=True)
            t.start()
            threads.append(t)
            self.stdout.write(
                self.style.SUCCESS(f"Started consumer: {consumer_cls.__name__}")
            )

        # Keep main thread alive
        for t in threads:
            t.join()
