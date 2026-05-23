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


def _make_time(dt_str: str) -> dict:
    """Støtter både heldagshendelser (YYYY-MM-DD) og tidspunkt (YYYY-MM-DDTHH:MM:SS)."""
    if "T" in dt_str:
        return {"dateTime": dt_str, "timeZone": TIMEZONE}
    return {"date": dt_str}


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
        recurrence_frequency: str = None,
        recurrence_until: str = None,
        recurrence_count: int = None,
    ) -> dict:
        event = {
            "summary": title,
            "description": description,
            "location": location,
            "start": _make_time(start_datetime),
            "end": _make_time(end_datetime),
        }
        if color_id:
            event["colorId"] = color_id
        if recurrence_frequency:
            rrule = f"RRULE:FREQ={recurrence_frequency}"
            if recurrence_until:
                rrule += f";UNTIL={recurrence_until.replace('-', '')}T000000Z"
            elif recurrence_count:
                rrule += f";COUNT={recurrence_count}"
            event["recurrence"] = [rrule]
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
            existing["start"] = _make_time(kwargs["start_datetime"])
        if "end_datetime" in kwargs:
            existing["end"] = _make_time(kwargs["end_datetime"])
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

    def list_events(self, days_ahead: int = 14, days_from: int = 0) -> list[dict]:
        now = datetime.now(timezone.utc)
        time_min = now + timedelta(days=days_from)
        time_max = now + timedelta(days=days_from + days_ahead)

        result = (
            self._service.events()
            .list(
                calendarId=CALENDAR_ID,
                timeMin=time_min.isoformat(),
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
