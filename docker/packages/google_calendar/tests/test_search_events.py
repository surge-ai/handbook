"""Tests for event search behavior."""

import importlib
import inspect
import json

import pytest
from pydantic import EmailStr, TypeAdapter, ValidationError

from google_calendar.models import (
    AttendeeResponseStatus,
    AvailabilityDurationMinutes,
    ListEventsMaxResults,
    Rfc3339OffsetDateTimeString,
    SearchEventsMaxResults,
    SearchEventsOrderBy,
)


def _get_gc():
    return importlib.import_module("google_calendar.server")


def _get_state():
    return importlib.import_module("google_calendar.state")


@pytest.fixture
def calendar_data(tmp_path):
    external_services = tmp_path / "external_services"
    external_services.mkdir()
    data_file = external_services / "calendar_data.json"
    data_file.write_text(
        json.dumps(
            {
                "events": {
                    "evt-budget": {
                        "id": "evt-budget",
                        "summary": "Budget Review",
                        "description": "Q3 planning with the finance team",
                        "location": "Conference Room A",
                        "start": {"dateTime": "2025-06-03T14:00:00Z"},
                        "end": {"dateTime": "2025-06-03T15:00:00Z"},
                        "created": "2025-05-01T00:00:00Z",
                        "updated": "2025-05-02T00:00:00Z",
                        "creator": {"email": "manager@example.com", "displayName": "Budget Manager"},
                        "organizer": {"email": "finance-lead@example.com", "displayName": "Finance Lead"},
                        "attendees": [
                            {
                                "email": "alice@example.com",
                                "displayName": "Alice Chen",
                                "responseStatus": "accepted",
                            },
                            {"email": "bob@example.com", "displayName": "Bob Smith", "responseStatus": "declined"},
                        ],
                    },
                    "evt-design": {
                        "id": "evt-design",
                        "summary": "Design Sync",
                        "description": "Review updated mobile mockups",
                        "location": "Studio",
                        "start": {"dateTime": "2025-06-05T16:00:00Z"},
                        "end": {"dateTime": "2025-06-05T17:00:00Z"},
                        "created": "2025-05-01T00:00:00Z",
                        "updated": "2025-05-04T00:00:00Z",
                        "attendees": [
                            {"email": "carol@example.com", "displayName": "Carol Lee", "responseStatus": "tentative"}
                        ],
                    },
                    "evt-naive": {
                        "id": "evt-naive",
                        "summary": "Naive Time Review",
                        "description": "Stored without a timezone offset",
                        "start": {"dateTime": "2025-06-07T10:00:00"},
                        "end": {"dateTime": "2025-06-07T11:00:00"},
                        "created": "2025-05-01T00:00:00Z",
                        "updated": "2025-05-06T00:00:00Z",
                    },
                    "evt-broken": {
                        "id": "evt-broken",
                        "summary": "Broken Planning",
                        "description": "Missing start and end should be reported when range-filtered",
                    },
                    "evt-standup": {
                        "id": "evt-standup",
                        "summary": "Daily Standup",
                        "description": "Engineering status updates",
                        "start": {"dateTime": "2025-06-01T09:00:00Z"},
                        "end": {"dateTime": "2025-06-01T09:15:00Z"},
                        "created": "2025-05-01T00:00:00Z",
                        "updated": "2025-05-03T00:00:00Z",
                        "recurrence": ["RRULE:FREQ=DAILY;COUNT=5"],
                    },
                },
                "calendars": {
                    "work": {
                        "id": "work",
                        "summary": "Work Calendar",
                        "events": {
                            "evt-roadmap": {
                                "id": "evt-roadmap",
                                "summary": "Roadmap Planning",
                                "description": "Quarterly product planning",
                                "location": "War Room",
                                "start": {"dateTime": "2025-06-04T10:00:00Z"},
                                "end": {"dateTime": "2025-06-04T11:30:00Z"},
                                "created": "2025-05-01T00:00:00Z",
                                "updated": "2025-05-05T00:00:00Z",
                                "creator": {"email": "product@example.com", "displayName": "Product"},
                                "organizer": {"email": "dana@example.com", "displayName": "Dana Patel"},
                                "attendees": [{"email": "dana@example.com", "displayName": "Dana Patel"}],
                            }
                        },
                    }
                },
            }
        )
    )
    return data_file


@pytest.fixture
def outputdir(tmp_path):
    out = tmp_path / "output" / "google_calendar"
    out.mkdir(parents=True)
    return out


@pytest.fixture(autouse=True)
def _patch_globals(calendar_data, outputdir):
    state = _get_state()
    workspace = calendar_data.parent.parent / "workspace"
    workspace.mkdir()
    state.set_agent_workspace(str(workspace))
    state.set_snapshot_paths(final_path=outputdir / "final.json", bundle_state_path=None)
    yield
    state.set_agent_workspace(None)
    state.set_snapshot_paths(final_path=None, bundle_state_path=None)


