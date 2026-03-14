"""Google Calendar tool for the agent."""

import json
import os
from datetime import datetime, timedelta
from typing import Any

from loguru import logger

from nanobot.agent.tools.base import Tool

SCOPES = [
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.readonly",
]

_TOKEN_CACHE = os.path.expanduser("~/.nanobot/google-token.json")


def _get_calendar_service(credentials_file: str):
    """Build a Google Calendar API service.

    Auto-detects credential type:
    - OAuth client credentials (installed app) → browser login + token cache
    - Service account → direct auth
    """
    from pathlib import Path

    creds_path = Path(credentials_file).expanduser()
    if not creds_path.exists():
        raise FileNotFoundError(f"Credentials file not found: {creds_path}")

    with open(creds_path) as f:
        creds_data = json.load(f)

    # Detect type: service account vs OAuth client
    if creds_data.get("type") == "service_account":
        return _build_from_service_account(str(creds_path))
    elif "installed" in creds_data or "web" in creds_data:
        return _build_from_oauth(str(creds_path))
    else:
        raise ValueError(
            "Unrecognized credentials format. Expected service_account or OAuth client JSON."
        )


def _build_from_service_account(creds_path: str):
    """Build service from a service account key."""
    from google.oauth2 import service_account
    from googleapiclient.discovery import build

    creds = service_account.Credentials.from_service_account_file(
        creds_path, scopes=SCOPES,
    )
    return build("calendar", "v3", credentials=creds, cache_discovery=False)


def _build_from_oauth(creds_path: str):
    """Build service from OAuth client credentials with token caching."""
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build

    creds = None
    token_path = _TOKEN_CACHE

    # Load cached token if available
    if os.path.exists(token_path):
        creds = Credentials.from_authorized_user_file(token_path, SCOPES)

    # Refresh or get new credentials
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(creds_path, SCOPES)
            creds = flow.run_local_server(port=0)

        # Cache the token for next time
        with open(token_path, "w") as token_file:
            token_file.write(creds.to_json())
        logger.info("Google OAuth token cached at {}", token_path)

    return build("calendar", "v3", credentials=creds, cache_discovery=False)


