"""
Tests for AMQP URL assembly.

Assertions go through kombu's own parser, which is what Celery uses, rather
than urllib: the bug these cover surfaced inside kombu.
"""

from django.test import SimpleTestCase
from kombu.utils.url import parse_url

from keep_up.broker_url import amqp_url


class AmqpUrlTests(SimpleTestCase):
    def parts(self, password="simplepass", user="keepup", vhost="keepup"):
        return parse_url(amqp_url(user, password, "rabbit", "5672", vhost))

    def test_plain_credentials_round_trip(self):
        parts = self.parts()

        self.assertEqual(parts["userid"], "keepup")
        self.assertEqual(parts["password"], "simplepass")
        self.assertEqual(parts["hostname"], "rabbit")
        self.assertEqual(parts["port"], 5672)
        self.assertEqual(parts["virtual_host"], "keepup")

    def test_hash_in_password(self):
        """Naive interpolation truncated the netloc here and the port became
        the password's leading chunk."""
        self.assertEqual(self.parts(password="F5a8#Zq2w")["password"], "F5a8#Zq2w")

    def test_slash_in_password(self):
        self.assertEqual(self.parts(password="F5a8/Zq2w")["password"], "F5a8/Zq2w")

    def test_question_mark_in_password(self):
        self.assertEqual(self.parts(password="F5a8?Zq2w")["password"], "F5a8?Zq2w")

    def test_at_sign_in_password(self):
        self.assertEqual(self.parts(password="F5a8@Zq2w")["password"], "F5a8@Zq2w")

    def test_colon_in_password(self):
        self.assertEqual(self.parts(password="F5a8:Zq2w")["password"], "F5a8:Zq2w")

    def test_percent_in_password(self):
        self.assertEqual(self.parts(password="F5a8%Zq2w")["password"], "F5a8%Zq2w")

    def test_the_port_survives_a_hostile_password(self):
        """The symptom that started this: the port parsed as a password chunk."""
        self.assertEqual(self.parts(password="F5a8#Zq2w")["port"], 5672)

    def test_special_characters_in_the_username(self):
        parts = self.parts(user="keep#up")

        self.assertEqual(parts["userid"], "keep#up")
        self.assertEqual(parts["port"], 5672)

    def test_default_vhost_still_resolves(self):
        self.assertEqual(self.parts(vhost="/")["virtual_host"], "/")

    def test_missing_credentials_do_not_raise(self):
        """Unset env should fail at connect time, not while building settings."""
        parts = parse_url(amqp_url(None, None, "rabbit", "5672", None))

        self.assertEqual(parts["hostname"], "rabbit")
        self.assertEqual(parts["port"], 5672)

    def test_settings_url_is_parseable(self):
        """Whatever this environment is configured with must actually parse."""
        from django.conf import settings

        parts = parse_url(settings.CELERY_BROKER_URL)

        self.assertEqual(parts["transport"], "amqp")
