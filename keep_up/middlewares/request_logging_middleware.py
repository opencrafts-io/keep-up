import logging
import time
from django.utils.deprecation import MiddlewareMixin


class RequestLoggingMiddleware(MiddlewareMixin):
    """Middleware to log all HTTP requests in structured format"""

    def process_request(self, request):
        request._start_time = time.time_ns()

    def process_response(self, request, response):
        # Calculate duration
        duration_ns = time.time_ns() - getattr(request, "_start_time", time.time_ns())

        # Get remote address
        x_forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
        if x_forwarded_for:
            remote_addr = x_forwarded_for.split(",")[0].strip()
        else:
            remote_addr = request.META.get("REMOTE_ADDR", "")

        # Add port if available
        remote_port = request.META.get("REMOTE_PORT", "")
        if remote_port:
            remote_addr = f"{remote_addr}:{remote_port}"

        # Log the request
        logger = logging.getLogger("django.request")
        logger.info(
            "Request handled",
            extra={
                "method": request.method,
                "path": request.path,
                "host": request.get_host(),
                "duration_ns": duration_ns,
                "status": response.status_code,
                "remote_addr": remote_addr,
            },
        )

        return response
