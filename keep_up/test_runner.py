import os
from django.test.runner import DiscoverRunner
from django.conf import settings
from django.db import connections
from testcontainers.postgres import PostgresContainer
from urllib.parse import urlparse
from django.db.utils import DEFAULT_DB_ALIAS


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

            if DEFAULT_DB_ALIAS in connections._connections:
                del connections._connections[DEFAULT_DB_ALIAS]

            conn = connections.create_connection(DEFAULT_DB_ALIAS)
            conn.settings_dict = db_config

        return super().setup_databases(**kwargs)

    def teardown_databases(self, *args, **kwargs):
        result = super().teardown_databases(*args, **kwargs)
        if self.postgres:
            self.postgres.stop()
        return result