"""Event schema enforces the supported Google Calendar mock shape."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from google_calendar.models import (
    Calendar,
    CalendarPerson,
    CalendarState,
    Event,
    EventAttendee,
    EventDateTime,
)
from google_calendar.tools.search import _parse_event_end, _parse_event_start


def _minimal_event(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "id": "evt-1",
        "summary": "s",
        "start": {"dateTime": "2025-06-01T10:00:00Z"},
        "end": {"dateTime": "2025-06-01T11:00:00Z"},
    }
    base.update(overrides)
    return base


def test_event_accepts_payload_without_created_and_updated():
    event = Event.model_validate(_minimal_event())
    assert event.created is None
    assert event.updated is None


def test_event_rejects_unmodeled_extra_fields():
    with pytest.raises(ValidationError):
        Event.model_validate(_minimal_event(conferenceData={"entryPoints": []}))


def test_event_coerces_single_recurrence_string_to_list():
    event = Event.model_validate(_minimal_event(recurrence="RRULE:FREQ=WEEKLY"))
    assert event.recurrence == ["RRULE:FREQ=WEEKLY"]


def test_event_recurrence_requires_supported_prefix():
    for recurrence in ["RRULE:FREQ=WEEKLY", "EXRULE:FREQ=WEEKLY", "RDATE:20250601", "EXDATE:20250601"]:
        assert Event.model_validate(_minimal_event(recurrence=[recurrence]))

    for recurrence in ["FREQ=WEEKLY", "BAD:FREQ=WEEKLY"]:
        with pytest.raises(ValidationError):
            Event.model_validate(_minimal_event(recurrence=[recurrence]))


def test_calendar_state_round_trips_legacy_event():
    state = CalendarState.model_validate({"events": {"evt-1": _minimal_event(recurrence="RRULE:FREQ=DAILY")}})
    dumped = state.model_dump(mode="json", exclude_none=True)
    reloaded = CalendarState.model_validate(dumped)
    assert reloaded == state


def test_calendar_state_round_trips_multi_calendar_state():
    state = CalendarState.model_validate(
        {
            "timeZone": "America/New_York",
            "events": {},
            "calendars": {
                "work": {
                    "summary": "Work",
                    "timeZone": "Europe/London",
                    "events": {"evt-1": _minimal_event()},
                }
            },
        }
    )
    dumped = state.model_dump(mode="json", exclude_none=True)
    assert dumped["timeZone"] == "America/New_York"
    assert dumped["calendars"]["work"]["timeZone"] == "Europe/London"
    assert dumped["calendars"]["work"]["events"]["evt-1"]["id"] == "evt-1"
    assert CalendarState.model_validate(dumped) == state


def test_calendar_state_rejects_explicit_primary_calendar():
    with pytest.raises(ValidationError):
        CalendarState.model_validate(
            {
                "events": {"evt-1": _minimal_event()},
                "calendars": {
                    "primary": {
                        "summary": "Primary",
                        "events": {"evt-2": _minimal_event(id="evt-2")},
                    }
                },
            }
        )


def test_calendar_events_must_be_keyed_by_event_id():
    with pytest.raises(ValidationError):
        Calendar.model_validate(
            {
                "summary": "Work",
                "events": {"wrong-key": _minimal_event(id="evt-1")},
            }
        )

    with pytest.raises(ValidationError):
        CalendarState.model_validate({"events": {"wrong-key": _minimal_event(id="evt-1")}})


def test_calendar_time_zone_is_validated():
    assert Calendar.model_validate({"summary": "Work", "timeZone": "America/Los_Angeles"})

    with pytest.raises(ValidationError):
        Calendar.model_validate({"summary": "Work", "timeZone": "Not/AZone"})

    with pytest.raises(ValidationError):
        CalendarState.model_validate({"timeZone": "Not/AZone"})


@pytest.mark.parametrize(
    "payload",
    [
        {"dateTime": "2025-06-01T10:00:00Z"},
        {"dateTime": "2025-06-01T10:00:00-04:00"},
        {"dateTime": "2025-06-01T10:00:00.123Z"},
        {"dateTime": "2025-06-01T10:00:00", "timeZone": "America/New_York"},
        {"dateTime": "2025-06-01T10:00:00-04:00", "timeZone": "America/New_York"},
        {"date": "2025-06-01"},
    ],
)
def test_event_datetime_accepts_google_calendar_shapes(payload):
    assert EventDateTime.model_validate(payload)


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"dateTime": "2025-06-01T10:00:00Z", "date": "2025-06-01"},
        {"dateTime": "2025-06-01 10:00:00Z"},
        {"dateTime": "2025-06-01T10:00:00"},
        {"date": "2025-6-1"},
        {"date": "2025-02-31"},
        {"dateTime": "2025-06-01T10:00:00", "timeZone": "Not/AZone"},
        {"dateTime": "2025-06-01T10:00:00Z", "timeZone": "America/New_York"},
    ],
)
def test_event_datetime_rejects_invalid_shapes(payload):
    with pytest.raises(ValidationError):
        EventDateTime.model_validate(payload)


def test_offsetless_datetime_uses_event_timezone():
    event = _minimal_event(
        start={"dateTime": "2025-06-01T10:00:00", "timeZone": "America/New_York"},
        end={"dateTime": "2025-06-01T11:00:00", "timeZone": "America/New_York"},
    )

    parsed_start = _parse_event_start(event)

    assert parsed_start is not None
    assert parsed_start.isoformat() == "2025-06-01T10:00:00-04:00"


def test_legacy_all_day_same_day_end_is_normalized():
    event = _minimal_event(
        start={"date": "2025-06-01"},
        end={"date": "2025-06-01"},
    )

    assert _parse_event_end(event) == datetime(2025, 6, 2, tzinfo=UTC)


def test_event_rejects_mixed_start_end_shapes():
    with pytest.raises(ValidationError):
        Event.model_validate(_minimal_event(start={"dateTime": "2025-06-01T10:00:00Z"}, end={"date": "2025-06-02"}))


def test_event_rejects_end_before_start():
    with pytest.raises(ValidationError):
        Event.model_validate(
            _minimal_event(
                start={"dateTime": "2025-06-01T10:00:00Z"},
                end={"dateTime": "2025-06-01T10:00:00Z"},
            )
        )


def test_event_rejects_empty_id():
    with pytest.raises(ValidationError):
        Event.model_validate(_minimal_event(id=""))


def test_event_rejects_invalid_audit_timestamp():
    with pytest.raises(ValidationError):
        Event.model_validate(_minimal_event(created="2025-06-01T10:00:00"))


def test_event_transparency_is_typed():
    event = Event.model_validate(_minimal_event(transparency="transparent"))
    assert event.transparency == "transparent"

    with pytest.raises(ValidationError):
        Event.model_validate(_minimal_event(transparency="busy-ish"))


def test_from_gmail_events_default_to_transparent():
    event = Event.model_validate(_minimal_event(eventType="fromGmail"))

    assert event.transparency == "transparent"


def test_attendee_email_and_guest_count_are_validated():
    assert EventAttendee.model_validate({"email": "alice@example.com", "additionalGuests": 1})

    with pytest.raises(ValidationError):
        EventAttendee.model_validate({"email": "not-an-email"})

    with pytest.raises(ValidationError):
        EventAttendee.model_validate({"email": "alice@example.com", "additionalGuests": -1})


def test_event_rejects_duplicate_attendees_case_insensitively():
    with pytest.raises(ValidationError):
        Event.model_validate(
            _minimal_event(
                attendees=[
                    {"email": "alice@example.com"},
                    {"email": "ALICE@example.com"},
                ]
            )
        )


def test_creator_and_organizer_are_typed_person_objects():
    event = Event.model_validate(
        _minimal_event(
            creator={"email": "creator@example.com", "displayName": "Creator"},
            organizer={"email": "organizer@example.com", "displayName": "Organizer", "self": True},
        )
    )

    assert event.creator is not None
    assert event.creator.email == "creator@example.com"
    assert event.organizer is not None
    assert event.organizer.self is True

    with pytest.raises(ValidationError):
        CalendarPerson.model_validate({"email": "not-an-email"})


def test_extended_properties_and_source_are_typed():
    event = Event.model_validate(
        _minimal_event(
            extendedProperties={
                "private": {"task_id": "task-123"},
                "shared": {"project": "Migration"},
            },
            source={"title": "Sprint planning notes"},
        )
    )

    assert event.extendedProperties is not None
    assert event.extendedProperties.private == {"task_id": "task-123"}
    assert event.source is not None
    assert event.source.title == "Sprint planning notes"

    with pytest.raises(ValidationError):
        Event.model_validate(_minimal_event(extendedProperties={"private": {"task_id": {"nested": "no"}}}))

    with pytest.raises(ValidationError):
        Event.model_validate(_minimal_event(source={"title": ""}))


def test_extended_properties_reject_unmodeled_top_level_keys():
    with pytest.raises(ValidationError):
        Event.model_validate(
            _minimal_event(
                extendedProperties={
                    "private": {"task_id": "task-123"},
                    "unexpected": {"not": "modeled"},
                }
            )
        )


def test_event_type_defaults_to_default():
    event = Event.model_validate(_minimal_event())
    assert event.eventType == "default"


def test_focus_time_event_accepts_matching_properties():
    event = Event.model_validate(
        _minimal_event(
            eventType="focusTime",
            focusTimeProperties={
                "autoDeclineMode": "declineOnlyNewConflictingInvitations",
                "chatStatus": "doNotDisturb",
                "declineMessage": "Focusing right now",
            },
        )
    )

    assert event.focusTimeProperties is not None
    assert event.focusTimeProperties.chatStatus == "doNotDisturb"


def test_event_type_rejects_missing_or_mismatched_properties():
    with pytest.raises(ValidationError):
        Event.model_validate(_minimal_event(eventType="focusTime"))

    with pytest.raises(ValidationError):
        Event.model_validate(_minimal_event(eventType="default", focusTimeProperties={"chatStatus": "available"}))


def test_working_location_properties_match_location_type():
    event = Event.model_validate(
        _minimal_event(
            eventType="workingLocation",
            workingLocationProperties={"type": "customLocation", "customLocation": {"label": "Client office"}},
        )
    )

    assert event.workingLocationProperties is not None
    assert event.workingLocationProperties.type == "customLocation"

    with pytest.raises(ValidationError):
        Event.model_validate(
            _minimal_event(eventType="workingLocation", workingLocationProperties={"type": "customLocation"})
        )


def test_working_location_home_office_is_empty_marker():
    event = Event.model_validate(
        _minimal_event(
            eventType="workingLocation",
            workingLocationProperties={"type": "homeOffice", "homeOffice": {}},
        )
    )

    assert event.workingLocationProperties is not None
    assert event.workingLocationProperties.type == "homeOffice"
    assert event.workingLocationProperties.homeOffice is not None

    with pytest.raises(ValidationError):
        Event.model_validate(
            _minimal_event(
                eventType="workingLocation",
                workingLocationProperties={"type": "homeOffice", "homeOffice": {"label": "Home"}},
            )
        )


def test_birthday_properties_validate_special_date_shape():
    Event.model_validate(_minimal_event(eventType="birthday", birthdayProperties={"type": "birthday"}))
    Event.model_validate(
        _minimal_event(eventType="birthday", birthdayProperties={"type": "anniversary", "contact": "people/c12345"})
    )

    with pytest.raises(ValidationError):
        Event.model_validate(
            _minimal_event(eventType="birthday", birthdayProperties={"type": "self", "contact": "people/c12345"})
        )

    with pytest.raises(ValidationError):
        Event.model_validate(
            _minimal_event(eventType="birthday", birthdayProperties={"type": "anniversary", "contact": "people/a"})
        )
