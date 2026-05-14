import os
import logging
from datetime import datetime, timedelta, timezone

from google.oauth2 import service_account
from googleapiclient.discovery import build

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/calendar"]
SERVICE_ACCOUNT_FILE = "/app/credentials/service_account.json"
CALENDAR_ID = os.environ.get("GOOGLE_CALENDAR_ID", "primary")
TIMEZONE = "Europe/Oslo"


class CalendarService:
    def __init__(self):
        creds = service_account.Credentials.from_service_account_file(
            SERVICE_ACCOUNT_FILE, scopes=SCOPES
        )
        self._service = build("calendar", "v3", credentials=creds, cache_discovery=False)

    def create_event(
        self,
        title: str,
        start_datetime: str,
        end_datetime: str,
        description: str = "",
        location: str = "",
        color_id: str = None,
    ) -> dict:
        event = {
            "summary": title,
            "description": description,
            "location": location,
            "start": {"dateTime": start_datetime, "timeZone": TIMEZONE},
            "end": {"dateTime": end_datetime, "timeZone": TIMEZONE},
        }
        if color_id:
            event["colorId"] = color_id
        result = self._service.events().insert(calendarId=CALENDAR_ID, body=event).execute()
        logger.info("Opprettet hendelse: %s (id=%s)", title, result["id"])
        return result

    def update_event(self, event_id: str, **kwargs) -> dict:
        existing = self._service.events().get(calendarId=CALENDAR_ID, eventId=event_id).execute()

        if "title" in kwargs:
            existing["summary"] = kwargs["title"]
        if "description" in kwargs:
            existing["description"] = kwargs["description"]
        if "location" in kwargs:
            existing["location"] = kwargs["location"]
        if "start_datetime" in kwargs:
            existing["start"] = {"dateTime": kwargs["start_datetime"], "timeZone": TIMEZONE}
        if "end_datetime" in kwargs:
            existing["end"] = {"dateTime": kwargs["end_datetime"], "timeZone": TIMEZONE}
        if "color_id" in kwargs and kwargs["color_id"]:
            existing["colorId"] = kwargs["color_id"]

        result = (
            self._service.events()
            .update(calendarId=CALENDAR_ID, eventId=event_id, body=existing)
            .execute()
        )
        logger.info("Oppdaterte hendelse id=%s", event_id)
        return result

    def delete_event(self, event_id: str) -> None:
        self._service.events().delete(calendarId=CALENDAR_ID, eventId=event_id).execute()
        logger.info("Slettet hendelse id=%s", event_id)

    def list_events(self, days_ahead: int = 14) -> list[dict]:
        now = datetime.now(timezone.utc)
        time_max = now + timedelta(days=days_ahead)

        result = (
            self._service.events()
            .list(
                calendarId=CALENDAR_ID,
                timeMin=now.isoformat(),
                timeMax=time_max.isoformat(),
                singleEvents=True,
                orderBy="startTime",
                maxResults=25,
            )
            .execute()
        )

        events = []
        for item in result.get("items", []):
            start = item["start"].get("dateTime", item["start"].get("date", ""))
            end = item["end"].get("dateTime", item["end"].get("date", ""))
            events.append({
                "id": item["id"],
                "title": item.get("summary", "(uten tittel)"),
                "start": start,
                "end": end,
                "description": item.get("description", ""),
                "location": item.get("location", ""),
                "colorId": item.get("colorId", ""),
            })
        return events
