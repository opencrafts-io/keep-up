from django.urls import path

from agenda.views import (
    CreateEventApiView,
    DeleteEventApiView,
    ListEventsApiView,
    PingAPIView,
    UpdateEventApiView,
)

urlpatterns = [
    path("ping", PingAPIView.as_view(), name="ping"),
    path("add", CreateEventApiView.as_view(), name="create-event"),
    path("", ListEventsApiView.as_view(), name="list-events"),
    path("update/<str:event_id>", UpdateEventApiView.as_view(), name="update-event"),
    path("delete/<str:event_id>", DeleteEventApiView.as_view(), name="delete-event"),
]
