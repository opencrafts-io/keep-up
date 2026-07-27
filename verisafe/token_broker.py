"""
Client for the Verisafe OAuth token broker.

This service never stores provider credentials. Each time it needs to call
Google on a user's behalf it asks Verisafe for an access token, uses it, and
forgets it. Verisafe owns refresh, expiry, encryption, and revocation.

Never cache, persist, or log the token this module returns. Verisafe already
caches it; a second copy risks serving a credential past revocation.
"""

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import requests
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .exceptions import (
    BrokerCredentialsRejected,
    NeedsAuthorization,
    ProviderDown,
    TokenBrokerError,
)

logger = logging.getLogger("keep_up")

PROVIDER_GOOGLE = "google"
PROVIDER_SPOTIFY = "spotify"
# Apple is sign-in only. Its identity grant is not brokerable, so asking this
# endpoint for an apple token yields no usable credential.
PROVIDER_APPLE = "apple"

# A capability only means something paired with a provider: `identity` under
# google is not the same grant as `identity` under spotify. Ask for the
# narrowest set needed, since over-asking turns a working request into a
# consent prompt for half the users.
CAPABILITY_IDENTITY = "identity"
CAPABILITY_CALENDAR = "calendar"
CAPABILITY_TASKS = "tasks"
CAPABILITY_PLAYBACK = "playback"
CAPABILITY_PLAYLIST = "playlist"
CAPABILITY_LIBRARY = "library"

# What each provider can actually broker. Apple is deliberately empty rather
# than absent: it is a supported sign-in provider that brokers nothing.
PROVIDER_CAPABILITIES = {
    PROVIDER_GOOGLE: frozenset(
        {CAPABILITY_IDENTITY, CAPABILITY_CALENDAR, CAPABILITY_TASKS}
    ),
    PROVIDER_SPOTIFY: frozenset(
        {
            CAPABILITY_IDENTITY,
            CAPABILITY_PLAYBACK,
            CAPABILITY_PLAYLIST,
            CAPABILITY_LIBRARY,
        }
    ),
    PROVIDER_APPLE: frozenset(),
}

# Error codes that mean the user must act. Retrying these just hammers the
# provider's token endpoint through Verisafe.
NEEDS_AUTHORIZATION_ERRORS = frozenset(
    {"insufficient_scope", "reauthorization_required", "no_grant"}
)


@dataclass(frozen=True)
class BrokeredToken:
    """
    A provider access token that is valid right now.

    `refreshed` and `from_cache` are diagnostics for logging, not something to
    branch on. `scopes_verified=False` is not an error: it means Verisafe
    inferred the scope list for a grant that predates scope recording.
    """

    access_token: str
    token_type: str = "Bearer"
    expires_at: Optional[str] = None
    expires_in: Optional[int] = None
    granted_scopes: List[str] = field(default_factory=list)
    scopes_verified: bool = True
    refreshed: bool = False
    from_cache: bool = False

    def __repr__(self) -> str:
        """Redact the token: a default repr leaks it into logs and tracebacks."""
        return (
            f"BrokeredToken(access_token='[REDACTED]', "
            f"token_type={self.token_type!r}, expires_at={self.expires_at!r}, "
            f"from_cache={self.from_cache!r}, refreshed={self.refreshed!r})"
        )


