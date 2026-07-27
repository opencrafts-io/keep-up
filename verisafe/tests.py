"""
Tests for the Verisafe token broker client.

No network and no database: every HTTP exchange is stubbed. The cases mirror
the "Testing your integration" section of Verisafe's SERVICE_INTEGRATION.md.
"""

import json
from unittest.mock import patch

import requests
from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase, override_settings

from .exceptions import (
    BrokerCredentialsRejected,
    NeedsAuthorization,
    ProviderDown,
    TokenBrokerError,
)
from .token_broker import (
    CAPABILITY_IDENTITY,
    CAPABILITY_TASKS,
    PROVIDER_APPLE,
    PROVIDER_CAPABILITIES,
    PROVIDER_GOOGLE,
    PROVIDER_SPOTIFY,
    BrokeredToken,
    TokenBroker,
    provider_token,
)

ACCOUNT_ID = "9f1c8b2e-0000-4000-8000-000000000001"
ACCESS_TOKEN = "ya29.a0AfH6SMB-super-secret-value"

SUCCESS_BODY = {
    "provider": "google",
    "account_id": ACCOUNT_ID,
    "access_token": ACCESS_TOKEN,
    "token_type": "Bearer",
    "expires_at": "2026-07-27T13:31:00Z",
    "expires_in": 3413,
    "granted_scopes": ["https://www.googleapis.com/auth/tasks"],
    "scopes_verified": True,
    "refreshed": False,
    "from_cache": True,
}


def stub_response(status_code, body=None, text=None):
    """Build a real Response so json() and status handling behave normally."""
    response = requests.Response()
    response.status_code = status_code
    if text is not None:
        response._content = text.encode()
    else:
        response._content = json.dumps(body if body is not None else {}).encode()
    response.headers["Content-Type"] = "application/json"
    return response


