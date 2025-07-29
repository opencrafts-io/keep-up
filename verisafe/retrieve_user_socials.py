import os
import uuid
from typing import Any, List, Union

import requests


def retrieve_user_social_accounts(user_id: str) -> Union[List[dict[str, Any]], str]:
    try:
        uuid.UUID(user_id)
    except ValueError:
        return f"Invalid user id format. Please provide a valid UUID"

    url = f"{os.getenv('VERISAFE_BASE_URL')}/socials/user/{user_id}"

    try:
        response = requests.get(url)
        if response.status_code == 200:
            return response.json()
    except requests.exceptions.RequestException as e:
        return f"Request failed {str(e)}"

    return "Something went terribly wrong and we couldn't satisfy your request at the moment. Please try again"
