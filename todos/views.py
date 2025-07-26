from rest_framework.views import APIView, Response, status

from keep_up.verisafe_jwt_authentication import VerisafeJWTAuthentication

# Create your views here.


class PingAPIView(APIView):
    authentication_classes = [VerisafeJWTAuthentication]
    def get(self, request, *args, **kwargs):
        """
        An endpoint that checks the heartbeat of the program
        """
        return Response(data={"message": "He is risen"}, status=status.HTTP_200_OK)
