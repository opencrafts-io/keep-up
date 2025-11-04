from rest_framework.generics import RetrieveAPIView
from rest_framework.views import Response, status


class PingAPIView(RetrieveAPIView):
    def get(self, request, *args, **kwargs):
        """
        An endpoint that checks the heartbeat of the program
        """
        return Response(
            data={"message": "He is risen."},
            status=status.HTTP_200_OK,
        )