@override_settings(
    VERISAFE_BASE_URL="https://verisafe.qa.example.com",
    VERISAFE_API_KEY="vst_test_key",
    VERISAFE_TIMEOUT=10,
    VERISAFE_RETRIES=2,
)
class TokenBrokerTests(SimpleTestCase):
    def broker(self):
        return TokenBroker()

    def call_with(self, response):
        """Run token() against a stubbed HTTP response, returning the mock."""
        with patch.object(
            requests.Session, "request", return_value=response
        ) as request:
            try:
                result = self.broker().token(
                    PROVIDER_GOOGLE, ACCOUNT_ID, [CAPABILITY_TASKS]
                )
            except Exception as exc:  # surfaced to the caller for assertions
                return request, exc
            return request, result

    # =========================================================
    # 200
    # =========================================================

    def test_returns_token_on_success(self):
        _, token = self.call_with(stub_response(200, SUCCESS_BODY))

        self.assertIsInstance(token, BrokeredToken)
        self.assertEqual(token.access_token, ACCESS_TOKEN)
        self.assertEqual(token.token_type, "Bearer")
        self.assertEqual(token.expires_in, 3413)
        self.assertTrue(token.from_cache)
        self.assertEqual(
            token.granted_scopes, ["https://www.googleapis.com/auth/tasks"]
        )

    def test_sends_api_key_and_capabilities(self):
        request, _ = self.call_with(stub_response(200, SUCCESS_BODY))

        _, kwargs = request.call_args
        self.assertEqual(kwargs["headers"]["X-API-Key"], "vst_test_key")
        self.assertEqual(
            kwargs["json"],
            {"account_id": ACCOUNT_ID, "capabilities": [CAPABILITY_TASKS]},
        )
        self.assertEqual(kwargs["timeout"], 10)
        self.assertIn(
            "https://verisafe.qa.example.com/oauth/google/token",
            request.call_args[0],
        )

    def test_success_without_access_token_is_an_error(self):
        body = dict(SUCCESS_BODY)
        del body["access_token"]

        _, exc = self.call_with(stub_response(200, body))

        self.assertIsInstance(exc, TokenBrokerError)

    def test_unverified_scopes_are_not_an_error(self):
        body = dict(SUCCESS_BODY, scopes_verified=False)

        _, token = self.call_with(stub_response(200, body))

        self.assertFalse(token.scopes_verified)
        self.assertEqual(token.access_token, ACCESS_TOKEN)

    # =========================================================
    # 403 / 404 - the user must act
    # =========================================================

    def test_insufficient_scope_raises_needs_authorization(self):
        body = {
            "error": "insufficient_scope",
            "provider": "google",
            "missing_capabilities": ["tasks"],
            "granted_capabilities": ["identity"],
            "authorization_url": "https://verisafe.qa.example.com/oauth/google/authorize",
        }

        _, exc = self.call_with(stub_response(403, body))

        self.assertIsInstance(exc, NeedsAuthorization)
        self.assertEqual(exc.capabilities, ["tasks"])
        self.assertEqual(
            exc.authorization_url,
            "https://verisafe.qa.example.com/oauth/google/authorize",
        )
        self.assertEqual(exc.reason, "insufficient_scope")

    def test_reauthorization_required_raises_needs_authorization(self):
        body = {
            "error": "reauthorization_required",
            "reason": "invalid_grant",
            "authorization_url": "https://verisafe.qa.example.com/oauth/google/authorize",
        }

        _, exc = self.call_with(stub_response(403, body))

        self.assertIsInstance(exc, NeedsAuthorization)
        self.assertEqual(exc.reason, "reauthorization_required")

    def test_no_grant_raises_needs_authorization(self):
        body = {
            "error": "no_grant",
            "authorization_url": "https://verisafe.qa.example.com/oauth/google/authorize",
        }

        _, exc = self.call_with(stub_response(404, body))

        self.assertIsInstance(exc, NeedsAuthorization)
        self.assertEqual(exc.reason, "no_grant")

    def test_missing_capabilities_falls_back_to_requested(self):
        body = {"error": "insufficient_scope", "authorization_url": "https://x/y"}

        _, exc = self.call_with(stub_response(403, body))

        self.assertEqual(exc.capabilities, [CAPABILITY_TASKS])

    # =========================================================
    # Our credentials, not the user's grant
    # =========================================================

    def test_403_without_error_field_means_missing_broker_role(self):
        _, exc = self.call_with(stub_response(403, {}))

        self.assertIsInstance(exc, BrokerCredentialsRejected)
        self.assertIn("oauth-token-broker", str(exc))

    def test_403_with_html_body_means_missing_broker_role(self):
        _, exc = self.call_with(stub_response(403, text="<html>Forbidden</html>"))

        self.assertIsInstance(exc, BrokerCredentialsRejected)

    def test_401_rejects_our_api_key(self):
        _, exc = self.call_with(stub_response(401, {}))

        self.assertIsInstance(exc, BrokerCredentialsRejected)
        self.assertIn("X-API-Key", str(exc))

    # =========================================================
    # Transient
    # =========================================================

    def test_503_raises_provider_down(self):
        _, exc = self.call_with(
            stub_response(503, {"error": "an upstream dependency is unavailable"})
        )

        self.assertIsInstance(exc, ProviderDown)

    def test_connection_failure_raises_provider_down(self):
        with patch.object(
            requests.Session,
            "request",
            side_effect=requests.exceptions.ConnectionError("boom"),
        ):
            with self.assertRaises(ProviderDown):
                self.broker().token(PROVIDER_GOOGLE, ACCOUNT_ID, [CAPABILITY_TASKS])

    def test_retries_are_configured_for_503(self):
        adapter = self.broker().session.get_adapter("https://verisafe.qa.example.com")
        retry = adapter.max_retries

        self.assertIn(503, retry.status_forcelist)
        self.assertIn("POST", retry.allowed_methods)
        self.assertEqual(retry.total, 2)

    # =========================================================
    # Everything else
    # =========================================================

    def test_unexpected_status_raises_broker_error(self):
        _, exc = self.call_with(stub_response(400, {"error": "bad capability"}))

        self.assertIsInstance(exc, TokenBrokerError)
        self.assertNotIsInstance(exc, NeedsAuthorization)
        self.assertIn("400", str(exc))

    def test_non_json_success_body_raises_broker_error(self):
        _, exc = self.call_with(stub_response(200, text="not json"))

        self.assertIsInstance(exc, TokenBrokerError)

    def test_409_explains_the_provider_cannot_refresh(self):
        _, exc = self.call_with(stub_response(409, {}))

        self.assertIsInstance(exc, TokenBrokerError)
        self.assertIn("sign in", str(exc))

    # =========================================================
    # Providers and capabilities
    # =========================================================

    def test_apple_brokers_nothing(self):
        self.assertEqual(PROVIDER_CAPABILITIES[PROVIDER_APPLE], frozenset())

    def test_provider_capabilities_match_the_broker(self):
        self.assertEqual(
            PROVIDER_CAPABILITIES[PROVIDER_GOOGLE],
            frozenset({"identity", "calendar", "tasks"}),
        )
        self.assertEqual(
            PROVIDER_CAPABILITIES[PROVIDER_SPOTIFY],
            frozenset({"identity", "playback", "playlist", "library"}),
        )

    def test_apple_identity_request_surfaces_the_broker_refusal(self):
        """Apple is sign-in only, so the broker declines rather than issuing."""
        body = {
            "error": "no_grant",
            "authorization_url": "https://verisafe.qa.example.com/oauth/apple/authorize",
        }

        with patch.object(
            requests.Session, "request", return_value=stub_response(404, body)
        ) as request:
            with self.assertRaises(NeedsAuthorization):
                self.broker().token(
                    PROVIDER_APPLE, ACCOUNT_ID, [CAPABILITY_IDENTITY]
                )

        self.assertIn(
            "https://verisafe.qa.example.com/oauth/apple/token",
            request.call_args[0],
        )

    # =========================================================
    # The token must not leak
    # =========================================================

    def test_token_is_never_logged(self):
        with self.assertLogs("keep_up", level="DEBUG") as logs:
            self.call_with(stub_response(200, SUCCESS_BODY))

        self.assertTrue(logs.output, "expected at least one log record")
        for line in logs.output:
            self.assertNotIn(ACCESS_TOKEN, line)

    def test_repr_redacts_the_token(self):
        token = BrokeredToken(access_token=ACCESS_TOKEN)

        self.assertNotIn(ACCESS_TOKEN, repr(token))
        self.assertIn("REDACTED", repr(token))

    # =========================================================
    # Pre-flight grants
    # =========================================================

    def test_grants_returns_body(self):
        body = {"grants": [{"provider": "google", "capabilities": ["identity"]}]}

        with patch.object(
            requests.Session, "request", return_value=stub_response(200, body)
        ) as request:
            result = self.broker().grants(ACCOUNT_ID)

        self.assertEqual(result, body)
        _, kwargs = request.call_args
        self.assertEqual(kwargs["params"], {"account_id": ACCOUNT_ID})

    def test_grants_surfaces_credential_rejection(self):
        with patch.object(
            requests.Session, "request", return_value=stub_response(401, {})
        ):
            with self.assertRaises(BrokerCredentialsRejected):
                self.broker().grants(ACCOUNT_ID)

    # =========================================================
    # Convenience wrapper
    # =========================================================

    def test_provider_token_wrapper(self):
        with patch.object(
            requests.Session, "request", return_value=stub_response(200, SUCCESS_BODY)
        ):
            token = provider_token(PROVIDER_GOOGLE, ACCOUNT_ID, [CAPABILITY_TASKS])

        self.assertEqual(token.access_token, ACCESS_TOKEN)


