from django.urls import path
from .views import UserManagementView


urlpatterns = [
    path("", UserManagementView.as_view(), name="verisafe-user-management"),
]
