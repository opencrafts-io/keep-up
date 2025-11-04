from rest_framework.generics import ListCreateAPIView

from users.models import User
from users.serializers import UserSerializer


class UserManagementView(ListCreateAPIView):
    """Creates a user"""

    serializer_class = UserSerializer
    queryset = User.objects.all()
