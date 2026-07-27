web: python manage.py migrate && (python manage.py run_consumers &) && gunicorn --workers 1 --bind 0.0.0.0:8000 keep_up.wsgi:application --access-logfile - --error-logfile -
worker: celery -A keep_up worker --loglevel=INFO --queues=google_sync,keep_up --concurrency=2
beat: celery -A keep_up beat --loglevel=INFO
