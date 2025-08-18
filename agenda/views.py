import logging
import os
import uuid
from datetime import timezone, datetime, timedelta
from googleapiclient.http import HttpError
from pythonjsonlogger.json import JsonFormatter
from rest_framework.views import APIView, Response, status
from rest_framework.generics import (
    CreateAPIView,
    DestroyAPIView,
    ListAPIView,
    RetrieveAPIView,
    UpdateAPIView,
    get_object_or_404,
)
from rest_framework.pagination import PageNumberPagination
from keep_up.verisafe_jwt_authentication import VerisafeJWTAuthentication
from agenda.models import Event
from utils.parse_date_time_to_iso_format import parse_date_time_to_iso_format
from verisafe.retrieve_user_socials import retrieve_user_social_accounts
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from .serializers import EventSerializer

# Create your views here.
logger = logging.getLogger()
logger.setLevel(logging.ERROR)

handler = logging.StreamHandler()
handler.setFormatter(JsonFormatter())
logger.addHandler(handler)


class CustomEventPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = "page_size"
    max_page_size = 100


class PingAPIView(RetrieveAPIView):
    def get(self, request, *args, **kwargs):
        """
        An endpoint that checks the heartbeat of the program
        """
        return Response(
            data={"message": "Agenda API is running."}, status=status.HTTP_200_OK
        )

    def post(self, request, *args, **kwargs):
        """
        Test endpoint to debug request data format
        """
        return Response(
            data={
                "message": "Test endpoint - received data",
                "data": request.data,
                "headers": dict(request.headers),
                "user_id": getattr(request, "user_id", None),
            },
            status=status.HTTP_200_OK,
        )


