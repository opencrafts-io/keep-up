from django.urls import path

from todos.views import (
    CreateTodoApiView,
    DeleteTaskAPIView,
    PingAPIView,
    UpdateTodoApiView,
)

urlpatterns = [
    path("ping", PingAPIView.as_view(), name="ping"),
    path("add", CreateTodoApiView.as_view(), name="create-todo"),
    path("update/<str:task_id>", UpdateTodoApiView.as_view(), name="update-todo"),
    path("delete/<str:task_id>", DeleteTaskAPIView.as_view(), name="delete-todo"),
]
