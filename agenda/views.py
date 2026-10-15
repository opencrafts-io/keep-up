import logging

from rest_framework import status
from rest_framework.generics import ListAPIView
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response
from rest_framework.views import APIView

from agenda.models import Event
from agenda.serializers import EventSerializer
from agenda.services import AgendaEventService
from keep_up.verisafe_jwt_authentication import VerisafeJWTAuthentication

logger = logging.getLogger("keep_up")


class CustomEventPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = "page_size"
    max_page_size = 100


class BaseEventView(APIView):
    authentication_classes = [VerisafeJWTAuthentication]

    @staticmethod
    def user_id(request):
        return getattr(request, "user_id", None)

    @staticmethod
    def missing_user_response():
        return Response(
            data={"message": "User ID not found in token."},
            status=status.HTTP_403_FORBIDDEN,
        )


class CreateEventApiView(BaseEventView):
    """Create an agenda event locally and queue its Google Tasks mirror."""

    def post(self, request, *args, **kwargs):
        owner_id = self.user_id(request)
        if not owner_id:
            return self.missing_user_response()

        serializer = EventSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        try:
            event = AgendaEventService.create_event(
                owner_id=owner_id, **serializer.validated_data
            )
        except ValueError as exc:
            return Response(
                {"message": str(exc)}, status=status.HTTP_400_BAD_REQUEST
            )
        except Exception:
            logger.exception("Unexpected error creating agenda event")
            return Response(
                {"message": "An internal error occurred."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        return Response(EventSerializer(event).data, status=status.HTTP_201_CREATED)


class ListEventsApiView(ListAPIView):
    """List the authenticated user's local events."""

    authentication_classes = [VerisafeJWTAuthentication]
    serializer_class = EventSerializer
    pagination_class = CustomEventPagination

    def get_queryset(self):
        owner_id = getattr(self.request, "user_id", None)
        if not owner_id:
            return Event.objects.none()
        if self.request.query_params.get("sync", "false").lower() == "true":
            AgendaEventService.sync_existing_events(owner_id)
        return AgendaEventService.list_events(
            owner_id=owner_id,
            start_date=self.request.query_params.get("start_date"),
            end_date=self.request.query_params.get("end_date"),
        )


class UpdateEventApiView(BaseEventView):
    """Update a local event and queue changes for its Tasks mirror."""

    def put(self, request, *args, **kwargs):
        return self._update(request, partial=True, **kwargs)

    def patch(self, request, *args, **kwargs):
        return self._update(request, partial=True, **kwargs)

    def _update(self, request, partial, event_id):
        owner_id = self.user_id(request)
        if not owner_id:
            return self.missing_user_response()

        try:
            event = AgendaEventService.get_event(owner_id, event_id)
        except Event.DoesNotExist:
            return Response(
                {"message": "Event not found."}, status=status.HTTP_404_NOT_FOUND
            )

        serializer = EventSerializer(event, data=request.data, partial=partial)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        try:
            event = AgendaEventService.update_event(
                owner_id, event_id, **serializer.validated_data
            )
        except Event.DoesNotExist:
            return Response(
                {"message": "Event not found."}, status=status.HTTP_404_NOT_FOUND
            )
        except ValueError as exc:
            return Response(
                {"message": str(exc)}, status=status.HTTP_400_BAD_REQUEST
            )
        except Exception:
            logger.exception("Unexpected error updating agenda event")
            return Response(
                {"message": "An internal error occurred."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        return Response(EventSerializer(event).data, status=status.HTTP_200_OK)


class DeleteEventApiView(BaseEventView):
    """Soft-delete a local event and queue deletion of its Tasks mirror."""

    def delete(self, request, *args, **kwargs):
        owner_id = self.user_id(request)
        if not owner_id:
            return self.missing_user_response()

        try:
            AgendaEventService.delete_event(owner_id, kwargs.get("event_id"))
        except Event.DoesNotExist:
            return Response(
                {"message": "Event not found."}, status=status.HTTP_404_NOT_FOUND
            )
        except Exception:
            logger.exception("Unexpected error deleting agenda event")
            return Response(
                {"message": "An internal error occurred."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        return Response(
            {"message": "Event deleted successfully."},
            status=status.HTTP_200_OK,
        )
