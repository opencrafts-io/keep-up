import jwt
from jwt import ExpiredSignatureError, InvalidTokenError
import os
import logging

# Load secret from environment or fallback default (never commit the default to code)
VERISAFE_API_SECRET = os.getenv(
    "VERISAFE_API_SECRET", "super-secret-token"
)  # Replace with your actual secret or env

VERISAFE_ISSUER = "https://verisafe.opencrafts.io/"
VERISAFE_AUDIENCE = "https://academia.opencrafts.io/"

logger = logging.getLogger("django")

def verify_verisafe_jwt(token: str):
    """
    Verifies and decodes a JWT issued by Verisafe using HS256.

    Returns:
        dict: Decoded token claims
    Raises:
        Exception: If the token is invalid or expired
    """
    try:
        payload = jwt.decode(
            token,
            VERISAFE_API_SECRET,
            algorithms=["HS256"],
            audience=VERISAFE_AUDIENCE,
            issuer=VERISAFE_ISSUER,
        )
        return payload
    except ExpiredSignatureError as e:
        logger.error("Error while validating user token", extra={"error": str(e)})
        raise Exception("Token has expired")
    except InvalidTokenError as e:
        logger.error("Error while validating user token", extra={"error": str(e)})
        raise Exception(f"Invalid token: {str(e)}")