@pytest.mark.asyncio
async def test_search_events_matches_all_query_terms_case_insensitively():
    gc = _get_gc()

    result = await gc.search_events(query="budget finance")

    assert result["status"] == "success"
    assert result["count"] == 1
    assert result["events"][0]["id"] == "evt-budget"
    assert result["events"][0]["calendar_id"] == "primary"


@pytest.mark.asyncio
async def test_search_events_supports_quoted_phrases():
    gc = _get_gc()

    result = await gc.search_events(query='"Conference Room A"')

    assert result["count"] == 1
    assert result["events"][0]["summary"] == "Budget Review"


@pytest.mark.asyncio
async def test_search_events_quoted_phrases_do_not_span_field_boundaries():
    gc = _get_gc()

    result = await gc.search_events(query='"review q3"')

    assert result["events"] == []
    assert result["count"] == 0


@pytest.mark.asyncio
async def test_search_events_matches_attendee_query_text():
    gc = _get_gc()

    result = await gc.search_events(query="carol@example.com")

    assert result["count"] == 1
    assert result["events"][0]["id"] == "evt-design"


@pytest.mark.asyncio
async def test_search_events_attendee_filter_can_search_without_keywords():
    gc = _get_gc()

    result = await gc.search_events(query="", attendee_email="bob@example.com")

    assert result["count"] == 1
    assert result["events"][0]["id"] == "evt-budget"


@pytest.mark.asyncio
async def test_search_events_filters_by_response_status_for_attendee():
    gc = _get_gc()

    accepted = await gc.search_events(
        query="", attendee_email="alice@example.com", response_status=AttendeeResponseStatus.ACCEPTED
    )
    assert accepted["count"] == 1
    assert accepted["events"][0]["id"] == "evt-budget"

    declined = await gc.search_events(
        query="", attendee_email="alice@example.com", response_status=AttendeeResponseStatus.DECLINED
    )
    assert declined["events"] == []
    assert declined["count"] == 0


@pytest.mark.asyncio
async def test_search_events_filters_by_response_status_without_attendee():
    gc = _get_gc()

    result = await gc.search_events(query="", response_status=AttendeeResponseStatus.DECLINED)

    assert result["count"] == 1
    assert result["events"][0]["id"] == "evt-budget"


def test_search_events_response_status_is_schema_enum():
    gc = _get_gc()

    assert gc.search_events.__annotations__["response_status"] == AttendeeResponseStatus | None
    with pytest.raises(ValidationError):
        TypeAdapter(AttendeeResponseStatus).validate_python("maybe")


@pytest.mark.asyncio
async def test_search_events_filters_by_creator_and_organizer():
    gc = _get_gc()

    by_creator = await gc.search_events(query="", creator_email="manager@example.com")
    assert by_creator["count"] == 1
    assert by_creator["events"][0]["id"] == "evt-budget"

    by_organizer = await gc.search_events(query="", organizer_email="dana@example.com")
    assert by_organizer["count"] == 1
    assert by_organizer["events"][0]["id"] == "evt-roadmap"


@pytest.mark.asyncio
async def test_search_events_matches_creator_and_organizer_query_text():
    gc = _get_gc()

    result = await gc.search_events(query="finance-lead@example.com")

    assert result["count"] == 1
    assert result["events"][0]["id"] == "evt-budget"


@pytest.mark.asyncio
async def test_search_events_time_range_filters_results():
    gc = _get_gc()

    result = await gc.search_events(
        query="planning",
        timeMin="2025-06-04T00:00:00Z",
        timeMax="2025-06-06T00:00:00Z",
    )

    assert result["count"] == 1
    assert result["events"][0]["id"] == "evt-roadmap"


@pytest.mark.asyncio
async def test_search_events_handles_naive_event_times_with_aware_ranges():
    gc = _get_gc()

    result = await gc.search_events(
        query="naive",
        timeMin="2025-06-07T00:00:00Z",
        timeMax="2025-06-08T00:00:00Z",
    )

    assert result["count"] == 1
    assert result["events"][0]["id"] == "evt-naive"


@pytest.mark.asyncio
async def test_search_events_calendar_id_scopes_results():
    gc = _get_gc()

    result = await gc.search_events(query="planning", calendar_id="work")

    assert result["count"] == 1
    assert result["events"][0]["id"] == "evt-roadmap"
    assert result["events"][0]["calendar_summary"] == "Work Calendar"


@pytest.mark.asyncio
async def test_search_events_nonexistent_calendar_returns_empty_results():
    gc = _get_gc()

    result = await gc.search_events(query="planning", calendar_id="nonexistent")

    assert result["status"] == "success"
    assert result["events"] == []
    assert result["count"] == 0


