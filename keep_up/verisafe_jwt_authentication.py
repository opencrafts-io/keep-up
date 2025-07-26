from rest_framework import status
from rest_framework.authentication import BaseAuthentication
from django.contrib.auth.models import AnonymousUser
from rest_framework.exceptions import AuthenticationFailed
from .verisafe_jwt import verify_verisafe_jwt  # from earlier


class VerisafeJWTAuthentication(BaseAuthentication):
    def authenticate(self, request):
        print("Authenticating")
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            raise AuthenticationFailed(
                "Wrong token format. Expected 'Bearer token'", status.HTTP_403_FORBIDDEN
            )
        token = auth_header.split(" ")[1]

        try:
            payload = verify_verisafe_jwt(token)
            request.verisafe_claims = payload
            # You can return a dummy user or create a real user model if needed
            return (AnonymousUser(), None)
        except Exception as e:
            print(e)
            raise AuthenticationFailed(str(e))
