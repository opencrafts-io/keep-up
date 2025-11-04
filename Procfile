release: python manage.py migrate
web: gunicorn --workers 1 --bind 0.0.0.0:8000 keep_up.wsgi:application --access-logfile - --error-logfile -

