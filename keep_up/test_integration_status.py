"""
Tests for the Google authorization status endpoint.

Authentication goes through the real VerisafeJWTAuthentication with a
genuinely signed token, so the wiring is covered rather than mocked away.
The token broker itself is patched: no network.
"""

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import jwt
from django.test import TestCase
from django.urls import reverse

from keep_up import verisafe_jwt
from verisafe.exceptions import (
    BrokerCredentialsRejected,
    NeedsAuthorization,
    ProviderDown,
    TokenBrokerError,
)

AUTHORIZE_URL = "https://verisafe.opencrafts.io/oauth/google/authorize"


def grants_response(capabilities, provider="google", revoked=False):
    return {
        "grants": [
            {
                "provider": provider,
                "granted_capabilities": capabilities,
                "granted_scopes": ["https://www.googleapis.com/auth/tasks"],
                "scopes_verified": True,
                "revoked": revoked,
                "connected_at": "2026-07-01T08:14:00Z",
            }
        ],
        "available_capabilities": {"google": ["calendar", "identity", "tasks"]},
    }


class IntegrationStatusTestCase(TestCase):
    def setUp(self):
        self.account_id = uuid.uuid4()
        self.url = reverse("google-integration-status")

        broker_patch = patch("keep_up.views.TokenBroker")
        self.broker_cls = broker_patch.start()
        self.broker = self.broker_cls.return_value
        self.addCleanup(broker_patch.stop)

        pull_patch = patch("todos.tasks.pull_from_google.delay")
        self.pull = pull_patch.start()
        self.addCleanup(pull_patch.stop)

    def bearer(self, account_id=None):
        """A real Verisafe-shaped JWT, signed with the configured secret."""
        now = datetime.now(timezone.utc)
        token = jwt.encode(
            {
                "sub": str(account_id or self.account_id),
                "iss": verisafe_jwt.VERISAFE_ISSUER,
                "aud": verisafe_jwt.VERISAFE_AUDIENCE,
                "iat": now,
                "exp": now + timedelta(hours=1),
            },
            verisafe_jwt.VERISAFE_API_SECRET,
            algorithm="HS256",
        )
        return f"Bearer {token}"

    def check(self, **extra):
        return self.client.post(
            self.url, HTTP_AUTHORIZATION=self.bearer(), **extra
        )


class GrantedTests(IntegrationStatusTestCase):
    def setUp(self):
        super().setUp()
        self.broker.grants.return_value = grants_response(
            ["identity", "calendar", "tasks"]
        )

    def test_reports_granted(self):
        response = self.check()

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["granted"])
        self.assertEqual(response.json()["missing_capabilities"], [])
        self.assertIsNone(response.json()["authorization"])

    def test_queues_a_pull_so_tasks_appear_promptly(self):
        response = self.check()

        self.pull.assert_called_once_with(str(self.account_id))
        self.assertTrue(response.json()["sync_queued"])

    def test_no_token_is_minted_to_answer_the_question(self):
        self.check()

        self.broker.token.assert_not_called()

    def test_reports_what_is_granted(self):
        response = self.check()

        self.assertEqual(
            response.json()["granted_capabilities"],
            ["identity", "calendar", "tasks"],
        )
        self.assertEqual(
            response.json()["required_capabilities"], ["calendar", "tasks"]
        )


