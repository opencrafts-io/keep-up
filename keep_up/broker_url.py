"""
Assembling the AMQP URL Celery connects with.

Interpolating credentials into a URL by hand is not safe. A '#', '?' or '/'
in a password terminates the netloc, so the port ends up parsed from whatever
trailed the last colon and the connection fails with

    ValueError: Port could not be cast to integer value as '<password chunk>'

which points nowhere near the actual cause. '@' and ':' happen to survive
naive interpolation, which is what makes this look like it works right up
until a rotated password contains one of the other three.

The pika consumers in event_bus pass credentials as separate parameters and
were never exposed to this.
"""

from typing import Optional
from urllib.parse import quote


def amqp_url(
    user: Optional[str],
    password: Optional[str],
    host: Optional[str],
    port: Optional[str],
    vhost: Optional[str],
) -> str:
    """
    Build an amqp:// URL with every credential part percent-encoded.

    The vhost is encoded too, so the conventional '/' default arrives as
    '%2F' and still resolves to '/'.
    """
    return (
        f"amqp://{quote(user or '', safe='')}:{quote(password or '', safe='')}"
        f"@{host or ''}:{port or ''}/{quote(vhost or '', safe='')}"
    )
