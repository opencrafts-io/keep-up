from django.urls import path

from todos.views import (
    CompleteTodoApiView,
    CreateTodoApiView,
    DeleteTaskAPIView,
    DeleteTaskListView,
    UpdateTaskListView,
    UpdateTodoApiView,
    ListTodoApiView,
    SyncTasksApiView,
    CreateTaskListView,
    RetrieveTaskLists,
    RetrieveOrCreateUserDefaultTaskList,
    RetrieveTaskListByIDView,
)

app_name = "todos"

urlpatterns = [
    # Task lists
    path("tasklist/create/", CreateTaskListView.as_view(), name="tasklist-create"),
    path("tasklist/", RetrieveTaskLists.as_view(), name="tasklist-retrieve"),
    path(
        "tasklist/default/",
        RetrieveOrCreateUserDefaultTaskList.as_view(),
        name="tasklist-default",
    ),
    path(
        "tasklist/<uuid:list_id>",
        RetrieveTaskListByIDView.as_view(),
        name="tasklist-detail",
    ),
    path(
        "tasklist/<uuid:list_id>/update/",
        UpdateTaskListView.as_view(),
        name="tasklist-update",
    ),
    path(
        "tasklist/<uuid:list_id>/delete/",
        DeleteTaskListView.as_view(),
        name="tasklist-delete",
    ),
    # Todos
    path("add", CreateTodoApiView.as_view(), name="create"),
    path("", ListTodoApiView.as_view(), name="list"),
    path("sync", SyncTasksApiView.as_view(), name="sync"),
    path("update/<str:task_id>", UpdateTodoApiView.as_view(), name="update"),
    path("complete/<str:task_id>", CompleteTodoApiView.as_view(), name="complete"),
    path("delete/<str:task_id>", DeleteTaskAPIView.as_view(), name="delete"),
]