@pytest.mark.asyncio
async def test_search_events_expands_recurring_events_with_time_range():
    gc = _get_gc()

    result = await gc.search_events(
        query="standup",
        timeMin="2025-06-03T00:00:00Z",
        timeMax="2025-06-05T00:00:00Z",
    )

    assert result["count"] == 2
    assert [event["start"]["dateTime"] for event in result["events"]] == [
        "2025-06-03T09:00:00+00:00",
        "2025-06-04T09:00:00+00:00",
    ]
    assert {event["recurringEventId"] for event in result["events"]} == {"evt-standup"}


@pytest.mark.asyncio
async def test_search_events_expands_recurring_events_without_time_range():
    gc = _get_gc()

    result = await gc.search_events(query="standup")

    assert result["count"] == 5
    assert [event["start"]["dateTime"] for event in result["events"]] == [
        "2025-06-01T09:00:00+00:00",
        "2025-06-02T09:00:00+00:00",
        "2025-06-03T09:00:00+00:00",
        "2025-06-04T09:00:00+00:00",
        "2025-06-05T09:00:00+00:00",
    ]
    assert {event["recurringEventId"] for event in result["events"]} == {"evt-standup"}


@pytest.mark.asyncio
async def test_search_events_can_order_by_updated_and_limit_results():
    gc = _get_gc()

    result = await gc.search_events(query="planning", maxResults=1, orderBy=SearchEventsOrderBy.UPDATED)

    assert result["count"] == 1
    assert result["events"][0]["id"] == "evt-roadmap"


@pytest.mark.asyncio
async def test_search_events_max_results_zero_returns_no_results():
    gc = _get_gc()

    result = await gc.search_events(query="planning", maxResults=0)

    assert result["status"] == "success"
    assert result["events"] == []
    assert result["count"] == 0


def test_search_events_max_results_is_schema_non_negative():
    with pytest.raises(ValidationError):
        TypeAdapter(SearchEventsMaxResults).validate_python(-1)


def test_range_bounds_require_rfc3339_offset_in_schema():
    gc = _get_gc()

    assert gc.list_events.__annotations__["timeMin"] == Rfc3339OffsetDateTimeString
    assert gc.list_events.__annotations__["timeMax"] == Rfc3339OffsetDateTimeString
    assert gc.search_events.__annotations__["timeMin"] == Rfc3339OffsetDateTimeString | None
    assert gc.check_availability.__annotations__["timeMin"] == Rfc3339OffsetDateTimeString
    assert gc.check_availability.__annotations__["attendee_emails"] == list[EmailStr] | None
    assert gc.check_availability.__annotations__["duration_minutes"] == AvailabilityDurationMinutes
    with pytest.raises(ValidationError):
        TypeAdapter(Rfc3339OffsetDateTimeString).validate_python("2025-06-01T00:00:00")
    with pytest.raises(ValidationError):
        TypeAdapter(AvailabilityDurationMinutes).validate_python(0)


def test_search_events_order_by_is_schema_enum():
    gc = _get_gc()

    assert gc.search_events.__annotations__["orderBy"] == SearchEventsOrderBy | None
    with pytest.raises(ValidationError):
        TypeAdapter(SearchEventsOrderBy).validate_python("summary")


def test_list_events_order_by_is_schema_enum():
    gc = _get_gc()

    assert gc.list_events.__annotations__["orderBy"] == SearchEventsOrderBy | None
    assert inspect.signature(gc.list_events).parameters["orderBy"].default is None
    assert gc.list_events.__annotations__["maxResults"] == ListEventsMaxResults | None
    with pytest.raises(ValidationError):
        TypeAdapter(SearchEventsOrderBy).validate_python("summary")
    with pytest.raises(ValidationError):
        TypeAdapter(ListEventsMaxResults).validate_python(-1)


@pytest.mark.asyncio
async def test_search_events_reports_skipped_unparseable_events():
    gc = _get_gc()

    result = await gc.search_events(
        query="planning",
        timeMin="2025-06-01T00:00:00Z",
        timeMax="2025-06-10T00:00:00Z",
    )

    assert result["status"] == "success"
    assert result["skipped_unparseable"] == ["evt-broken"]


@pytest.mark.asyncio
async def test_search_events_no_match_returns_empty_results():
    gc = _get_gc()

    result = await gc.search_events(query="vendor escalation")

    assert result["status"] == "success"
    assert result["events"] == []
    assert result["count"] == 0


@pytest.mark.asyncio
async def test_search_events_requires_query_or_attendee_filter():
    gc = _get_gc()

    result = await gc.search_events(query="")

    assert result["status"] == "error"
    assert result["events"] == []
    assert result["count"] == 0


@pytest.mark.asyncio
async def test_search_events_reports_unsupported_query_operator():
    gc = _get_gc()

    result = await gc.search_events(query="before:2024-01-01 planning")

    assert result["status"] == "success"
    assert result["count"] == 0
    assert result["warnings"] == [
        "Unsupported Calendar query operator 'before:'; use search_events parameters such as timeMin, timeMax, "
        "attendee_email, response_status, organizer_email, or creator_email."
    ]
