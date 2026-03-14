"""Tests for Google Calendar tool."""

import json
import pytest
from unittest.mock import MagicMock, patch, AsyncMock

from nanobot.agent.tools.google_calendar import GoogleCalendarTool


# -------------------------------------------------------------------
# Schema / metadata
# -------------------------------------------------------------------

def test_tool_name():
    tool = GoogleCalendarTool(credentials_file="/tmp/creds.json")
    assert tool.name == "google_calendar"


def test_tool_description_non_empty():
    tool = GoogleCalendarTool(credentials_file="/tmp/creds.json")
    assert len(tool.description) > 20


def test_tool_parameters_has_action():
    tool = GoogleCalendarTool(credentials_file="/tmp/creds.json")
    schema = tool.parameters
    assert "action" in schema["properties"]
    assert schema["properties"]["action"]["enum"] == [
        "list_events", "find_free_slots", "create_event", "delete_event"
    ]


def test_to_schema_format():
    tool = GoogleCalendarTool(credentials_file="/tmp/creds.json")
    schema = tool.to_schema()
    assert schema["type"] == "function"
    assert schema["function"]["name"] == "google_calendar"
    assert "parameters" in schema["function"]


# -------------------------------------------------------------------
# Error handling
# -------------------------------------------------------------------

@pytest.mark.asyncio
async def test_no_credentials_returns_error():
    """Tool should return helpful error when no credentials configured."""
    tool = GoogleCalendarTool(credentials_file="")
    result = await tool.execute(action="list_events")
    assert "Error" in result
    assert "credentials" in result.lower()


@pytest.mark.asyncio
async def test_unknown_action():
    """Unknown action should return error."""
    tool = GoogleCalendarTool(credentials_file="/tmp/creds.json")
    with patch("nanobot.agent.tools.google_calendar._get_calendar_service"):
        result = await tool.execute(action="unknown_action")
    assert "Error" in result
    assert "unknown_action" in result


# -------------------------------------------------------------------
# RFC 3339 helper
# -------------------------------------------------------------------

def test_to_rfc3339_date_only():
    assert GoogleCalendarTool._to_rfc3339("2026-03-14") == "2026-03-14T00:00:00Z"


def test_to_rfc3339_datetime_no_tz():
    assert GoogleCalendarTool._to_rfc3339("2026-03-14T19:00:00") == "2026-03-14T19:00:00Z"


def test_to_rfc3339_already_utc():
    assert GoogleCalendarTool._to_rfc3339("2026-03-14T19:00:00Z") == "2026-03-14T19:00:00Z"


def test_to_rfc3339_with_offset():
    val = "2026-03-14T19:00:00+01:00"
    assert GoogleCalendarTool._to_rfc3339(val) == val


# -------------------------------------------------------------------
# list_events (mocked)
# -------------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_events_no_events():
    """Empty calendar should return 'no events' message."""
    mock_service = MagicMock()
    mock_service.events.return_value.list.return_value.execute.return_value = {
        "items": []
    }

    tool = GoogleCalendarTool(credentials_file="/tmp/creds.json")
    with patch("nanobot.agent.tools.google_calendar._get_calendar_service", return_value=mock_service):
        result = await tool.execute(
            action="list_events",
            start_date="2026-03-14",
            end_date="2026-03-15",
        )

    assert "No events" in result


@pytest.mark.asyncio
async def test_list_events_with_events():
    """Should format events with emojis and IDs."""
    mock_service = MagicMock()
    mock_service.events.return_value.list.return_value.execute.return_value = {
        "items": [
            {
                "id": "evt_123",
                "summary": "Team Standup",
                "start": {"dateTime": "2026-03-14T09:00:00Z"},
                "end": {"dateTime": "2026-03-14T09:30:00Z"},
                "location": "Office",
                "attendees": [{"email": "alex@example.com"}],
            }
        ]
    }

    tool = GoogleCalendarTool(credentials_file="/tmp/creds.json")
    with patch("nanobot.agent.tools.google_calendar._get_calendar_service", return_value=mock_service):
        result = await tool.execute(action="list_events", start_date="2026-03-14")

    assert "Team Standup" in result
    assert "evt_123" in result
    assert "Office" in result
    assert "alex@example.com" in result


