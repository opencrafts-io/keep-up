import os
import uuid
from django.test.runner import DiscoverRunner
from django.conf import settings
from django.db import connections
from testcontainers.postgres import PostgresContainer
from urllib.parse import urlparse
from django.db.utils import DEFAULT_DB_ALIAS
from django.core.management import call_command


class TestcontainersRunner(DiscoverRunner):
    def __init__(self, *args, **kwargs):
        self.postgres = None
        self._test_db_name = None
        super().__init__(*args, **kwargs)

    def setup_databases(self, **kwargs):
        if not os.getenv("DB_NAME"):
            self.postgres = PostgresContainer("postgres:18")
            self.postgres.start()
            url = self.postgres.get_connection_url()
            parsed = urlparse(url)

            db_name = parsed.path[1:]
            self._test_db_name = f"test_{uuid.uuid4().hex[:8]}"
            db_config = {
                "ENGINE": "django.db.backends.postgresql_psycopg2",
                "NAME": db_name,
                "USER": parsed.username,
                "PASSWORD": parsed.password,
                "HOST": parsed.hostname,
                "PORT": parsed.port,
                "CONN_HEALTH_CHECKS": False,
                "OPTIONS": {},
                "ATOMIC_REQUESTS": False,
                "AUTOCOMMIT": True,
                "CONN_MAX_AGE": 0,
                "TIME_ZONE": None,
                "TEST": {
                    "NAME": self._test_db_name,
                    "CHARSET": "utf8",
                    "MIRROR": None,
                },
            }

            settings.DATABASES["default"] = db_config
            
            conn = connections[DEFAULT_DB_ALIAS]
            conn.settings_dict = db_config
            
            test_db_name = self._test_db_name
            
            class NoopCreation:
                def _create_test_db(self, verbosity, autoclobber, keepdb=False):
                    return test_db_name
                    
                def create_test_db(self, verbosity=1, autoclobber=False, keepdb=False):
                    return test_db_name
                    
                def destroy_test_db(self, test_db_name, verbosity=1, keepdb=False):
                    pass
                    
                def test_db_signature(self):
                    return (self.settings_dict["NAME"], test_db_name, True)
            
            NoopCreation.settings_dict = db_config
            conn.creation = NoopCreation()

            conn.close()
            conn.connect()
            
            call_command("migrate", verbosity=0)

        return super().setup_databases(**kwargs)

    def teardown_databases(self, *args, **kwargs):
        result = super().teardown_databases(*args, **kwargs)
        if self.postgres:
            self.postgres.stop()
        return result