from django.urls import path

from todos.views import CreateTodoApiView, PingAPIView

urlpatterns = [
    path("ping", PingAPIView.as_view(), name="ping"),
    path("add", CreateTodoApiView.as_view(), name="create-todo"),
]