# -------------------------------------------------------------------
# create_event (mocked)
# -------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_event_missing_times():
    """Should error when start/end times are missing."""
    tool = GoogleCalendarTool(credentials_file="/tmp/creds.json")
    with patch("nanobot.agent.tools.google_calendar._get_calendar_service"):
        result = await tool.execute(action="create_event", title="Dinner")
    assert "Error" in result
    assert "start_time" in result


@pytest.mark.asyncio
async def test_create_event_success():
    """Should create event and return confirmation."""
    mock_service = MagicMock()
    mock_service.events.return_value.insert.return_value.execute.return_value = {
        "id": "new_evt_456",
        "summary": "Dinner with Alex",
        "start": {"dateTime": "2026-03-14T19:00:00Z"},
        "end": {"dateTime": "2026-03-14T21:00:00Z"},
        "htmlLink": "https://calendar.google.com/event/new_evt_456",
    }

    tool = GoogleCalendarTool(credentials_file="/tmp/creds.json")
    with patch("nanobot.agent.tools.google_calendar._get_calendar_service", return_value=mock_service):
        result = await tool.execute(
            action="create_event",
            title="Dinner with Alex",
            start_time="2026-03-14T19:00:00",
            end_time="2026-03-14T21:00:00",
            attendees=["alex@example.com"],
        )

    assert "✅" in result
    assert "Dinner with Alex" in result
    assert "alex@example.com" in result
    assert "new_evt_456" in result


# -------------------------------------------------------------------
# delete_event (mocked)
# -------------------------------------------------------------------

@pytest.mark.asyncio
async def test_delete_event_missing_id():
    """Should error when event_id is missing."""
    tool = GoogleCalendarTool(credentials_file="/tmp/creds.json")
    with patch("nanobot.agent.tools.google_calendar._get_calendar_service"):
        result = await tool.execute(action="delete_event")
    assert "Error" in result
    assert "event_id" in result


@pytest.mark.asyncio
async def test_delete_event_success():
    """Should delete and confirm."""
    mock_service = MagicMock()
    mock_service.events.return_value.delete.return_value.execute.return_value = None

    tool = GoogleCalendarTool(credentials_file="/tmp/creds.json")
    with patch("nanobot.agent.tools.google_calendar._get_calendar_service", return_value=mock_service):
        result = await tool.execute(action="delete_event", event_id="evt_789")

    assert "✅" in result
    assert "evt_789" in result


# -------------------------------------------------------------------
# find_free_slots (mocked)
# -------------------------------------------------------------------

@pytest.mark.asyncio
async def test_find_free_slots():
    """Should find gaps between busy periods."""
    mock_service = MagicMock()
    mock_service.freebusy.return_value.query.return_value.execute.return_value = {
        "calendars": {
            "primary": {
                "busy": [
                    {"start": "2026-03-14T09:00:00Z", "end": "2026-03-14T10:00:00Z"},
                    {"start": "2026-03-14T14:00:00Z", "end": "2026-03-14T15:00:00Z"},
                ]
            }
        }
    }

    tool = GoogleCalendarTool(credentials_file="/tmp/creds.json")
    with patch("nanobot.agent.tools.google_calendar._get_calendar_service", return_value=mock_service):
        result = await tool.execute(
            action="find_free_slots",
            start_date="2026-03-14T08:00:00Z",
            end_date="2026-03-14T18:00:00Z",
            slot_duration_minutes=60,
        )

    assert "Free slots" in result


# -------------------------------------------------------------------
# Config integration
# -------------------------------------------------------------------

def test_config_schema_has_google_calendar():
    """GoogleCalendarConfig should exist in ToolsConfig."""
    from nanobot.config.schema import ToolsConfig
    tools = ToolsConfig()
    assert hasattr(tools, "google_calendar")
    assert tools.google_calendar.enabled is False
    assert tools.google_calendar.calendar_id == "primary"
    assert tools.google_calendar.timezone == "Europe/London"