class CreateEventApiView(APIView):
    """
    Creates a local calendar event without Google Calendar integration (for testing)
    """

    authentication_classes = [VerisafeJWTAuthentication]

    def post(self, request, *args, **kwargs):
        user_id: str | None = getattr(request, "user_id", None)

        # Log the request data for debugging
        logger.info(f"Received event creation request: {request.data}")
        logger.info(f"User ID from token: {user_id}")

        if not user_id:
            logger.error(
                "Failed to extract user_id from JWT claims",
                extra={"user_id": user_id, "headers": dict(request.headers)},
            )
            return Response(
                data={
                    "message": "We couldn't extract your user id from the provided token."
                    "Please ensure the token is valid and contains the necessary user data."
                },
                status=status.HTTP_403_FORBIDDEN,
            )

        # Retrieve user socials
        socials = retrieve_user_social_accounts(user_id)

        if isinstance(socials, str):
            return Response(
                data={
                    "message": socials,
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        # get google social login
        google_social = None
        for social in socials:
            if social["provider"] == "google":
                google_social = social
                break

        if not google_social:
            logger.error(
                "No Google social account found for user.", extra={"user_id": user_id}
            )
            return Response(
                data={
                    "message": "No Google social account linked to this user please consider linking your google account"
                },
                status=status.HTTP_404_NOT_FOUND,
            )

        creds = Credentials(
            token=google_social["access_token"],
            client_id=os.getenv("GOOGLE_CLIENT_ID"),
            client_secret=os.getenv("GOOGLE_CLIENT_SECRET"),
            refresh_token=google_social["refresh_token"],
            token_uri="https://oauth2.googleapis.com/token",
        )

        try:
            # Build the Google Calendar API service
            service = build("calendar", "v3", credentials=creds)

            # Get event data from request
            event_summary = request.data.get("summary")
            if not event_summary:
                return Response(
                    data={"message": "Event summary is required."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            # Parse start and end times
            start_time_str = request.data.get("start_time")
            end_time_str = request.data.get("end_time")

            if not start_time_str or not end_time_str:
                logger.error(
                    f"Missing start_time or end_time: start_time={start_time_str}, end_time={end_time_str}"
                )
                return Response(
                    data={"message": "Both start_time and end_time are required."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            # Log the parsed times for debugging
            logger.info(
                f"Parsing times: start_time={start_time_str}, end_time={end_time_str}"
            )

            # Create event body for Google Calendar API
            event_body = {
                "summary": event_summary,
                "description": request.data.get("description", ""),
                "location": request.data.get("location", ""),
                "start": {
                    "dateTime": parse_date_time_to_iso_format(start_time_str),
                    "timeZone": request.data.get("timezone", "UTC"),
                },
                "end": {
                    "dateTime": parse_date_time_to_iso_format(end_time_str),
                    "timeZone": request.data.get("timezone", "UTC"),
                },
                "transparency": request.data.get("transparency", "opaque"),
                "reminders": {
                    "useDefault": True,
                },
            }

            # Add attendees if provided - handle Flutter format
            attendees = request.data.get("attendees", [])
            if attendees:
                # Convert Flutter format to Google Calendar format
                if isinstance(attendees, dict):
                    # Flutter sends: {"attendee_0": {"email": "...", "displayName": "..."}}
                    attendees_list = []
                    for key, attendee in attendees.items():
                        if isinstance(attendee, dict) and "email" in attendee:
                            attendees_list.append(attendee)
                    event_body["attendees"] = attendees_list
                elif isinstance(attendees, list):
                    event_body["attendees"] = attendees

            # Add recurrence if provided - handle Flutter format
            recurrence = request.data.get("recurrence", [])
            if recurrence:
                # Convert Flutter format to Google Calendar format
                if isinstance(recurrence, dict):
                    # Flutter sends: {"rule": "RRULE:FREQ=WEEKLY"}
                    recurrence_list = []
                    for key, value in recurrence.items():
                        if isinstance(value, str) and value.startswith("RRULE:"):
                            recurrence_list.append(value)
                    event_body["recurrence"] = recurrence_list
                elif isinstance(recurrence, list):
                    event_body["recurrence"] = recurrence

            # Add reminders if provided - handle Flutter format
            reminders = request.data.get("reminders", {})
            if reminders and isinstance(reminders, dict):
                # Flutter sends: {"useDefault": false, "overrides": [{"method": "popup", "minutes": 60}]}
                if "useDefault" in reminders or "overrides" in reminders:
                    event_body["reminders"] = reminders

            # Create the event in Google Calendar FIRST
            calendar_id = request.data.get("calendar_id", "primary")
            logger.info(f"Creating event in Google Calendar with body: {event_body}")

            created_event = (
                service.events()
                .insert(calendarId=calendar_id, body=event_body)
                .execute()
            )

            logger.info(
                f"Successfully created event in Google Calendar: {created_event['id']}"
            )

            # NOW save the Google Calendar response to our database
            event_data = {
                "id": created_event["id"],  # Use Google Calendar's event ID
                "summary": created_event["summary"],
                "description": created_event.get("description", ""),
                "location": created_event.get("location", ""),
                "start_time": created_event["start"]["dateTime"],
                "end_time": created_event["end"]["dateTime"],
                "all_day": "date" in created_event["start"],
                "timezone": created_event["start"].get("timeZone", "UTC"),
                "status": created_event.get("status", "confirmed"),
                "transparency": created_event.get("transparency", "opaque"),
                "calendar_id": calendar_id,
                "html_link": created_event["htmlLink"],
                "created": created_event["created"],
                "updated": created_event["updated"],
                "etag": created_event["etag"],
                "attendees": created_event.get("attendees", []),
                "reminders": created_event.get("reminders", {}),
                "recurrence": created_event.get("recurrence", []),
                "owner_id": uuid.UUID(user_id),
            }

            # Use the EventSerializer to validate and save the event to the database
            serializer = EventSerializer(data=event_data)
            if serializer.is_valid():
                event_instance = serializer.save()
                logger.info(
                    f"Event successfully saved to database after Google Calendar creation: {event_instance.id}"
                )
                return Response(
                    data=serializer.data,
                    status=status.HTTP_201_CREATED,
                )
            else:
                logger.error(
                    f"Failed to save event to database after Google Calendar creation: {serializer.errors}"
                )
                # If we can't save to database, we should ideally delete from Google Calendar too
                try:
                    service.events().delete(
                        calendarId=calendar_id, eventId=created_event["id"]
                    ).execute()
                    logger.info(
                        f"Cleaned up Google Calendar event after database save failure: {created_event['id']}"
                    )
                except Exception as cleanup_error:
                    logger.error(
                        f"Failed to cleanup Google Calendar event after database save failure: {cleanup_error}"
                    )

                return Response(
                    data={
                        "message": "Event created in Google Calendar but failed to save in database.",
                        "error": serializer.errors,
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )
        except HttpError as e:
            logger.error(
                f"Google Calendar API error: {str(e)}",
                extra={
                    "user_id": user_id,
                    "request_data": request.data,
                    "status_code": e.resp.status,
                },
            )
            return Response(
                data={"message": f"Google Calendar API error: {str(e)}"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except Exception as e:
            logger.error(
                f"Unexpected error creating Google Calendar Event: {str(e)}",
                extra={"user_id": user_id, "request_data": request.data},
            )
            return Response(
                data={
                    "message": f"Unexpected error creating Google Calendar event: {str(e)}"
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class ListEventsApiView(ListAPIView):
    """
    Lists all events for a user, optionally syncing with Google Calendar
    """

    authentication_classes = [VerisafeJWTAuthentication]
    serializer_class = EventSerializer
    pagination_class = CustomEventPagination

    def get_queryset(self):
        user_id = getattr(self.request, "user_id", None)
        if not user_id:
            return Event.objects.none()

        # Get query parameters
        start_date = self.request.query_params.get("start_date")
        end_date = self.request.query_params.get("end_date")
        sync_with_google = (
            self.request.query_params.get("sync", "false").lower() == "true"
        )
        print(sync_with_google)

        queryset = Event.objects.filter(owner_id=user_id)

        # Filter by date range if provided
        if start_date:
            try:
                start_datetime = datetime.fromisoformat(start_date)
                queryset = queryset.filter(start_time__gte=start_datetime)
            except ValueError:
                pass

        if end_date:
            try:
                end_datetime = datetime.fromisoformat(end_date)
                queryset = queryset.filter(end_time__lte=end_datetime)
            except ValueError:
                pass

        # Sync with Google Calendar if requested
        if sync_with_google:
            self._sync_with_google_calendar(user_id)
            # Refresh queryset after sync
            queryset = Event.objects.filter(owner_id=user_id)

        return queryset.order_by("start_time")

    def _sync_with_google_calendar(self, user_id):
        """Sync local events with Google Calendar"""
        try:
            # Retrieve user socials and get Google credentials
            socials = retrieve_user_social_accounts(user_id)
            print(socials)
            if isinstance(socials, str):
                return

            google_social = None
            for social in socials:
                if social["provider"] == "google":
                    google_social = social
                    break

            if not google_social:
                return

            creds = Credentials(
                token=google_social["access_token"],
                client_id=os.getenv("GOOGLE_CLIENT_ID"),
                client_secret=os.getenv("GOOGLE_CLIENT_SECRET"),
                refresh_token=google_social["refresh_token"],
                token_uri="https://oauth2.googleapis.com/token",
            )

            service = build("calendar", "v3", credentials=creds)

            # Get events from the last 30 days to the next 30 days
            now = datetime.now(timezone.utc)
            time_min = (now - timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
            time_max = (now + timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ")

            logger.info(
                f"Syncing calendar for user {user_id} from {time_min} to {time_max}"
            )

            events_result = (
                service.events()
                .list(
                    calendarId="primary",
                    timeMin=time_min,
                    timeMax=time_max,
                    singleEvents=True,
                    orderBy="startTime",
                )
                .execute()
            )

            events = events_result.get("items", [])

            for event in events:
                # Check if event already exists in database
                existing_event = Event.objects.filter(
                    id=event["id"], owner_id=user_id
                ).first()

                if not existing_event:
                    # Create new event in database
                    event_data = {
                        "id": event["id"],
                        "summary": event.get("summary", "No Title"),
                        "description": event.get("description", ""),
                        "location": event.get("location", ""),
                        "start_time": (
                            event["start"]["dateTime"]
                            if "dateTime" in event["start"]
                            else event["start"]["date"]
                        ),
                        "end_time": (
                            event["end"]["dateTime"]
                            if "dateTime" in event["end"]
                            else event["end"]["date"]
                        ),
                        "all_day": "date" in event["start"],
                        "timezone": event["start"].get("timeZone", "UTC"),
                        "status": event.get("status", "confirmed"),
                        "transparency": event.get("transparency", "opaque"),
                        "calendar_id": "primary",
                        "html_link": event["htmlLink"],
                        "created": event["created"],
                        "updated": event["updated"],
                        "etag": event["etag"],
                        "attendees": event.get("attendees", []),
                        "reminders": event.get("reminders", {}),
                        "recurrence": event.get("recurrence", []),
                        "owner_id": uuid.UUID(user_id),
                    }

                    serializer = EventSerializer(data=event_data)
                    if serializer.is_valid():
                        serializer.save()

        except Exception as e:
            logger.error(
                f"Error syncing with Google Calendar: {str(e)}",
                extra={"user_id": user_id},
            )


class UpdateEventApiView(APIView):
    """
    Updates a calendar event specified by its ID.
    Syncs the update with Google Calendar.
    """

    authentication_classes = [VerisafeJWTAuthentication]

    def put(self, request, *args, **kwargs):
        event_id = kwargs.get("event_id")
        user_id = getattr(request, "user_id", None)

        if not user_id:
            return Response(
                data={"message": "User ID not found in token."},
                status=status.HTTP_403_FORBIDDEN,
            )

        # Get the event from database
        try:
            event = Event.objects.get(id=event_id, owner_id=user_id)
        except Event.DoesNotExist:
            return Response(
                data={"message": "Event not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        # Get Google credentials
        socials = retrieve_user_social_accounts(user_id)
        if isinstance(socials, str):
            return Response(
                data={"message": socials},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        google_social = None
        for social in socials:
            if social["provider"] == "google":
                google_social = social
                break

        if not google_social:
            return Response(
                data={"message": "No Google social account linked to this user"},
                status=status.HTTP_404_NOT_FOUND,
            )

        creds = Credentials(
            token=google_social["access_token"],
            client_id=os.getenv("GOOGLE_CLIENT_ID"),
            client_secret=os.getenv("GOOGLE_CLIENT_SECRET"),
            refresh_token=google_social["refresh_token"],
            token_uri="https://oauth2.googleapis.com/token",
        )

        try:
            service = build("calendar", "v3", credentials=creds)

            # Prepare event body for update
            event_body = {
                "summary": request.data.get("summary", event.summary),
                "description": request.data.get("description", event.description),
                "location": request.data.get("location", event.location),
                "start": {
                    "dateTime": parse_date_time_to_iso_format(
                        request.data.get("start_time")
                    )
                    or event.start_time.isoformat(),
                    "timeZone": request.data.get("timezone", event.timezone),
                },
                "end": {
                    "dateTime": parse_date_time_to_iso_format(
                        request.data.get("end_time")
                    )
                    or event.end_time.isoformat(),
                    "timeZone": request.data.get("timezone", event.timezone),
                },
                "transparency": request.data.get("transparency", event.transparency),
            }

            # Update event in Google Calendar
            updated_event = (
                service.events()
                .update(calendarId=event.calendar_id, eventId=event_id, body=event_body)
                .execute()
            )

            # Update local database
            event.summary = updated_event["summary"]
            event.description = updated_event.get("description", "")
            event.location = updated_event.get("location", "")
            event.start_time = updated_event["start"]["dateTime"]
            event.end_time = updated_event["end"]["dateTime"]
            event.transparency = updated_event.get("transparency", "opaque")
            event.updated = updated_event["updated"]
            event.etag = updated_event["etag"]
            event.save()

            serializer = EventSerializer(event)
            return Response(data=serializer.data, status=status.HTTP_200_OK)

        except Exception as e:
            logger.error(
                f"Error updating Google Calendar Event: {str(e)}",
                extra={"user_id": user_id},
            )
            return Response(
                data={"message": f"Error updating Google Calendar event: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class DeleteEventApiView(DestroyAPIView):
    """
    Deletes a calendar event specified by its ID.
    Syncs the deletion with Google Calendar.
    """

    authentication_classes = [VerisafeJWTAuthentication]

    def delete(self, request, *args, **kwargs):
        event_id = kwargs.get("event_id")
        user_id = getattr(request, "user_id", None)

        if not user_id:
            return Response(
                data={"message": "User ID not found in token."},
                status=status.HTTP_403_FORBIDDEN,
            )

        # Get the event from database
        try:
            event = Event.objects.get(id=event_id, owner_id=user_id)
        except Event.DoesNotExist:
            return Response(
                data={"message": "Event not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        # Get Google credentials
        socials = retrieve_user_social_accounts(user_id)
        if isinstance(socials, str):
            return Response(
                data={"message": socials},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        google_social = None
        for social in socials:
            if social["provider"] == "google":
                google_social = social
                break

        if not google_social:
            return Response(
                data={"message": "No Google social account linked to this user"},
                status=status.HTTP_404_NOT_FOUND,
            )

        creds = Credentials(
            token=google_social["access_token"],
            client_id=os.getenv("GOOGLE_CLIENT_ID"),
            client_secret=os.getenv("GOOGLE_CLIENT_SECRET"),
            refresh_token=google_social["refresh_token"],
            token_uri="https://oauth2.googleapis.com/token",
        )

        try:
            service = build("calendar", "v3", credentials=creds)

            # Delete event from Google Calendar
            service.events().delete(
                calendarId=event.calendar_id, eventId=event_id
            ).execute()

            # Soft delete from local database using SafeDeleteModel
            event.delete()

            return Response(
                data={"message": "Event deleted successfully."},
                status=status.HTTP_200_OK,
            )

        except Exception as e:
            logger.error(
                f"Error deleting Google Calendar Event: {str(e)}",
                extra={"user_id": user_id},
            )
            return Response(
                data={"message": f"Error deleting Google Calendar event: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