class TokenBroker:
    """
    Talks to Verisafe's token broker endpoints.

    Configuration comes from settings so QA and production differ only by the
    environment they load.
    """

    def __init__(
        self,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        timeout: Optional[float] = None,
        session: Optional[requests.Session] = None,
    ) -> None:
        self.base_url = (base_url or getattr(settings, "VERISAFE_BASE_URL", None) or "").rstrip("/")
        self.api_key = api_key or getattr(settings, "VERISAFE_API_KEY", None)
        self.timeout = timeout or getattr(settings, "VERISAFE_TIMEOUT", 10)

        if not self.base_url:
            raise ImproperlyConfigured(
                "VERISAFE_BASE_URL is not set. Point it at the Verisafe "
                "instance for this environment."
            )
        if not self.api_key:
            raise ImproperlyConfigured(
                "VERISAFE_API_KEY is not set. It must be a service token whose "
                "bot account holds the oauth-token-broker role."
            )

        self.session = session or self._build_session()

    @staticmethod
    def _build_session() -> requests.Session:
        """
        Retry 503s and connection failures with backoff.

        Retrying a POST is normally unsafe. It is correct here because issuing
        a token is idempotent and a provider outage is explicitly never
        treated as a revocation.
        """
        retry = Retry(
            total=getattr(settings, "VERISAFE_RETRIES", 2),
            backoff_factor=0.5,
            status_forcelist=(503,),
            allowed_methods=frozenset({"GET", "POST"}),
            raise_on_status=False,
        )
        session = requests.Session()
        session.mount("https://", HTTPAdapter(max_retries=retry))
        session.mount("http://", HTTPAdapter(max_retries=retry))
        return session

    def token(
        self,
        provider: str,
        account_id: str,
        capabilities: List[str],
    ) -> BrokeredToken:
        """
        Fetch an access token covering `capabilities` for one user.

        `account_id` is the user's Verisafe account UUID, which arrives on
        authenticated requests as `request.user_id`.

        Raises:
            NeedsAuthorization: the user has not granted these capabilities.
            ProviderDown: transient; retry with backoff.
            BrokerCredentialsRejected: our API key or bot role is wrong.
            TokenBrokerError: any other unexpected response.
        """
        url = f"{self.base_url}/oauth/{provider}/token"
        payload = {"account_id": str(account_id), "capabilities": list(capabilities)}

        try:
            response = self.session.post(
                url,
                headers={"X-API-Key": self.api_key},
                json=payload,
                timeout=self.timeout,
            )
        except requests.exceptions.RequestException as exc:
            # Failing to reach Verisafe is the same class of problem as
            # Verisafe failing to reach the provider: transient, retry.
            raise ProviderDown(
                f"could not reach the token broker: {exc}"
            ) from exc

        return self._handle_token_response(response, provider, account_id, capabilities)

    def _handle_token_response(
        self,
        response: requests.Response,
        provider: str,
        account_id: str,
        capabilities: List[str],
    ) -> BrokeredToken:
        if response.status_code == 200:
            body = self._json(response)
            token = BrokeredToken(
                access_token=body.get("access_token", ""),
                token_type=body.get("token_type", "Bearer"),
                expires_at=body.get("expires_at"),
                expires_in=body.get("expires_in"),
                granted_scopes=body.get("granted_scopes") or [],
                scopes_verified=body.get("scopes_verified", True),
                refreshed=body.get("refreshed", False),
                from_cache=body.get("from_cache", False),
            )
            if not token.access_token:
                raise TokenBrokerError(
                    "token broker returned 200 without an access_token"
                )
            # Diagnostics only. The token itself never reaches a log record.
            logger.info(
                "Brokered %s token for account %s",
                provider,
                account_id,
                extra={
                    "provider": provider,
                    "account_id": str(account_id),
                    "capabilities": list(capabilities),
                    "from_cache": token.from_cache,
                    "refreshed": token.refreshed,
                    "scopes_verified": token.scopes_verified,
                },
            )
            return token

        if response.status_code == 503:
            raise ProviderDown("oauth provider temporarily unavailable")

        if response.status_code in (403, 404):
            # A proxy can return HTML here. An unparseable body is not an
            # error field, which lands on the credentials branch below.
            body = self._json_or_empty(response)
            error = body.get("error")

            if error in NEEDS_AUTHORIZATION_ERRORS:
                missing = body.get("missing_capabilities") or list(capabilities)
                logger.info(
                    "User %s must authorize %s for %s",
                    account_id,
                    missing,
                    provider,
                    extra={
                        "provider": provider,
                        "account_id": str(account_id),
                        "missing_capabilities": missing,
                        "error": error,
                    },
                )
                raise NeedsAuthorization(
                    provider=provider,
                    capabilities=missing,
                    authorization_url=body.get("authorization_url"),
                    reason=error,
                    authorization_method=body.get("authorization_method", "POST"),
                )

            # No error field means the rejection is about our credentials, not
            # the user's grant.
            raise BrokerCredentialsRejected(
                f"token broker rejected our credentials ({response.status_code}) "
                f"- is the oauth-token-broker role assigned to this bot account?"
            )

        if response.status_code == 401:
            raise BrokerCredentialsRejected(
                "token broker rejected our API key (401) - check X-API-Key, and "
                "whether the token was revoked, expired, or hit its max_uses"
            )

        if response.status_code == 409:
            # The provider cannot refresh and the stored token expired. Apple
            # is the documented case. Only the user can resolve it, so there
            # is nothing to retry.
            raise TokenBrokerError(
                f"{provider} cannot refresh an expired token (409); the user "
                f"must sign in with {provider} again"
            )

        raise TokenBrokerError(
            f"unexpected token broker status {response.status_code}"
        )

    def grants(self, account_id: str) -> Dict[str, Any]:
        """
        Report which providers a user has connected, issuing no token.

        Use this to hide UI rather than fail into it.
        """
        try:
            response = self.session.get(
                f"{self.base_url}/oauth/grants",
                headers={"X-API-Key": self.api_key},
                params={"account_id": str(account_id)},
                timeout=self.timeout,
            )
        except requests.exceptions.RequestException as exc:
            raise ProviderDown(
                f"could not reach the token broker: {exc}"
            ) from exc

        if response.status_code == 200:
            return self._json(response)

        if response.status_code == 503:
            raise ProviderDown("oauth provider temporarily unavailable")

        if response.status_code in (401, 403):
            raise BrokerCredentialsRejected(
                f"token broker rejected our credentials ({response.status_code}) "
                f"- is the oauth-token-broker role assigned to this bot account?"
            )

        raise TokenBrokerError(
            f"unexpected token broker status {response.status_code}"
        )

    @staticmethod
    def _json(response: requests.Response) -> Dict[str, Any]:
        try:
            body = response.json()
        except ValueError as exc:
            raise TokenBrokerError(
                f"token broker returned a non-JSON body ({response.status_code})"
            ) from exc

        if not isinstance(body, dict):
            raise TokenBrokerError("token broker returned an unexpected JSON shape")

        return body

    @staticmethod
    def _json_or_empty(response: requests.Response) -> Dict[str, Any]:
        """Parse a body we only inspect for hints, tolerating junk."""
        try:
            body = response.json()
        except ValueError:
            return {}

        return body if isinstance(body, dict) else {}


def provider_token(
    provider: str,
    account_id: str,
    capabilities: List[str],
) -> BrokeredToken:
    """
    Fetch a provider access token using settings-based configuration.

    Convenience wrapper for call sites that do not need to hold a broker.
    """
    return TokenBroker().token(provider, account_id, capabilities)