class GoogleCalendarTool(Tool):
    """Manage Google Calendar: list events, check availability, create and delete events."""

    name = "google_calendar"
    description = (
        "Interact with Google Calendar. Actions: "
        "list_events (show events in a date range), "
        "find_free_slots (find available time windows), "
        "create_event (book a new event with optional attendees), "
        "delete_event (remove an event by ID)."
    )
    parameters = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["list_events", "find_free_slots", "create_event", "delete_event"],
                "description": "The calendar operation to perform.",
            },
            "start_date": {
                "type": "string",
                "description": "Start date/datetime in ISO 8601 format (e.g. 2026-03-14 or 2026-03-14T09:00:00). Used by list_events and find_free_slots.",
            },
            "end_date": {
                "type": "string",
                "description": "End date/datetime in ISO 8601 format. Used by list_events and find_free_slots.",
            },
            "title": {
                "type": "string",
                "description": "Event title/summary. Used by create_event.",
            },
            "start_time": {
                "type": "string",
                "description": "Event start datetime in ISO 8601 (e.g. 2026-03-14T19:00:00). Used by create_event.",
            },
            "end_time": {
                "type": "string",
                "description": "Event end datetime in ISO 8601. Used by create_event.",
            },
            "description": {
                "type": "string",
                "description": "Event description. Used by create_event.",
            },
            "location": {
                "type": "string",
                "description": "Event location. Used by create_event.",
            },
            "attendees": {
                "type": "array",
                "items": {"type": "string"},
                "description": "List of attendee email addresses. Used by create_event.",
            },
            "event_id": {
                "type": "string",
                "description": "Event ID. Used by delete_event.",
            },
            "slot_duration_minutes": {
                "type": "integer",
                "description": "Minimum free slot duration in minutes (default 60). Used by find_free_slots.",
            },
        },
        "required": ["action"],
    }

    def __init__(
        self,
        credentials_file: str = "",
        calendar_id: str = "primary",
        timezone: str = "Europe/London",
    ):
        self._credentials_file = credentials_file or os.environ.get(
            "GOOGLE_CALENDAR_CREDENTIALS", ""
        )
        self._calendar_id = calendar_id
        self._timezone = timezone

    async def execute(self, action: str, **kwargs: Any) -> str:
        if not self._credentials_file:
            return (
                "Error: Google Calendar credentials not configured. "
                "Set tools.googleCalendar.credentialsFile in config.json "
                "or export GOOGLE_CALENDAR_CREDENTIALS."
            )

        try:
            if action == "list_events":
                return await self._list_events(**kwargs)
            elif action == "find_free_slots":
                return await self._find_free_slots(**kwargs)
            elif action == "create_event":
                return await self._create_event(**kwargs)
            elif action == "delete_event":
                return await self._delete_event(**kwargs)
            else:
                return f"Error: Unknown action '{action}'"
        except Exception as e:
            logger.error("GoogleCalendar error ({}): {}", action, e)
            return f"Error: {e}"

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    async def _list_events(self, **kwargs: Any) -> str:
        start_date = kwargs.get("start_date")
        end_date = kwargs.get("end_date")

        now = datetime.now()
        if not start_date:
            time_min = now.replace(hour=0, minute=0, second=0).isoformat() + "Z"
        else:
            time_min = self._to_rfc3339(start_date)

        if not end_date:
            time_max = (now + timedelta(days=7)).replace(hour=23, minute=59, second=59).isoformat() + "Z"
        else:
            time_max = self._to_rfc3339(end_date)

        service = _get_calendar_service(self._credentials_file)
        result = (
            service.events()
            .list(
                calendarId=self._calendar_id,
                timeMin=time_min,
                timeMax=time_max,
                singleEvents=True,
                orderBy="startTime",
                maxResults=25,
                timeZone=self._timezone,
            )
            .execute()
        )

        events = result.get("items", [])
        if not events:
            return f"No events found between {start_date or 'today'} and {end_date or 'next 7 days'}."

        lines = [f"📅 Events ({len(events)}):\n"]
        for ev in events:
            start = ev["start"].get("dateTime", ev["start"].get("date"))
            end = ev["end"].get("dateTime", ev["end"].get("date"))
            summary = ev.get("summary", "(No title)")
            location = ev.get("location", "")
            lines.append(f"• {summary}")
            lines.append(f"  🕐 {start} → {end}")
            if location:
                lines.append(f"  📍 {location}")
            attendees = ev.get("attendees", [])
            if attendees:
                names = [a.get("email", "") for a in attendees[:5]]
                lines.append(f"  👥 {', '.join(names)}")
            lines.append(f"  ID: {ev['id']}")
            lines.append("")

        return "\n".join(lines)

    async def _find_free_slots(self, **kwargs: Any) -> str:
        start_date = kwargs.get("start_date")
        end_date = kwargs.get("end_date")
        slot_duration = kwargs.get("slot_duration_minutes", 60)

        now = datetime.now()
        if not start_date:
            time_min = now.isoformat() + "Z"
        else:
            time_min = self._to_rfc3339(start_date)

        if not end_date:
            time_max = (now + timedelta(days=7)).isoformat() + "Z"
        else:
            time_max = self._to_rfc3339(end_date)

        service = _get_calendar_service(self._credentials_file)
        body = {
            "timeMin": time_min,
            "timeMax": time_max,
            "timeZone": self._timezone,
            "items": [{"id": self._calendar_id}],
        }
        result = service.freebusy().query(body=body).execute()
        busy_periods = result["calendars"][self._calendar_id]["busy"]

        # Parse busy periods
        busy = []
        for period in busy_periods:
            bs = datetime.fromisoformat(period["start"].replace("Z", "+00:00"))
            be = datetime.fromisoformat(period["end"].replace("Z", "+00:00"))
            busy.append((bs, be))
        busy.sort(key=lambda x: x[0])

        # Find free slots
        range_start = datetime.fromisoformat(time_min.replace("Z", "+00:00"))
        range_end = datetime.fromisoformat(time_max.replace("Z", "+00:00"))
        min_duration = timedelta(minutes=slot_duration)

        free_slots = []
        current = range_start
        for bs, be in busy:
            if current < bs:
                gap = bs - current
                if gap >= min_duration:
                    free_slots.append((current, bs))
            current = max(current, be)
        if current < range_end:
            gap = range_end - current
            if gap >= min_duration:
                free_slots.append((current, range_end))

        if not free_slots:
            return f"No free slots of {slot_duration}+ minutes found in the given range."

        lines = [f"🟢 Free slots ({len(free_slots)}, min {slot_duration} min):\n"]
        for fs, fe in free_slots[:10]:
            duration = fe - fs
            hours, rem = divmod(int(duration.total_seconds()), 3600)
            mins = rem // 60
            dur_str = f"{hours}h{mins:02d}m" if hours else f"{mins}m"
            lines.append(f"• {fs.strftime('%a %b %d %H:%M')} → {fe.strftime('%H:%M')} ({dur_str})")

        return "\n".join(lines)

    async def _create_event(self, **kwargs: Any) -> str:
        title = kwargs.get("title", "New Event")
        start_time = kwargs.get("start_time")
        end_time = kwargs.get("end_time")
        description = kwargs.get("description", "")
        location = kwargs.get("location", "")
        attendees = kwargs.get("attendees", [])

        if not start_time or not end_time:
            return "Error: start_time and end_time are required for create_event."

        event_body: dict[str, Any] = {
            "summary": title,
            "start": {"dateTime": self._to_rfc3339(start_time), "timeZone": self._timezone},
            "end": {"dateTime": self._to_rfc3339(end_time), "timeZone": self._timezone},
        }
        if description:
            event_body["description"] = description
        if location:
            event_body["location"] = location
        if attendees:
            event_body["attendees"] = [{"email": email} for email in attendees]

        service = _get_calendar_service(self._credentials_file)
        created = (
            service.events()
            .insert(
                calendarId=self._calendar_id,
                body=event_body,
                sendUpdates="all" if attendees else "none",
            )
            .execute()
        )

        result_lines = [
            f"✅ Event created successfully!",
            f"📌 {created.get('summary', title)}",
            f"🕐 {created['start'].get('dateTime', '')} → {created['end'].get('dateTime', '')}",
        ]
        if location:
            result_lines.append(f"📍 {location}")
        if attendees:
            result_lines.append(f"📧 Invites sent to: {', '.join(attendees)}")
        result_lines.append(f"🔗 {created.get('htmlLink', '')}")
        result_lines.append(f"ID: {created['id']}")

        return "\n".join(result_lines)

    async def _delete_event(self, **kwargs: Any) -> str:
        event_id = kwargs.get("event_id")
        if not event_id:
            return "Error: event_id is required for delete_event."

        service = _get_calendar_service(self._credentials_file)
        service.events().delete(
            calendarId=self._calendar_id, eventId=event_id
        ).execute()

        return f"✅ Event {event_id} deleted successfully."

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _to_rfc3339(dt_str: str) -> str:
        """Normalize a date/datetime string to RFC 3339 for Google API."""
        dt_str = dt_str.strip()
        # Already has timezone info
        if dt_str.endswith("Z") or "+" in dt_str[10:]:
            return dt_str
        # Date only → start of day
        if len(dt_str) == 10:
            return dt_str + "T00:00:00Z"
        # Datetime without TZ
        if "T" in dt_str and not dt_str.endswith("Z"):
            return dt_str + "Z"
        return dt_str + "T00:00:00Z"
