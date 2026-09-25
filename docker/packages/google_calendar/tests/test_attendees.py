"""Tests for attendees and RSVP functionality."""

import importlib
import json

import pytest
from pydantic import EmailStr, TypeAdapter, ValidationError

from google_calendar.models import (
    AttendeeResponseStatus,
    CalendarPerson,
    EventAttendee,
    EventClearField,
    EventSource,
    EventTransparency,
    EventType,
    ExtendedProperties,
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
    data_file.write_text(json.dumps({"events": {}}))
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


class TestCreateEventWithAttendees:
    def test_create_and_update_attendees_are_schema_models(self):
        gc = _get_gc()

        assert gc.create_event.__annotations__["attendees"] == list[EventAttendee] | None
        assert gc.update_event.__annotations__["attendees"] == list[EventAttendee] | None

    def test_create_and_update_people_are_schema_models(self):
        gc = _get_gc()

        assert gc.create_event.__annotations__["creator"] == CalendarPerson | None
        assert gc.create_event.__annotations__["organizer"] == CalendarPerson | None
        assert gc.update_event.__annotations__["creator"] == CalendarPerson | None
        assert gc.update_event.__annotations__["organizer"] == CalendarPerson | None

    def test_create_and_update_metadata_are_schema_models(self):
        gc = _get_gc()

        assert gc.create_event.__annotations__["extendedProperties"] == ExtendedProperties | None
        assert gc.create_event.__annotations__["source"] == EventSource | None
        assert gc.create_event.__annotations__["transparency"] == EventTransparency | None
        assert gc.update_event.__annotations__["extendedProperties"] == ExtendedProperties | None
        assert gc.update_event.__annotations__["source"] == EventSource | None
        assert gc.update_event.__annotations__["transparency"] == EventTransparency | None
        assert gc.update_event.__annotations__["eventType"] == EventType | None
        assert gc.update_event.__annotations__["clear_fields"] == list[EventClearField] | None

    @pytest.mark.asyncio
    async def test_create_with_attendees(self):
        gc = _get_gc()
        result = await gc.create_event(
            summary="Team Sync",
            start={"dateTime": "2025-06-01T10:00:00Z"},
            end={"dateTime": "2025-06-01T11:00:00Z"},
            attendees=[
                {"email": "alice@co.com", "displayName": "Alice"},
                {"email": "bob@co.com"},
            ],
        )
        assert result["status"] == "success"
        attendees = result["event"]["attendees"]
        assert len(attendees) == 2
        assert attendees[0]["email"] == "alice@co.com"
        assert attendees[0]["responseStatus"] == "needsAction"
        assert attendees[1]["displayName"] == "bob@co.com"  # falls back to email

    @pytest.mark.asyncio
    async def test_create_preserves_full_attendee_payload(self):
        gc = _get_gc()
        result = await gc.create_event(
            summary="Team Sync",
            start={"dateTime": "2025-06-01T10:00:00Z"},
            end={"dateTime": "2025-06-01T11:00:00Z"},
            attendees=[
                {
                    "email": "room@example.com",
                    "resource": True,
                    "optional": True,
                    "comment": "Projector needed",
                    "additionalGuests": 2,
                }
            ],
        )

        attendee = result["event"]["attendees"][0]
        assert attendee["resource"] is True
        assert attendee["optional"] is True
        assert attendee["comment"] == "Projector needed"
        assert attendee["additionalGuests"] == 2
        assert attendee["displayName"] == "room@example.com"
        assert attendee["responseStatus"] == "needsAction"
        assert attendee["organizer"] is False
        assert attendee["self"] is False

    @pytest.mark.asyncio
    async def test_create_rejects_duplicate_attendees(self):
        gc = _get_gc()

        result = await gc.create_event(
            summary="Team Sync",
            start={"dateTime": "2025-06-01T10:00:00Z"},
            end={"dateTime": "2025-06-01T11:00:00Z"},
            attendees=[
                {"email": "alice@co.com"},
                {"email": "ALICE@co.com"},
            ],
        )

        assert result["status"] == "error"
        assert "Duplicate attendee email" in result["message"]

    @pytest.mark.asyncio
    async def test_create_without_attendees(self):
        gc = _get_gc()
        result = await gc.create_event(
            summary="Solo Event",
            start={"dateTime": "2025-06-01T10:00:00Z"},
            end={"dateTime": "2025-06-01T11:00:00Z"},
        )
        assert "attendees" not in result["event"]


class TestUpdateEventAttendees:
    @pytest.mark.asyncio
    async def test_add_attendees_via_update(self):
        gc = _get_gc()
        result = await gc.create_event(
            summary="Meeting",
            start={"dateTime": "2025-06-01T10:00:00Z"},
            end={"dateTime": "2025-06-01T11:00:00Z"},
        )
        event_id = result["event"]["id"]

        updated = await gc.update_event(
            eventId=event_id,
            attendees=[{"email": "carol@co.com", "displayName": "Carol"}],
        )
        assert len(updated["event"]["attendees"]) == 1

    @pytest.mark.asyncio
    async def test_update_preserves_full_attendee_payload(self):
        gc = _get_gc()
        result = await gc.create_event(
            summary="Meeting",
            start={"dateTime": "2025-06-01T10:00:00Z"},
            end={"dateTime": "2025-06-01T11:00:00Z"},
        )
        event_id = result["event"]["id"]

        updated = await gc.update_event(
            eventId=event_id,
            attendees=[
                {
                    "email": "room@example.com",
                    "resource": True,
                    "optional": True,
                    "comment": "Projector needed",
                    "additionalGuests": 2,
                }
            ],
        )

        attendee = updated["event"]["attendees"][0]
        assert attendee["resource"] is True
        assert attendee["optional"] is True
        assert attendee["comment"] == "Projector needed"
        assert attendee["additionalGuests"] == 2
        assert attendee["displayName"] == "room@example.com"
        assert attendee["responseStatus"] == "needsAction"
        assert attendee["organizer"] is False
        assert attendee["self"] is False

    @pytest.mark.asyncio
    async def test_update_rejects_duplicate_attendees(self):
        gc = _get_gc()
        result = await gc.create_event(
            summary="Meeting",
            start={"dateTime": "2025-06-01T10:00:00Z"},
            end={"dateTime": "2025-06-01T11:00:00Z"},
        )
        event_id = result["event"]["id"]

        updated = await gc.update_event(
            eventId=event_id,
            attendees=[
                {"email": "alice@co.com"},
                {"email": "ALICE@co.com"},
            ],
        )

        assert updated["status"] == "error"
        assert "Duplicate attendee email" in updated["message"]

    @pytest.mark.asyncio
    async def test_clear_attendees(self):
        gc = _get_gc()
        result = await gc.create_event(
            summary="Meeting",
            start={"dateTime": "2025-06-01T10:00:00Z"},
            end={"dateTime": "2025-06-01T11:00:00Z"},
            attendees=[{"email": "alice@co.com"}],
        )
        event_id = result["event"]["id"]

        updated = await gc.update_event(eventId=event_id, attendees=[])
        assert updated["event"]["attendees"] == []


class TestUpdateEventMetadata:
    @pytest.mark.asyncio
    async def test_update_can_clear_optional_metadata_fields(self):
        gc = _get_gc()
        result = await gc.create_event(
            summary="Annotated Event",
            start={"dateTime": "2025-06-01T10:00:00Z"},
            end={"dateTime": "2025-06-01T11:00:00Z"},
            creator={"email": "creator@example.com"},
            organizer={"email": "organizer@example.com"},
            extendedProperties={"private": {"task_id": "task-123"}},
            source={"title": "Planning notes"},
            transparency="transparent",
        )
        event_id = result["event"]["id"]

        updated = await gc.update_event(
            eventId=event_id,
            clear_fields=[
                EventClearField.CREATOR,
                EventClearField.ORGANIZER,
                EventClearField.EXTENDED_PROPERTIES,
                EventClearField.SOURCE,
                EventClearField.TRANSPARENCY,
            ],
        )

        assert updated["status"] == "success"
        for field in ["creator", "organizer", "extendedProperties", "source", "transparency"]:
            assert field not in updated["event"]

    @pytest.mark.asyncio
    async def test_update_can_switch_special_event_type_back_to_default(self):
        gc = _get_gc()
        result = await gc.create_event(
            summary="Focus Block",
            start={"dateTime": "2025-06-01T10:00:00Z"},
            end={"dateTime": "2025-06-01T11:00:00Z"},
            eventType="focusTime",
            focusTimeProperties={"chatStatus": "doNotDisturb"},
        )
        event_id = result["event"]["id"]

        updated = await gc.update_event(eventId=event_id, eventType="default")

        assert updated["status"] == "success"
        assert updated["event"]["eventType"] == "default"
        assert "focusTimeProperties" not in updated["event"]

    @pytest.mark.asyncio
    async def test_create_returns_error_for_invalid_event_type_shape(self):
        gc = _get_gc()

        result = await gc.create_event(
            summary="Focus Block",
            start={"dateTime": "2025-06-01T10:00:00Z"},
            end={"dateTime": "2025-06-01T11:00:00Z"},
            eventType="focusTime",
        )

        assert result["status"] == "error"
        assert "focusTimeProperties is required" in result["message"]

    @pytest.mark.asyncio
    async def test_update_returns_error_for_invalid_merged_event(self):
        gc = _get_gc()
        result = await gc.create_event(
            summary="All Day",
            start={"date": "2025-06-01"},
            end={"date": "2025-06-02"},
        )
        event_id = result["event"]["id"]

        updated = await gc.update_event(eventId=event_id, start={"dateTime": "2025-06-01T10:00:00Z"})

        assert updated["status"] == "error"
        assert "both use dateTime or both use date" in updated["message"]

    @pytest.mark.asyncio
    async def test_update_can_switch_default_event_to_special_type_with_properties(self):
        gc = _get_gc()
        result = await gc.create_event(
            summary="Working location",
            start={"date": "2025-06-01"},
            end={"date": "2025-06-02"},
        )
        event_id = result["event"]["id"]

        updated = await gc.update_event(
            eventId=event_id,
            eventType=EventType.WORKING_LOCATION,
            workingLocationProperties={"type": "customLocation", "customLocation": {"label": "Client office"}},
        )

        assert updated["status"] == "success"
        assert updated["event"]["eventType"] == "workingLocation"
        assert updated["event"]["workingLocationProperties"]["customLocation"]["label"] == "Client office"


class TestRespondToEvent:
    @pytest.mark.asyncio
    async def test_accept_invitation(self):
        gc = _get_gc()
        result = await gc.create_event(
            summary="Meeting",
            start={"dateTime": "2025-06-01T10:00:00Z"},
            end={"dateTime": "2025-06-01T11:00:00Z"},
            attendees=[{"email": "alice@co.com"}, {"email": "bob@co.com"}],
        )
        event_id = result["event"]["id"]

        rsvp = await gc.respond_to_event(
            eventId=event_id, email="alice@co.com", response=AttendeeResponseStatus.ACCEPTED
        )
        assert rsvp["status"] == "success"
        alice = next(a for a in rsvp["event"]["attendees"] if a["email"] == "alice@co.com")
        assert alice["responseStatus"] == "accepted"
        # Bob should still be needsAction
        bob = next(a for a in rsvp["event"]["attendees"] if a["email"] == "bob@co.com")
        assert bob["responseStatus"] == "needsAction"

    @pytest.mark.asyncio
    async def test_decline_invitation(self):
        gc = _get_gc()
        result = await gc.create_event(
            summary="Meeting",
            start={"dateTime": "2025-06-01T10:00:00Z"},
            end={"dateTime": "2025-06-01T11:00:00Z"},
            attendees=[{"email": "alice@co.com"}],
        )
        event_id = result["event"]["id"]

        rsvp = await gc.respond_to_event(
            eventId=event_id, email="alice@co.com", response=AttendeeResponseStatus.DECLINED
        )
        alice = rsvp["event"]["attendees"][0]
        assert alice["responseStatus"] == "declined"

    @pytest.mark.asyncio
    async def test_tentative_response(self):
        gc = _get_gc()
        result = await gc.create_event(
            summary="Meeting",
            start={"dateTime": "2025-06-01T10:00:00Z"},
            end={"dateTime": "2025-06-01T11:00:00Z"},
            attendees=[{"email": "alice@co.com"}],
        )
        event_id = result["event"]["id"]

        rsvp = await gc.respond_to_event(
            eventId=event_id, email="alice@co.com", response=AttendeeResponseStatus.TENTATIVE
        )
        assert rsvp["event"]["attendees"][0]["responseStatus"] == "tentative"

    def test_respond_response_is_schema_enum(self):
        gc = _get_gc()

        assert gc.respond_to_event.__annotations__["response"] == AttendeeResponseStatus
        assert gc.respond_to_event.__annotations__["email"] == EmailStr
        with pytest.raises(ValidationError):
            TypeAdapter(AttendeeResponseStatus).validate_python("maybe")

    @pytest.mark.asyncio
    async def test_respond_attendee_not_found(self):
        gc = _get_gc()
        result = await gc.create_event(
            summary="Meeting",
            start={"dateTime": "2025-06-01T10:00:00Z"},
            end={"dateTime": "2025-06-01T11:00:00Z"},
            attendees=[{"email": "alice@co.com"}],
        )
        event_id = result["event"]["id"]

        rsvp = await gc.respond_to_event(
            eventId=event_id,
            email="unknown@co.com",
            response=AttendeeResponseStatus.ACCEPTED,
        )
        assert rsvp["status"] == "error"
        assert "not found" in rsvp["message"]

    @pytest.mark.asyncio
    async def test_respond_event_not_found(self):
        gc = _get_gc()
        rsvp = await gc.respond_to_event(
            eventId="nonexistent",
            email="alice@co.com",
            response=AttendeeResponseStatus.ACCEPTED,
        )
        assert rsvp["status"] == "error"

    @pytest.mark.asyncio
    async def test_respond_case_insensitive_email(self):
        gc = _get_gc()
        result = await gc.create_event(
            summary="Meeting",
            start={"dateTime": "2025-06-01T10:00:00Z"},
            end={"dateTime": "2025-06-01T11:00:00Z"},
            attendees=[{"email": "Alice@CO.com"}],
        )
        event_id = result["event"]["id"]

        rsvp = await gc.respond_to_event(
            eventId=event_id, email="alice@co.com", response=AttendeeResponseStatus.ACCEPTED
        )
        assert rsvp["status"] == "success"


class TestAvailabilityWithAttendees:
    @pytest.mark.asyncio
    async def test_filter_by_attendee(self):
        gc = _get_gc()
        # Create event with alice as attendee
        await gc.create_event(
            summary="Alice's Meeting",
            start={"dateTime": "2025-06-01T10:00:00Z"},
            end={"dateTime": "2025-06-01T11:00:00Z"},
            attendees=[{"email": "alice@co.com", "responseStatus": "accepted"}],
        )
        # Create event with only bob
        await gc.create_event(
            summary="Bob's Meeting",
            start={"dateTime": "2025-06-01T14:00:00Z"},
            end={"dateTime": "2025-06-01T15:00:00Z"},
            attendees=[{"email": "bob@co.com", "responseStatus": "accepted"}],
        )

        # Check alice's availability — should only see alice's meeting
        result = await gc.check_availability(
            timeMin="2025-06-01T08:00:00Z",
            timeMax="2025-06-01T18:00:00Z",
            attendee_emails=["alice@co.com"],
        )
        assert len(result["busy"]) == 1
        assert "Alice's Meeting" in result["busy"][0]["events"]

    @pytest.mark.asyncio
    async def test_declined_events_not_busy(self):
        gc = _get_gc()
        result = await gc.create_event(
            summary="Optional Meeting",
            start={"dateTime": "2025-06-01T10:00:00Z"},
            end={"dateTime": "2025-06-01T11:00:00Z"},
            attendees=[{"email": "alice@co.com"}],
        )
        event_id = result["event"]["id"]

        # Alice declines
        await gc.respond_to_event(eventId=event_id, email="alice@co.com", response=AttendeeResponseStatus.DECLINED)

        # Check alice's availability — declined event should not block
        avail = await gc.check_availability(
            timeMin="2025-06-01T08:00:00Z",
            timeMax="2025-06-01T18:00:00Z",
            attendee_emails=["alice@co.com"],
        )
        assert len(avail["busy"]) == 0

    @pytest.mark.asyncio
    async def test_find_mutual_availability(self):
        gc = _get_gc()
        # Alice busy 10-11am
        await gc.create_event(
            summary="Alice Meeting",
            start={"dateTime": "2025-06-01T10:00:00Z"},
            end={"dateTime": "2025-06-01T11:00:00Z"},
            attendees=[{"email": "alice@co.com", "responseStatus": "accepted"}],
        )
        # Bob busy 2-3pm (non-adjacent so they don't merge)
        await gc.create_event(
            summary="Bob Meeting",
            start={"dateTime": "2025-06-01T14:00:00Z"},
            end={"dateTime": "2025-06-01T15:00:00Z"},
            attendees=[{"email": "bob@co.com", "responseStatus": "accepted"}],
        )

        # Check availability for both — should see both busy periods
        result = await gc.check_availability(
            timeMin="2025-06-01T08:00:00Z",
            timeMax="2025-06-01T18:00:00Z",
            attendee_emails=["alice@co.com", "bob@co.com"],
        )
        assert len(result["busy"]) == 2
        assert result["total_busy_minutes"] == 120
