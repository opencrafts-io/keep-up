from rest_framework.views import APIView, Response, status

# Create your views here.


class PingAPIView(APIView):
    def get(self, request, *args, **kwargs):
        """
        An endpoint that checks the heartbeat of the program
        """
        return Response(data={"message": "He is risen"}, status=status.HTTP_200_OK)
