from django.urls import path

from todos.views import (
    CompleteTaskView,
    ConvertToSubtaskView,
    CreateTaskView,
    DeleteTaskListView,
    DeleteTaskView,
    ListTasksView,
    MoveTaskToListView,
    PromoteSubtaskView,
    ReopenTaskView,
    RetrieveTaskView,
    TagDetailView,
    TagListCreateView,
    UpdateTaskListView,
    UpdateTaskView,
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
    # Tags..
    path("tags/", TagListCreateView.as_view(), name="tag-list-create"),
    path("tags/<uuid:tag_id>/", TagDetailView.as_view(), name="tag-detail"),
    # Todos
    path("add", CreateTaskView.as_view(), name="task-create"),
    path("", ListTasksView.as_view(), name="task-list"),
    path("<uuid:task_id>/", RetrieveTaskView.as_view(), name="task-retrieve"),
    path("<uuid:task_id>/update", UpdateTaskView.as_view(), name="task-update"),
    path("<uuid:task_id>/delete", DeleteTaskView.as_view(), name="task-delete"),
    path("<uuid:task_id>/complete", CompleteTaskView.as_view(), name="task-complete"),
    path("<uuid:task_id>/reopen", ReopenTaskView.as_view(), name="task-reopen"),
    path("<uuid:task_id>/move", MoveTaskToListView.as_view(), name="task-move"),
    path(
        "<uuid:task_id>/convert-to-subtask",
        ConvertToSubtaskView.as_view(),
        name="task-convert-to-subtask",
    ),
    path("<uuid:task_id>/promote", PromoteSubtaskView.as_view(), name="task-promote"),
]
