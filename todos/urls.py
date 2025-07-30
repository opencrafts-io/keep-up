from django.urls import path

from todos.views import (
    CompleteTodoApiView,
    CreateTodoApiView,
    DeleteTaskAPIView,
    PingAPIView,
    UpdateTodoApiView,
    ListTodoApiView,
)

urlpatterns = [
    path("ping", PingAPIView.as_view(), name="ping"),
    path("add", CreateTodoApiView.as_view(), name="create-todo"),
    path("", ListTodoApiView.as_view(), name="retrieve-todos"),
    path("update/<str:task_id>", UpdateTodoApiView.as_view(), name="update-todo"),
    path("complete/<str:task_id>", CompleteTodoApiView.as_view(), name="complete-todo"),
    path("delete/<str:task_id>", DeleteTaskAPIView.as_view(), name="delete-todo"),
]
