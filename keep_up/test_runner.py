import os
from django.test.runner import DiscoverRunner
from django.conf import settings
from django.db import connections
from testcontainers.postgres import PostgresContainer
from urllib.parse import urlparse


class TestcontainersRunner(DiscoverRunner):
    def __init__(self, *args, **kwargs):
        self.postgres = None
        super().__init__(*args, **kwargs)

    def setup_databases(self, **kwargs):
        if not os.getenv("DB_NAME"):
            self.postgres = PostgresContainer("postgres:18")
            self.postgres.start()
            url = self.postgres.get_connection_url()
            parsed = urlparse(url)

            db_config = {
                "ENGINE": "django.db.backends.postgresql_psycopg2",
                "NAME": parsed.path[1:],
                "USER": parsed.username,
                "PASSWORD": parsed.password,
                "HOST": parsed.hostname,
                "PORT": parsed.port,
            }
            settings.DATABASES["default"] = db_config

            connections.close_all()

        return super().setup_databases(**kwargs)

    def teardown_databases(self, *args, **kwargs):
        result = super().teardown_databases(*args, **kwargs)
        if self.postgres:
            self.postgres.stop()
        return result