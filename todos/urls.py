from django.urls import path

from todos.views import (
    CompleteTodoApiView,
    CreateTodoApiView,
    DeleteTaskAPIView,
    UpdateTodoApiView,
    ListTodoApiView,
    SyncTasksApiView,
)

app_name = "todos"

urlpatterns = [
    path("add", CreateTodoApiView.as_view(), name="create"),
    path("", ListTodoApiView.as_view(), name="list"),
    path("sync", SyncTasksApiView.as_view(), name="sync"),
    path("update/<str:task_id>", UpdateTodoApiView.as_view(), name="update"),
    path("complete/<str:task_id>", CompleteTodoApiView.as_view(), name="complete"),
    path("delete/<str:task_id>", DeleteTaskAPIView.as_view(), name="delete"),
]
