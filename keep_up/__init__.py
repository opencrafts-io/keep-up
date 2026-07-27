from .celery import app as celery_app

# Importing the app here is what binds @shared_task and .delay() to the
# configured broker. Without it, Django processes fall back to Celery's
# default app and quietly enqueue against amqp://guest@localhost.
__all__ = ("celery_app",)