class NotGrantedTests(IntegrationStatusTestCase):
    def setUp(self):
        super().setUp()
        self.broker.token.side_effect = NeedsAuthorization(
            provider="google",
            capabilities=["tasks"],
            authorization_url=AUTHORIZE_URL,
            reason="insufficient_scope",
            authorization_method="POST",
        )

    def test_partial_grant_reports_only_what_is_missing(self):
        self.broker.grants.return_value = grants_response(["identity", "calendar"])

        response = self.check()
        body = response.json()

        self.assertEqual(response.status_code, 200)
        self.assertFalse(body["granted"])
        self.assertEqual(body["missing_capabilities"], ["tasks"])

    def test_relays_the_authorization_instruction(self):
        self.broker.grants.return_value = grants_response(["identity"])

        body = self.check().json()

        self.assertEqual(body["authorization"]["url"], AUTHORIZE_URL)
        self.assertEqual(body["authorization"]["method"], "POST")
        self.assertEqual(body["authorization"]["capabilities"], ["tasks"])

    def test_no_grant_at_all_reports_both_missing(self):
        self.broker.grants.return_value = {"grants": []}

        body = self.check().json()

        self.assertFalse(body["granted"])
        self.assertEqual(body["missing_capabilities"], ["calendar", "tasks"])

    def test_a_revoked_grant_counts_as_nothing_granted(self):
        self.broker.grants.return_value = grants_response(
            ["identity", "calendar", "tasks"], revoked=True
        )

        body = self.check().json()

        self.assertFalse(body["granted"])
        self.assertEqual(body["missing_capabilities"], ["calendar", "tasks"])

    def test_another_providers_grant_does_not_count(self):
        self.broker.grants.return_value = grants_response(
            ["identity", "calendar", "tasks"], provider="spotify"
        )

        body = self.check().json()

        self.assertFalse(body["granted"])

    def test_no_pull_is_queued(self):
        self.broker.grants.return_value = grants_response(["identity"])

        self.check()

        self.pull.assert_not_called()

    def test_a_lost_instruction_still_answers_the_question(self):
        self.broker.grants.return_value = grants_response(["identity"])
        self.broker.token.side_effect = TokenBrokerError("broker hiccup")

        body = self.check().json()

        self.assertFalse(body["granted"])
        self.assertEqual(body["missing_capabilities"], ["calendar", "tasks"])
        self.assertIsNone(body["authorization"])


class BrokerFailureTests(IntegrationStatusTestCase):
    def test_provider_down_is_a_503(self):
        self.broker.grants.side_effect = ProviderDown("upstream")

        response = self.check()

        self.assertEqual(response.status_code, 503)
        self.pull.assert_not_called()

    def test_our_bad_credentials_are_not_reported_as_the_users_problem(self):
        """A 403 here would send the user to re-authorize our misconfiguration."""
        self.broker.grants.side_effect = BrokerCredentialsRejected(
            "missing oauth-token-broker role"
        )

        response = self.check()

        self.assertEqual(response.status_code, 502)
        self.assertNotEqual(response.status_code, 403)

    def test_unexpected_broker_failure_is_a_502(self):
        self.broker.grants.side_effect = TokenBrokerError("unexpected status 418")

        response = self.check()

        self.assertEqual(response.status_code, 502)


class AuthenticationTests(IntegrationStatusTestCase):
    def test_rejects_a_request_with_no_token(self):
        response = self.client.post(self.url)

        self.assertEqual(response.status_code, 403)
        self.broker.grants.assert_not_called()

    def test_rejects_a_token_signed_with_the_wrong_secret(self):
        now = datetime.now(timezone.utc)
        forged = jwt.encode(
            {
                "sub": str(self.account_id),
                "iss": verisafe_jwt.VERISAFE_ISSUER,
                "aud": verisafe_jwt.VERISAFE_AUDIENCE,
                "exp": now + timedelta(hours=1),
            },
            "not-the-real-secret",
            algorithm="HS256",
        )

        response = self.client.post(
            self.url, HTTP_AUTHORIZATION=f"Bearer {forged}"
        )

        self.assertEqual(response.status_code, 403)
        self.broker.grants.assert_not_called()

    def test_rejects_an_expired_token(self):
        past = datetime.now(timezone.utc) - timedelta(hours=2)
        expired = jwt.encode(
            {
                "sub": str(self.account_id),
                "iss": verisafe_jwt.VERISAFE_ISSUER,
                "aud": verisafe_jwt.VERISAFE_AUDIENCE,
                "exp": past,
            },
            verisafe_jwt.VERISAFE_API_SECRET,
            algorithm="HS256",
        )

        response = self.client.post(
            self.url, HTTP_AUTHORIZATION=f"Bearer {expired}"
        )

        self.assertEqual(response.status_code, 403)

    def test_the_checked_account_is_the_one_in_the_token(self):
        self.broker.grants.return_value = grants_response(["calendar", "tasks"])
        someone_else = uuid.uuid4()

        self.client.post(self.url, HTTP_AUTHORIZATION=self.bearer(someone_else))

        self.broker.grants.assert_called_once_with(str(someone_else))
        self.pull.assert_called_once_with(str(someone_else))
