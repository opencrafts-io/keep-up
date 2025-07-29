from django.urls import path

from todos.views import CreateTodoApiView, DeleteTaskAPIView, PingAPIView

urlpatterns = [
    path("ping", PingAPIView.as_view(), name="ping"),
    path("add", CreateTodoApiView.as_view(), name="create-todo"),
    path("delete/<str:task_id>", DeleteTaskAPIView.as_view(), name="create-todo"),
]
