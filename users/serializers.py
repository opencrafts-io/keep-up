from rest_framework.serializers import ModelSerializer

from users.models import User


class UserSerializer(ModelSerializer):
    class Meta:
        model = User
        fields = [
            "user_id",
            "name",
            "email",
            "phone",
            "username",
            "avatar_url",
            "vibe_points",
            "created_at",
            "updated_at",
        ]
