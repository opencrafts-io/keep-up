import logging
from typing import Optional

from drf_spectacular.utils import OpenApiExample, OpenApiResponse, extend_schema
from rest_framework.views import APIView, Response, status

from keep_up.verisafe_jwt_authentication import VerisafeJWTAuthentication
from verisafe.exceptions import (
    BrokerCredentialsRejected,
    NeedsAuthorization,
    ProviderDown,
    TokenBrokerError,
)
from verisafe.token_broker import (
    CAPABILITY_CALENDAR,
    CAPABILITY_TASKS,
    PROVIDER_GOOGLE,
    TokenBroker,
)

logger = logging.getLogger("keep_up")

# What this service needs to sync a user's tasks and calendar.
REQUIRED_CAPABILITIES = [CAPABILITY_CALENDAR, CAPABILITY_TASKS]


class PingAPIView(APIView):
    def get(self, request, *args, **kwargs):
        """
        An endpoint that checks the heartbeat of the program
        """
        return Response(
            data={"message": "He is risen."},
            status=status.HTTP_200_OK,
        )


class GoogleIntegrationStatusView(APIView):
    """
    Reports whether the caller has granted Google calendar and tasks access.

    Not having granted them is a normal answer rather than an error, so the
    check returns 200 either way and the client branches on `granted`.

    When something is missing, `authorization` carries Verisafe's instruction
    verbatim. The client makes that call itself, adding its own `platform`
    and `redirect_uri` and its own bearer token: starting an OAuth consent
    flow requires the user's token, which is deliberately not something this
    service holds.
    """

    authentication_classes = [VerisafeJWTAuthentication]

    @extend_schema(
        tags=["Integrations"],
        summary="Check Google calendar and tasks authorization",
        description=(
            "Reports whether the authenticated user has granted the Google "
            "calendar and tasks capabilities. Returns 200 whether or not they "
            "have. When capabilities are missing, the response carries the "
            "authorization call the client should make next. When they are all "
            "present, a Google Tasks pull is queued so the user's tasks appear "
            "without waiting for the next scheduled sync."
        ),
        request=None,
        responses={
            200: OpenApiResponse(
                description="Authorization status for the caller.",
                examples=[
                    OpenApiExample(
                        "Everything granted",
                        value={
                            "provider": "google",
                            "granted": True,
                            "required_capabilities": ["calendar", "tasks"],
                            "granted_capabilities": ["identity", "calendar", "tasks"],
                            "missing_capabilities": [],
                            "authorization": None,
                            "sync_queued": True,
                        },
                    ),
                    OpenApiExample(
                        "Tasks not granted yet",
                        value={
                            "provider": "google",
                            "granted": False,
                            "required_capabilities": ["calendar", "tasks"],
                            "granted_capabilities": ["identity", "calendar"],
                            "missing_capabilities": ["tasks"],
                            "authorization": {
                                "url": "https://verisafe.opencrafts.io/oauth/google/authorize",
                                "method": "POST",
                                "capabilities": ["tasks"],
                            },
                            "sync_queued": False,
                        },
                    ),
                ],
            ),
            403: OpenApiResponse(description="Missing or invalid bearer token."),
            502: OpenApiResponse(description="This service cannot talk to Verisafe."),
            503: OpenApiResponse(description="Verisafe or Google is unavailable."),
        },
    )
    def post(self, request, *args, **kwargs):
        account_id = getattr(request, "user_id", None)
        if not account_id:
            logger.error("Failed to extract user_id from JWT claims")
            return Response(
                data={
                    "message": "We couldn't extract your user id from the provided "
                    "token. Please ensure the token is valid and contains the "
                    "necessary user data."
                },
                status=status.HTTP_403_FORBIDDEN,
            )

        try:
            granted = self._granted_capabilities(account_id)
        except ProviderDown as exc:
            logger.warning("Verisafe unavailable while checking grants: %s", exc)
            return Response(
                data={"message": "Google authorization is temporarily unavailable."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        except BrokerCredentialsRejected as exc:
            # Our service token or bot role, not the user's grant. Surfacing
            # this as a permission problem would send the user to re-authorize
            # something that was never their fault.
            logger.error("Verisafe rejected this service's credentials: %s", exc)
            return Response(
                data={"message": "This service is not configured to reach Verisafe."},
                status=status.HTTP_502_BAD_GATEWAY,
            )
        except TokenBrokerError as exc:
            logger.error("Unexpected token broker failure: %s", exc)
            return Response(
                data={"message": "Could not check your Google authorization."},
                status=status.HTTP_502_BAD_GATEWAY,
            )

        missing = [cap for cap in REQUIRED_CAPABILITIES if cap not in granted]

        if missing:
            return Response(
                data=self._payload(granted, missing, self._authorization(account_id, missing)),
                status=status.HTTP_200_OK,
            )

        self._queue_pull(account_id)
        return Response(
            data=self._payload(granted, [], None, sync_queued=True),
            status=status.HTTP_200_OK,
        )

    @staticmethod
    def _granted_capabilities(account_id) -> list:
        """
        Read the user's grants without issuing a token.

        The pre-flight endpoint exists for exactly this: answering whether the
        user has granted something, without minting a Google credential to
        find out.
        """
        response = TokenBroker().grants(account_id)

        for grant in response.get("grants") or []:
            if grant.get("provider") != PROVIDER_GOOGLE:
                continue
            if grant.get("revoked"):
                # Revoked at the provider: whatever it once covered, it
                # covers nothing now.
                return []
            return list(grant.get("granted_capabilities") or [])

        return []

    @staticmethod
    def _authorization(account_id, missing: list) -> Optional[dict]:
        """
        Ask the broker for the authorization instruction to relay.

        This is a token request that is expected to be refused: a refusal is
        what carries the authoritative url and method, and no token is minted
        along the way.
        """
        try:
            TokenBroker().token(PROVIDER_GOOGLE, account_id, missing)
        except NeedsAuthorization as exc:
            return {
                "url": exc.authorization_url,
                "method": exc.authorization_method,
                "capabilities": exc.capabilities or missing,
            }
        except TokenBrokerError as exc:
            # The grant check already told us what is missing; losing the
            # instruction is worth reporting but not worth failing over.
            logger.warning("Could not fetch the authorization instruction: %s", exc)
            return None

        # The grant appeared between the two calls. Rare, and the next check
        # will report it as granted.
        logger.info("Capabilities %s became available mid-check", missing)
        return None

    @staticmethod
    def _queue_pull(account_id) -> None:
        """Bring their Google tasks in now rather than at the next beat."""
        from todos.tasks import pull_from_google

        pull_from_google.delay(str(account_id))

    @staticmethod
    def _payload(granted: list, missing: list, authorization, sync_queued=False):
        return {
            "provider": PROVIDER_GOOGLE,
            "granted": not missing,
            "required_capabilities": REQUIRED_CAPABILITIES,
            "granted_capabilities": granted,
            "missing_capabilities": missing,
            "authorization": authorization,
            "sync_queued": sync_queued,
        }
