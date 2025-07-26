from django.urls import path

from todos.views import PingAPIView

urlpatterns = [path("ping", PingAPIView.as_view(), name="ping")]
