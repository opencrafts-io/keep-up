import logging
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

def parse_date_time_to_iso_format(raw_date: Optional[str]) -> Optional[str]:
    """
    Parses a raw date/time string (assumed to be ISO 8601) and formats it
    into an RFC 3339 compliant string (YYYY-MM-DDTHH:MM:SS.sssZ) in UTC.

    If raw_date is None, it returns the current UTC date and time in the
    specified RFC 3339 format.

    Args:
        raw_date (str | None): The input date/time string, or None.

    Returns:
        str | None: The formatted date/time string in RFC 3339 format if successful,
                    or None if the input is not None but cannot be parsed.
    """
    if raw_date is None:
        # If the input is None, return the current UTC date and time
        now_utc = datetime.now(timezone.utc)
        formatted_now = now_utc.strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z'
        logger.debug(f"Input date was None, defaulting to current UTC time: {formatted_now}")
        return formatted_now

    try:
        # 1. Parse the raw input string into a datetime object
        dt_obj = datetime.fromisoformat(raw_date)

        # 2. Ensure the datetime object is timezone-aware and in UTC
        if dt_obj.tzinfo is None:
            # If the input string is naive (no timezone info), assume it's UTC.
            dt_obj = dt_obj.replace(tzinfo=timezone.utc)
        else:
            # If it's timezone-aware but not UTC, convert it to UTC.
            dt_obj = dt_obj.astimezone(timezone.utc)

        # 3. Format as RFC 3339 timestamp: YYYY-MM-DDTHH:MM:SS.sssZ
        # strftime ensures exactly 3 digits for milliseconds and 'Z' for UTC.
        # '%f' gives microseconds, [:-3] truncates to milliseconds.
        formatted_date = dt_obj.strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z'
        return formatted_date

    except ValueError:
        logger.warning(
            f"Invalid date format provided to parse_date_time_to_iso_format: '{raw_date}'. "
            "Returning None as it could not be parsed."
        )
        # If the raw_date was provided but invalid, return None to signal an error
        return None
    except Exception as e:
        logger.error(f"An unexpected error occurred during date parsing: {e}", exc_info=True)
        return None
