import uuid
from typing import Any, List, Union

import requests
from django.conf import settings


def retrieve_user_social_accounts(user_id: str) -> Union[List[dict[str, Any]], str]:
    try:
        uuid.UUID(user_id)
    except ValueError:
        return f"Invalid user id format. Please provide a valid UUID"

    url = f"{settings.VERISAFE_BASE_URL}/socials/user/{user_id}"

    try:
        response = requests.get(
            url,
            headers={"x-api-key": settings.VERISAFE_API_KEY},
            timeout=settings.VERISAFE_TIMEOUT,
        )
        response.raise_for_status()
        if response.status_code == 200:
            return response.json()

    except requests.exceptions.RequestException as e:
        return f"Request failed {str(e)}"

    return "Something went terribly wrong and we couldn't satisfy your request at the moment. Please try again"
