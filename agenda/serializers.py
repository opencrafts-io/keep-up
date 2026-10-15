from rest_framework import serializers
from .models import Event


class EventSerializer(serializers.ModelSerializer):
    sync_status = serializers.SerializerMethodField()

    class Meta:
        model = Event
        fields = [
            "id",
            "summary",
            "description",
            "location",
            "start_time",
            "end_time",
            "all_day",
            "timezone",
            "status",
            "transparency",
            "calendar_id",
            "html_link",
            "created",
            "updated",
            "etag",
            "attendees",
            "reminders",
            "recurrence",
            "owner_id",
            "deleted",
            "sync_status",
        ]
        read_only_fields = [
            "id",
            "calendar_id",
            "html_link",
            "created",
            "updated",
            "etag",
            "owner_id",
            "deleted",
            "sync_status",
        ]

    def validate_summary(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError("Summary cannot be empty.")
        return value

    def validate(self, attrs):
        start_time = attrs.get(
            "start_time", self.instance.start_time if self.instance else None
        )
        end_time = attrs.get(
            "end_time", self.instance.end_time if self.instance else None
        )
        if start_time and end_time and end_time <= start_time:
            raise serializers.ValidationError(
                {"end_time": "End time must be after start time."}
            )
        return attrs

    def get_sync_status(self, event):
        return event.task.sync_status if event.task_id else None
