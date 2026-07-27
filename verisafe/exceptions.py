"""
Exceptions raised by the Verisafe token broker client.

Callers branch on two of these: NeedsAuthorization means the user must grant
access before a retry can ever succeed, and ProviderDown means the same call
is worth trying again shortly.
"""

from typing import List, Optional


class TokenBrokerError(Exception):
    """Base for every token broker failure."""


class NeedsAuthorization(TokenBrokerError):
    """
    The user has not granted the capabilities we asked for.

    Retrying without user action will not help. Relay `authorization_url` to
    the client so it can prompt the user; only the user's own JWT can start
    the authorization flow, which is why this service cannot start it.
    """

    def __init__(
        self,
        provider: str,
        capabilities: List[str],
        authorization_url: Optional[str],
        reason: str,
    ) -> None:
        self.provider = provider
        self.capabilities = capabilities
        self.authorization_url = authorization_url
        self.reason = reason
        super().__init__(
            f"user must authorize {capabilities} at {provider} ({reason})"
        )


class ProviderDown(TokenBrokerError):
    """
    Verisafe or the upstream provider is temporarily unavailable.

    The grant is untouched, so nothing should be disconnected and the user
    should not be prompted. Retry with backoff.
    """


class BrokerCredentialsRejected(TokenBrokerError):
    """
    Verisafe rejected our service credentials rather than the user's grant.

    Either the API key is wrong, or the bot account behind it is missing the
    `oauth-token-broker` role.
    """
