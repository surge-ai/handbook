"""Tests for multiple calendar support."""

import importlib
import json

import pytest
from pydantic import ValidationError
from starlette.testclient import TestClient

from google_calendar.models import EventTransparency, EventType


def _get_gc():
    return importlib.import_module("google_calendar.server")


def _get_state():
    return importlib.import_module("google_calendar.state")


@pytest.fixture
def calendar_data(tmp_path):
    """Seed with primary events (backward-compatible flat format)."""
    external_services = tmp_path / "external_services"
    external_services.mkdir()
    data_file = external_services / "calendar_data.json"
    data_file.write_text(
        json.dumps(
            {
                "timeZone": "America/New_York",
                "events": {
                    "evt-1": {
                        "id": "evt-1",
                        "summary": "Primary Event",
                        "start": {"dateTime": "2025-06-01T10:00:00Z"},
                        "end": {"dateTime": "2025-06-01T11:00:00Z"},
                        "created": "2025-01-01T00:00:00Z",
                        "updated": "2025-01-01T00:00:00Z",
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


class TestBackwardCompatibility:
    """Existing flat events dict works as 'primary' calendar."""

    def test_get_calendar_events_attaches_missing_primary_events_dict(self):
        state = _get_state()
        data = {}

        events = state.get_calendar_events(data, "primary")
        events["evt-1"] = {"id": "evt-1"}

        assert data["events"] is events
        assert data["events"]["evt-1"]["id"] == "evt-1"

    def test_get_calendar_events_attaches_missing_secondary_events_dict(self):
        state = _get_state()
        data = {"calendars": {"work": {"summary": "Work"}}}

        events = state.get_calendar_events(data, "work")
        events["evt-1"] = {"id": "evt-1"}

        assert data["calendars"]["work"]["events"] is events
        assert data["calendars"]["work"]["events"]["evt-1"]["id"] == "evt-1"

    @pytest.mark.asyncio
    async def test_get_event_from_primary(self):
        gc = _get_gc()
        result = await gc.get_event(eventId="evt-1")
        assert result["status"] == "success"
        assert result["event"]["summary"] == "Primary Event"

    @pytest.mark.asyncio
    async def test_get_event_with_explicit_primary(self):
        gc = _get_gc()
        result = await gc.get_event(eventId="evt-1", calendar_id="primary")
        assert result["status"] == "success"

    @pytest.mark.asyncio
    async def test_create_event_defaults_to_primary(self):
        gc = _get_gc()
        result = await gc.create_event(
            summary="New Event",
            start={"dateTime": "2025-06-02T10:00:00Z"},
            end={"dateTime": "2025-06-02T11:00:00Z"},
        )
        assert result["status"] == "success"
        # Should be in the flat events dict
        data = _get_state().load_data()
        assert result["event"]["id"] in data["events"]

    @pytest.mark.asyncio
    async def test_list_events_default_includes_primary(self):
        gc = _get_gc()
        result = await gc.list_events(
            timeMin="2025-01-01T00:00:00Z",
            timeMax="2025-12-31T23:59:59Z",
        )
        assert result["count"] >= 1
        summaries = [e["summary"] for e in result["events"]]
        assert "Primary Event" in summaries

    def test_save_data_rejects_invalid_calendar_state(self):
        state = _get_state()
        data = state.load_data()
        data["calendars"] = {"primary": {"summary": "Duplicate Primary", "events": {}}}

        with pytest.raises(ValidationError):
            state.save_data(data)

    @pytest.mark.asyncio
    async def test_write_returns_error_when_existing_state_is_invalid(self, calendar_data):
        gc = _get_gc()
        invalid_state = _get_state().load_data()
        invalid_state["calendars"] = {"primary": {"summary": "Duplicate Primary", "events": {}}}
        calendar_data.write_text(json.dumps(invalid_state))

        result = await gc.create_calendar(summary="Work")

        assert result["status"] == "error"
        assert "Primary calendar events must be stored in top-level events" in result["message"]
        persisted = json.loads(calendar_data.read_text())
        assert "work" not in persisted["calendars"]


class TestCreateCalendar:
    @pytest.mark.asyncio
    async def test_create_calendar(self):
        gc = _get_gc()
        result = await gc.create_calendar(summary="Personal")
        assert result["status"] == "success"
        assert result["calendar"]["id"] == "personal"
        assert result["calendar"]["summary"] == "Personal"
        assert result["calendar"]["primary"] is False

    @pytest.mark.asyncio
    async def test_create_calendar_with_description(self):
        gc = _get_gc()
        result = await gc.create_calendar(summary="Team Meetings", description="Shared team calendar")
        assert result["calendar"]["description"] == "Shared team calendar"

    @pytest.mark.asyncio
    async def test_create_calendar_with_timezone(self):
        gc = _get_gc()
        result = await gc.create_calendar(summary="London Office", timeZone="Europe/London")
        assert result["status"] == "success"
        assert result["calendar"]["timeZone"] == "Europe/London"

        data = _get_state().load_data()
        assert data["calendars"]["london-office"]["timeZone"] == "Europe/London"

    @pytest.mark.asyncio
    async def test_create_duplicate_calendar(self):
        gc = _get_gc()
        await gc.create_calendar(summary="Personal")
        result = await gc.create_calendar(summary="Personal")
        assert result["status"] == "error"
        assert "already exists" in result["message"]

    @pytest.mark.asyncio
    async def test_cannot_create_primary(self):
        gc = _get_gc()
        result = await gc.create_calendar(summary="Primary")
        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_cannot_create_empty_sanitized_calendar_id(self):
        gc = _get_gc()
        result = await gc.create_calendar(summary="!!!")
        assert result["status"] == "error"
        assert "letter or number" in result["message"]
        assert "" not in _get_state().load_data().get("calendars", {})


class TestListCalendars:
    @pytest.mark.asyncio
    async def test_list_includes_primary(self):
        gc = _get_gc()
        result = await gc.list_calendars()
        assert result["status"] == "success"
        assert result["count"] >= 1
        ids = [c["id"] for c in result["calendars"]]
        assert "primary" in ids
        primary = next(c for c in result["calendars"] if c["id"] == "primary")
        assert primary["timeZone"] == "America/New_York"

    @pytest.mark.asyncio
    async def test_list_includes_created_calendars(self):
        gc = _get_gc()
        await gc.create_calendar(summary="Work")
        await gc.create_calendar(summary="Personal")
        result = await gc.list_calendars()
        ids = [c["id"] for c in result["calendars"]]
        assert "work" in ids
        assert "personal" in ids
        assert result["count"] == 3  # primary + work + personal

    @pytest.mark.asyncio
    async def test_primary_shows_event_count(self):
        gc = _get_gc()
        result = await gc.list_calendars()
        primary = next(c for c in result["calendars"] if c["id"] == "primary")
        assert primary["eventCount"] == 1  # from fixture


class TestMultiCalendarEvents:
    @pytest.mark.asyncio
    async def test_create_event_in_secondary_calendar(self):
        gc = _get_gc()
        await gc.create_calendar(summary="Work")
        result = await gc.create_event(
            summary="Work Meeting",
            start={"dateTime": "2025-06-01T14:00:00Z"},
            end={"dateTime": "2025-06-01T15:00:00Z"},
            calendar_id="work",
        )
        assert result["status"] == "success"
        assert result["event"]["summary"] == "Work Meeting"

    @pytest.mark.asyncio
    async def test_get_event_from_secondary_calendar(self):
        gc = _get_gc()
        await gc.create_calendar(summary="Work")
        create_result = await gc.create_event(
            summary="Work Meeting",
            start={"dateTime": "2025-06-01T14:00:00Z"},
            end={"dateTime": "2025-06-01T15:00:00Z"},
            calendar_id="work",
        )
        event_id = create_result["event"]["id"]

        result = await gc.get_event(eventId=event_id, calendar_id="work")
        assert result["status"] == "success"
        assert result["event"]["summary"] == "Work Meeting"

    @pytest.mark.asyncio
    async def test_event_not_found_in_wrong_calendar(self):
        gc = _get_gc()
        await gc.create_calendar(summary="Work")
        create_result = await gc.create_event(
            summary="Work Meeting",
            start={"dateTime": "2025-06-01T14:00:00Z"},
            end={"dateTime": "2025-06-01T15:00:00Z"},
            calendar_id="work",
        )
        event_id = create_result["event"]["id"]

        result = await gc.get_event(eventId=event_id, calendar_id="primary")
        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_create_event_in_nonexistent_calendar(self):
        gc = _get_gc()
        result = await gc.create_event(
            summary="Orphan",
            start={"dateTime": "2025-06-01T14:00:00Z"},
            end={"dateTime": "2025-06-01T15:00:00Z"},
            calendar_id="nonexistent",
        )
        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_list_events_all_calendars(self):
        gc = _get_gc()
        await gc.create_calendar(summary="Work")
        await gc.create_event(
            summary="Work Meeting",
            start={"dateTime": "2025-06-01T14:00:00Z"},
            end={"dateTime": "2025-06-01T15:00:00Z"},
            calendar_id="work",
        )

        # List without calendar_id = all calendars
        result = await gc.list_events(
            timeMin="2025-01-01T00:00:00Z",
            timeMax="2025-12-31T23:59:59Z",
        )
        summaries = [e["summary"] for e in result["events"]]
        assert "Primary Event" in summaries
        assert "Work Meeting" in summaries

    @pytest.mark.asyncio
    async def test_list_events_preserves_duplicate_ids_across_calendars(self):
        gc = _get_gc()
        state = _get_state()
        data = state.load_data()
        data["calendars"] = {
            "work": {
                "summary": "Work",
                "events": {
                    "evt-1": {
                        "id": "evt-1",
                        "summary": "Work Event With Same ID",
                        "start": {"dateTime": "2025-06-01T12:00:00Z"},
                        "end": {"dateTime": "2025-06-01T13:00:00Z"},
                        "created": "2025-01-01T00:00:00Z",
                        "updated": "2025-01-01T00:00:00Z",
                    }
                },
            }
        }
        state.save_data(data)

        result = await gc.list_events(
            timeMin="2025-01-01T00:00:00Z",
            timeMax="2025-12-31T23:59:59Z",
        )

        summaries = [e["summary"] for e in result["events"]]
        assert "Primary Event" in summaries
        assert "Work Event With Same ID" in summaries
        assert result["count"] == 2

    def test_viewer_events_include_secondary_calendars(self):
        from google_calendar.viewer import _get_events

        state = _get_state()
        data = state.load_data()
        data["calendars"] = {
            "work": {
                "summary": "Work",
                "events": {
                    "evt-1": {
                        "id": "evt-1",
                        "summary": "Work Event With Same ID",
                        "start": {"dateTime": "2025-06-01T12:00:00Z"},
                        "end": {"dateTime": "2025-06-01T13:00:00Z"},
                        "created": "2025-01-01T00:00:00Z",
                        "updated": "2025-01-01T00:00:00Z",
                    }
                },
            }
        }
        state.save_data(data)

        events = _get_events()

        assert events["evt-1"]["summary"] == "Primary Event"
        assert events["evt-1"]["calendar_id"] == "primary"
        assert events["evt-1"]["lookup_id"] == "evt-1"
        assert events["work:evt-1"]["summary"] == "Work Event With Same ID"
        assert events["work:evt-1"]["calendar_id"] == "work"
        assert events["work:evt-1"]["lookup_id"] == "work:evt-1"

    def test_viewer_events_are_pinned_to_default_account(self):
        from google_calendar.viewer import _get_events

        state = _get_state()
        state.state_from_json(
            {
                "accounts": {
                    "default": {
                        "events": {
                            "default-event": {
                                "id": "default-event",
                                "summary": "Default Account Event",
                                "start": {"dateTime": "2025-06-01T10:00:00Z"},
                                "end": {"dateTime": "2025-06-01T11:00:00Z"},
                            }
                        }
                    },
                    "work": {
                        "events": {
                            "work-event": {
                                "id": "work-event",
                                "summary": "Work Account Event",
                                "start": {"dateTime": "2025-06-01T12:00:00Z"},
                                "end": {"dateTime": "2025-06-01T13:00:00Z"},
                            }
                        }
                    },
                }
            }
        )
        state.set_active_account("work")

        events = _get_events()

        assert "default-event" in events
        assert "work-event" not in events

    def test_viewer_events_use_first_account_when_default_is_absent(self):
        from google_calendar.viewer import _get_events

        state = _get_state()
        state.state_from_json(
            {
                "accounts": {
                    "work": {
                        "events": {
                            "work-event": {
                                "id": "work-event",
                                "summary": "Work Account Event",
                                "start": {"dateTime": "2025-06-01T12:00:00Z"},
                                "end": {"dateTime": "2025-06-01T13:00:00Z"},
                            }
                        }
                    }
                }
            }
        )

        events = _get_events()

        assert list(events) == ["work-event"]
        assert events["work-event"]["summary"] == "Work Account Event"

    def test_viewer_event_detail_uses_unique_lookup_id(self):
        from google_calendar.viewer import create_calendar_viewer_app

        state = _get_state()
        data = state.load_data()
        data["calendars"] = {
            "work": {
                "summary": "Work",
                "events": {
                    "evt-1": {
                        "id": "evt-1",
                        "summary": "Work Event With Same ID",
                        "start": {"dateTime": "2025-06-01T12:00:00Z"},
                        "end": {"dateTime": "2025-06-01T13:00:00Z"},
                        "created": "2025-01-01T00:00:00Z",
                        "updated": "2025-01-01T00:00:00Z",
                    }
                },
            }
        }
        state.save_data(data)

        client = TestClient(create_calendar_viewer_app())

        primary = client.get("/api/events/evt-1")
        work = client.get("/api/events/work%3Aevt-1")

        assert primary.status_code == 200
        assert primary.json()["event"]["summary"] == "Primary Event"
        assert primary.json()["event"]["lookup_id"] == "evt-1"
        assert work.status_code == 200
        assert work.json()["event"]["summary"] == "Work Event With Same ID"
        assert work.json()["event"]["lookup_id"] == "work:evt-1"

    def test_viewer_stats_handles_timed_events(self):
        from google_calendar.viewer import create_calendar_viewer_app

        client = TestClient(create_calendar_viewer_app(), raise_server_exceptions=False)
        response = client.get("/api/stats")

        assert response.status_code == 200
        assert response.json()["total_events"] >= 1

    @pytest.mark.asyncio
    async def test_list_events_single_calendar(self):
        gc = _get_gc()
        await gc.create_calendar(summary="Work")
        await gc.create_event(
            summary="Work Meeting",
            start={"dateTime": "2025-06-01T14:00:00Z"},
            end={"dateTime": "2025-06-01T15:00:00Z"},
            calendar_id="work",
        )

        # List only work calendar
        result = await gc.list_events(
            timeMin="2025-01-01T00:00:00Z",
            timeMax="2025-12-31T23:59:59Z",
            calendar_id="work",
        )
        assert result["count"] == 1
        assert result["events"][0]["summary"] == "Work Meeting"

    @pytest.mark.asyncio
    async def test_list_events_max_results_zero_returns_no_events(self):
        gc = _get_gc()
        result = await gc.list_events(
            timeMin="2025-01-01T00:00:00Z",
            timeMax="2025-12-31T23:59:59Z",
            maxResults=0,
        )
        assert result["status"] == "success"
        assert result["events"] == []
        assert result["count"] == 0

    @pytest.mark.asyncio
    async def test_delete_event_from_secondary_calendar(self):
        gc = _get_gc()
        await gc.create_calendar(summary="Work")
        create_result = await gc.create_event(
            summary="To Delete",
            start={"dateTime": "2025-06-01T14:00:00Z"},
            end={"dateTime": "2025-06-01T15:00:00Z"},
            calendar_id="work",
        )
        event_id = create_result["event"]["id"]

        result = await gc.delete_event(eventId=event_id, calendar_id="work")
        assert result["status"] == "success"

    @pytest.mark.asyncio
    async def test_update_event_in_secondary_calendar(self):
        gc = _get_gc()
        await gc.create_calendar(summary="Work")
        create_result = await gc.create_event(
            summary="Original",
            start={"dateTime": "2025-06-01T14:00:00Z"},
            end={"dateTime": "2025-06-01T15:00:00Z"},
            calendar_id="work",
        )
        event_id = create_result["event"]["id"]

        result = await gc.update_event(eventId=event_id, summary="Updated", calendar_id="work")
        assert result["status"] == "success"
        assert result["event"]["summary"] == "Updated"


class TestCheckAvailability:
    """Tests for free/busy availability checking."""

    @pytest.mark.asyncio
    async def test_fully_free_range(self):
        gc = _get_gc()
        # Primary has one event at 10-11am on June 1. Check June 2 = all free.
        result = await gc.check_availability(
            timeMin="2025-06-02T08:00:00Z",
            timeMax="2025-06-02T18:00:00Z",
        )
        assert result["status"] == "success"
        assert len(result["busy"]) == 0
        assert len(result["free_slots"]) == 1
        assert result["free_slots"][0]["duration_minutes"] == 600  # 10 hours

    @pytest.mark.asyncio
    async def test_busy_period_detected(self):
        gc = _get_gc()
        # Check range that includes the fixture event (10-11am June 1)
        result = await gc.check_availability(
            timeMin="2025-06-01T08:00:00Z",
            timeMax="2025-06-01T18:00:00Z",
        )
        assert len(result["busy"]) == 1
        assert "Primary Event" in result["busy"][0]["events"]

    @pytest.mark.asyncio
    async def test_transparent_events_do_not_block_availability(self):
        gc = _get_gc()
        result = await gc.create_event(
            summary="FYI hold",
            start={"dateTime": "2025-06-05T09:00:00Z"},
            end={"dateTime": "2025-06-05T10:00:00Z"},
            transparency="transparent",
        )
        assert result["event"]["transparency"] == "transparent"

        availability = await gc.check_availability(
            timeMin="2025-06-05T08:00:00Z",
            timeMax="2025-06-05T12:00:00Z",
        )
        assert availability["busy"] == []
        assert availability["total_free_minutes"] == 240

    @pytest.mark.asyncio
    async def test_from_gmail_events_default_transparent_and_do_not_block_availability(self):
        gc = _get_gc()
        result = await gc.create_event(
            summary="Flight to Denver",
            start={"dateTime": "2025-06-05T09:00:00Z"},
            end={"dateTime": "2025-06-05T10:00:00Z"},
            eventType=EventType.FROM_GMAIL,
        )
        assert result["event"]["transparency"] == "transparent"

        availability = await gc.check_availability(
            timeMin="2025-06-05T08:00:00Z",
            timeMax="2025-06-05T12:00:00Z",
        )
        assert availability["busy"] == []

    @pytest.mark.asyncio
    async def test_update_event_can_mark_event_transparent(self):
        gc = _get_gc()
        result = await gc.create_event(
            summary="Optional office hours",
            start={"dateTime": "2025-06-05T10:00:00Z"},
            end={"dateTime": "2025-06-05T11:00:00Z"},
        )

        updated = await gc.update_event(eventId=result["event"]["id"], transparency=EventTransparency.TRANSPARENT)
        assert updated["event"]["transparency"] == "transparent"

        availability = await gc.check_availability(
            timeMin="2025-06-05T08:00:00Z",
            timeMax="2025-06-05T12:00:00Z",
        )
        assert availability["busy"] == []

    @pytest.mark.asyncio
    async def test_free_slots_around_event(self):
        gc = _get_gc()
        result = await gc.check_availability(
            timeMin="2025-06-01T08:00:00Z",
            timeMax="2025-06-01T18:00:00Z",
            duration_minutes=30,
        )
        # Should have free slot before (8-10am) and after (11am-6pm) the event
        assert len(result["free_slots"]) == 2
        assert result["free_slots"][0]["duration_minutes"] == 120  # 8am-10am
        assert result["free_slots"][1]["duration_minutes"] == 420  # 11am-6pm

    @pytest.mark.asyncio
    async def test_duration_filter(self):
        gc = _get_gc()
        # Create two events close together, leaving only a 30min gap
        await gc.create_event(
            summary="Meeting 1",
            start={"dateTime": "2025-06-03T09:00:00Z"},
            end={"dateTime": "2025-06-03T10:00:00Z"},
        )
        await gc.create_event(
            summary="Meeting 2",
            start={"dateTime": "2025-06-03T10:30:00Z"},
            end={"dateTime": "2025-06-03T11:30:00Z"},
        )
        # Ask for 60min slots — the 30min gap should be filtered out
        result = await gc.check_availability(
            timeMin="2025-06-03T08:00:00Z",
            timeMax="2025-06-03T12:00:00Z",
            duration_minutes=60,
        )
        free_durations = [s["duration_minutes"] for s in result["free_slots"]]
        assert 30 not in free_durations  # 30min gap filtered
        assert 60 in free_durations  # 8-9am slot

    @pytest.mark.asyncio
    async def test_check_specific_calendar(self):
        gc = _get_gc()
        await gc.create_calendar(summary="Work")
        await gc.create_event(
            summary="Work Meeting",
            start={"dateTime": "2025-06-01T14:00:00Z"},
            end={"dateTime": "2025-06-01T15:00:00Z"},
            calendar_id="work",
        )

        # Check only work calendar — should not see primary event
        result = await gc.check_availability(
            timeMin="2025-06-01T08:00:00Z",
            timeMax="2025-06-01T18:00:00Z",
            calendar_id="work",
        )
        assert len(result["busy"]) == 1
        assert "Work Meeting" in result["busy"][0]["events"]

    @pytest.mark.asyncio
    async def test_check_availability_preserves_duplicate_ids_across_calendars(self):
        gc = _get_gc()
        state = _get_state()
        data = state.load_data()
        data["calendars"] = {
            "work": {
                "summary": "Work",
                "events": {
                    "evt-1": {
                        "id": "evt-1",
                        "summary": "Work Event With Same ID",
                        "start": {"dateTime": "2025-06-01T12:00:00Z"},
                        "end": {"dateTime": "2025-06-01T13:00:00Z"},
                        "created": "2025-01-01T00:00:00Z",
                        "updated": "2025-01-01T00:00:00Z",
                    }
                },
            }
        }
        state.save_data(data)

        result = await gc.check_availability(
            timeMin="2025-06-01T08:00:00Z",
            timeMax="2025-06-01T18:00:00Z",
        )

        busy_event_names = [name for period in result["busy"] for name in period["events"]]
        assert "Primary Event" in busy_event_names
        assert "Work Event With Same ID" in busy_event_names

    @pytest.mark.asyncio
    async def test_overlapping_events_merged(self):
        gc = _get_gc()
        await gc.create_event(
            summary="A",
            start={"dateTime": "2025-06-04T09:00:00Z"},
            end={"dateTime": "2025-06-04T10:30:00Z"},
        )
        await gc.create_event(
            summary="B",
            start={"dateTime": "2025-06-04T10:00:00Z"},
            end={"dateTime": "2025-06-04T11:00:00Z"},
        )
        result = await gc.check_availability(
            timeMin="2025-06-04T08:00:00Z",
            timeMax="2025-06-04T12:00:00Z",
        )
        # A and B overlap, so should merge into one busy period
        assert len(result["busy"]) == 1
        assert result["total_busy_minutes"] == 120  # 9am-11am

    @pytest.mark.asyncio
    async def test_totals_correct(self):
        gc = _get_gc()
        result = await gc.check_availability(
            timeMin="2025-06-01T08:00:00Z",
            timeMax="2025-06-01T18:00:00Z",
        )
        assert result["total_busy_minutes"] + result["total_free_minutes"] == 600  # 10 hours


class TestMultiAccountSupport:
    @pytest.mark.asyncio
    async def test_list_accounts_reports_default_for_flat_state(self):
        gc = _get_gc()

        result = await gc.list_accounts()

        assert result["status"] == "success"
        assert result["count"] == 1
        assert result["accounts"][0]["account_id"] == "default"
        assert result["accounts"][0]["event_count"] == 1

    @pytest.mark.asyncio
    async def test_multi_account_state_routes_reads_and_writes_by_account(self):
        gc = _get_gc()
        state = _get_state()
        state.state_from_json(
            {
                "accounts": {
                    "default": {
                        "events": {
                            "default-event": {
                                "id": "default-event",
                                "summary": "Default Account Event",
                                "start": {"dateTime": "2025-06-01T10:00:00Z"},
                                "end": {"dateTime": "2025-06-01T11:00:00Z"},
                            }
                        }
                    },
                    "work": {
                        "events": {
                            "work-event": {
                                "id": "work-event",
                                "summary": "Work Account Event",
                                "start": {"dateTime": "2025-06-01T12:00:00Z"},
                                "end": {"dateTime": "2025-06-01T13:00:00Z"},
                            }
                        }
                    },
                }
            }
        )

        accounts = await gc.list_accounts()
        assert {account["account_id"] for account in accounts["accounts"]} == {"default", "work"}

        default_result = await gc.get_event(eventId="default-event")
        assert default_result["status"] == "success"
        assert default_result["event"]["summary"] == "Default Account Event"

        work_result = await gc.get_event(eventId="work-event", account_id="work")
        assert work_result["status"] == "success"
        assert work_result["event"]["summary"] == "Work Account Event"

        missing_cross_account = await gc.get_event(eventId="work-event", account_id="default")
        assert missing_cross_account["status"] == "error"

        created = await gc.create_event(
            summary="Work Follow-up",
            start={"dateTime": "2025-06-02T10:00:00Z"},
            end={"dateTime": "2025-06-02T11:00:00Z"},
            account_id="work",
        )
        assert created["status"] == "success"

        exported = state.state_to_json()
        created_id = created["event"]["id"]
        assert created_id in exported["accounts"]["work"]["events"]
        assert created_id not in exported["accounts"]["default"]["events"]

    @pytest.mark.asyncio
    async def test_search_and_availability_respect_account_id(self):
        gc = _get_gc()
        state = _get_state()
        state.state_from_json(
            {
                "accounts": {
                    "default": {
                        "events": {
                            "default-event": {
                                "id": "default-event",
                                "summary": "Shared Planning",
                                "start": {"dateTime": "2025-06-01T10:00:00Z"},
                                "end": {"dateTime": "2025-06-01T11:00:00Z"},
                            }
                        }
                    },
                    "work": {
                        "events": {
                            "work-event": {
                                "id": "work-event",
                                "summary": "Shared Planning",
                                "start": {"dateTime": "2025-06-01T12:00:00Z"},
                                "end": {"dateTime": "2025-06-01T13:00:00Z"},
                            }
                        }
                    },
                }
            }
        )

        default_search = await gc.search_events(query="shared planning")
        work_search = await gc.search_events(query="shared planning", account_id="work")
        assert [event["id"] for event in default_search["events"]] == ["default-event"]
        assert [event["id"] for event in work_search["events"]] == ["work-event"]

        default_availability = await gc.check_availability(
            timeMin="2025-06-01T09:00:00Z",
            timeMax="2025-06-01T14:00:00Z",
            account_id="default",
        )
        work_availability = await gc.check_availability(
            timeMin="2025-06-01T09:00:00Z",
            timeMax="2025-06-01T14:00:00Z",
            account_id="work",
        )
        assert default_availability["busy"][0]["events"] == ["Shared Planning"]
        assert default_availability["busy"][0]["start"] == "2025-06-01T10:00:00+00:00"
        assert work_availability["busy"][0]["events"] == ["Shared Planning"]
        assert work_availability["busy"][0]["start"] == "2025-06-01T12:00:00+00:00"

    @pytest.mark.asyncio
    async def test_failed_write_in_one_account_leaves_other_accounts_untouched(self):
        gc = _get_gc()
        state = _get_state()
        state.state_from_json(
            {
                "accounts": {
                    "default": {"events": {}},
                    "work": {
                        "events": {
                            "work-event": {
                                "id": "work-event",
                                "summary": "Work Event",
                                "start": {"dateTime": "2025-06-01T10:00:00Z"},
                                "end": {"dateTime": "2025-06-01T11:00:00Z"},
                            }
                        }
                    },
                }
            }
        )

        result = await gc.create_event(
            summary="Broken Event",
            start={"dateTime": "2025-06-02T11:00:00Z"},
            end={"dateTime": "2025-06-02T10:00:00Z"},
        )

        exported = state.state_to_json()
        assert result["status"] == "error"
        assert exported["accounts"]["default"]["events"] == {}
        assert exported["accounts"]["work"]["events"]["work-event"]["summary"] == "Work Event"

    @pytest.mark.asyncio
    async def test_state_from_json_resets_active_account_to_default(self):
        gc = _get_gc()
        state = _get_state()
        state.state_from_json({"accounts": {"default": {"events": {}}, "work": {"events": {}}}})
        await gc.create_event(
            summary="Work Event",
            start={"dateTime": "2025-06-01T10:00:00Z"},
            end={"dateTime": "2025-06-01T11:00:00Z"},
            account_id="work",
        )
        assert state.get_active_account_id() == "work"

        state.state_from_json({"accounts": {"default": {"events": {}}, "personal": {"events": {}}}})

        assert state.get_active_account_id() == "default"
        result = await gc.create_event(
            summary="Default Event",
            start={"dateTime": "2025-06-02T10:00:00Z"},
            end={"dateTime": "2025-06-02T11:00:00Z"},
        )
        exported = state.state_to_json()
        assert result["status"] == "success"
        assert result["event"]["id"] in exported["accounts"]["default"]["events"]
        assert exported["accounts"]["personal"]["events"] == {}

    @pytest.mark.asyncio
    async def test_omitted_account_uses_first_loaded_account_when_default_is_absent(self):
        gc = _get_gc()
        state = _get_state()
        state.state_from_json(
            {
                "accounts": {
                    "work": {
                        "events": {
                            "work-event": {
                                "id": "work-event",
                                "summary": "Work Account Event",
                                "start": {"dateTime": "2025-06-01T12:00:00Z"},
                                "end": {"dateTime": "2025-06-01T13:00:00Z"},
                            }
                        }
                    }
                }
            }
        )

        result = await gc.get_event(eventId="work-event")

        assert state.get_active_account_id() == "work"
        assert result["status"] == "success"
        assert result["event"]["summary"] == "Work Account Event"

    def test_ensure_loaded_uses_first_persisted_account_when_default_is_absent(self, calendar_data):
        state = _get_state()
        workspace = calendar_data.parent.parent / "workspace"
        calendar_data.write_text(json.dumps({"accounts": {"work": {"events": {}}}}))
        state.set_agent_workspace(str(workspace))

        assert state.load_data() == {"events": {}}
        assert state.get_active_account_id() == "work"

    def test_no_workspace_mode_stays_in_memory(self, tmp_path, monkeypatch):
        state = _get_state()
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("AGENT_WORKSPACE", raising=False)
        state.set_agent_workspace(None)

        state.state_from_json({"events": {}})
        state.save_data({"events": {}})

        assert not (tmp_path / "calendar_data.json").exists()

    @pytest.mark.asyncio
    async def test_multi_account_snapshot_preserves_wrapper(self, outputdir):
        gc = _get_gc()
        state = _get_state()
        state.state_from_json({"accounts": {"default": {"events": {}}, "personal": {"events": {}}}})

        result = await gc.create_event(
            summary="Personal Event",
            start={"dateTime": "2025-06-02T10:00:00Z"},
            end={"dateTime": "2025-06-02T11:00:00Z"},
            account_id="personal",
        )
        assert result["status"] == "success"

        snapshot = json.loads((outputdir / "final.json").read_text())
        assert set(snapshot["accounts"]) == {"default", "personal"}
        assert result["event"]["id"] in snapshot["accounts"]["personal"]["events"]
