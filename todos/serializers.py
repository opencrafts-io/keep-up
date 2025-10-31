from rest_framework import serializers
from .models import Task


class TaskSerializer(serializers.ModelSerializer):
    class Meta:
        model = Task
        fields = [
            "id",
            "external_id",
            "kind",
            "etag",
            "title",
            "updated",
            "self_link",
            "parent",
            "position",
            "notes",
            "status",
            "due",
            "completed",
            "deleted",
            "hidden",
            "web_view_link",
            "owner_id",
        ]
        read_only_fields = ["id", "updated", "completed", "web_view_link", "self_link"]

    def create(self, validated_data):
        """
        Override the create method to customize how the Task is saved
        after receiving the data from the Google Tasks API.
        """
        task = Task.objects.create(**validated_data)
        return task
