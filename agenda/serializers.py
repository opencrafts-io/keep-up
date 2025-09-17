from rest_framework import serializers
from .models import Event
from django.utils import timezone


class EventSerializer(serializers.ModelSerializer):
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
        ]

    def create(self, validated_data):
        """
        Override the create method to customize how the Event is saved
        after receiving the data from the Google Calendar API.
        """
        event = Event.objects.create(**validated_data)
        return event
