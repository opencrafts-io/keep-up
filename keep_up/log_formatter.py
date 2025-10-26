from json_log_formatter import JSONFormatter


class StandardJSONLogFormatter(JSONFormatter):
    def json_record(self, message, extra, record):
        request = extra.pop("request", None)
        if request:
            # Add any other parameter
            extra["IP_ADDRESS"] = request.META.get(
                "HTTP_X_FORWARDED_FOR"
            )  # or other ways to get ip
        additional_info = {
            "name": record.name,
            "level": record.levelname,
            "file": record.filename,
            "exc_info": record.exc_info,
            "thread": record.thread,
        }
        extra = {**extra, **additional_info}
        return super().json_record(message, extra, record)