class TokenBrokerConfigurationTests(SimpleTestCase):
    @override_settings(VERISAFE_BASE_URL=None, VERISAFE_API_KEY="vst_test_key")
    def test_missing_base_url_is_improperly_configured(self):
        with self.assertRaises(ImproperlyConfigured) as ctx:
            TokenBroker()

        self.assertIn("VERISAFE_BASE_URL", str(ctx.exception))

    @override_settings(
        VERISAFE_BASE_URL="https://verisafe.qa.example.com", VERISAFE_API_KEY=None
    )
    def test_missing_api_key_is_improperly_configured(self):
        with self.assertRaises(ImproperlyConfigured) as ctx:
            TokenBroker()

        self.assertIn("VERISAFE_API_KEY", str(ctx.exception))

    @override_settings(
        VERISAFE_BASE_URL="https://verisafe.qa.example.com/",
        VERISAFE_API_KEY="vst_test_key",
    )
    def test_trailing_slash_does_not_double_up(self):
        with patch.object(
            requests.Session, "request", return_value=stub_response(200, SUCCESS_BODY)
        ) as request:
            TokenBroker().token(PROVIDER_GOOGLE, ACCOUNT_ID, [CAPABILITY_TASKS])

        self.assertIn(
            "https://verisafe.qa.example.com/oauth/google/token",
            request.call_args[0],
        )
