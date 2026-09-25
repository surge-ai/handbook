"""Tests for recurring events and reminders."""

import importlib
import json

import pytest
from pydantic import TypeAdapter, ValidationError

from google_calendar.models import EventReminders


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


# ---------------------------------------------------------------------------
# Reminders
# ---------------------------------------------------------------------------


class TestReminders:
    @pytest.mark.asyncio
    async def test_create_event_with_reminders(self):
        gc = _get_gc()
        result = await gc.create_event(
            summary="Meeting",
            start={"dateTime": "2025-06-01T10:00:00Z"},
            end={"dateTime": "2025-06-01T11:00:00Z"},
            reminders={"useDefault": False, "overrides": [{"method": "popup", "minutes": 15}]},
        )
        assert result["event"]["reminders"]["overrides"][0]["minutes"] == 15

    @pytest.mark.asyncio
    async def test_update_event_reminders(self):
        gc = _get_gc()
        result = await gc.create_event(
            summary="Meeting",
            start={"dateTime": "2025-06-01T10:00:00Z"},
            end={"dateTime": "2025-06-01T11:00:00Z"},
        )
        event_id = result["event"]["id"]

        updated = await gc.update_event(
            eventId=event_id,
            reminders={"useDefault": False, "overrides": [{"method": "email", "minutes": 30}]},
        )
        assert updated["event"]["reminders"]["overrides"][0]["method"] == "email"

    @pytest.mark.asyncio
    async def test_update_event_reverts_to_default_reminders(self):
        gc = _get_gc()
        result = await gc.create_event(
            summary="Meeting",
            start={"dateTime": "2025-06-01T10:00:00Z"},
            end={"dateTime": "2025-06-01T11:00:00Z"},
            reminders={"useDefault": False, "overrides": [{"method": "email", "minutes": 30}]},
        )
        event_id = result["event"]["id"]

        updated = await gc.update_event(eventId=event_id, reminders={"useDefault": True})

        assert updated["event"]["reminders"] == {"useDefault": True}

    @pytest.mark.asyncio
    async def test_event_without_reminders(self):
        gc = _get_gc()
        result = await gc.create_event(
            summary="No Reminders",
            start={"dateTime": "2025-06-01T10:00:00Z"},
            end={"dateTime": "2025-06-01T11:00:00Z"},
        )
        assert "reminders" not in result["event"]

    def test_reminders_are_schema_model(self):
        gc = _get_gc()

        assert gc.create_event.__annotations__["reminders"] == EventReminders | None
        assert gc.update_event.__annotations__["reminders"] == EventReminders | None

    @pytest.mark.parametrize(
        "payload",
        [
            {"useDefault": False, "overrides": [{"method": "sms", "minutes": 10}]},
            {"useDefault": False, "overrides": [{"method": "popup", "minutes": -1}]},
            {"useDefault": False, "overrides": [{"method": "popup", "minutes": 40321}]},
            {"useDefault": True, "overrides": [{"method": "popup", "minutes": 10}]},
            {"useDefault": False},
            {"useDefault": False, "overrides": []},
            {
                "useDefault": False,
                "overrides": [
                    {"method": "popup", "minutes": 1},
                    {"method": "popup", "minutes": 2},
                    {"method": "popup", "minutes": 3},
                    {"method": "popup", "minutes": 4},
                    {"method": "popup", "minutes": 5},
                    {"method": "popup", "minutes": 6},
                ],
            },
        ],
    )
    def test_invalid_reminders_are_rejected(self, payload):
        with pytest.raises(ValidationError):
            TypeAdapter(EventReminders).validate_python(payload)


# ---------------------------------------------------------------------------
# Recurring Events
# ---------------------------------------------------------------------------


class TestRecurringEvents:
    @pytest.mark.asyncio
    async def test_create_daily_recurring_event(self):
        gc = _get_gc()
        result = await gc.create_event(
            summary="Daily Standup",
            start={"dateTime": "2025-06-01T09:00:00Z"},
            end={"dateTime": "2025-06-01T09:15:00Z"},
            recurrence=["RRULE:FREQ=DAILY;COUNT=5"],
        )
        assert result["event"]["recurrence"] == ["RRULE:FREQ=DAILY;COUNT=5"]

    @pytest.mark.asyncio
    async def test_create_rejects_bad_recurrence_prefix(self):
        gc = _get_gc()

        result = await gc.create_event(
            summary="Bad recurrence",
            start={"dateTime": "2025-06-01T09:00:00Z"},
            end={"dateTime": "2025-06-01T09:15:00Z"},
            recurrence=["FREQ=DAILY;COUNT=5"],
        )

        assert result["status"] == "error"
        assert "String should match pattern" in result["message"]

    @pytest.mark.asyncio
    async def test_create_accepts_google_calendar_recurrence_exception_prefixes(self):
        gc = _get_gc()
        result = await gc.create_event(
            summary="Recurring With Exceptions",
            start={"dateTime": "2025-06-01T09:00:00Z"},
            end={"dateTime": "2025-06-01T09:15:00Z"},
            recurrence=["RRULE:FREQ=DAILY;COUNT=5", "EXDATE:20250603T090000Z", "RDATE:20250610T090000Z"],
        )

        assert result["status"] == "success"
        assert result["event"]["recurrence"] == [
            "RRULE:FREQ=DAILY;COUNT=5",
            "EXDATE:20250603T090000Z",
            "RDATE:20250610T090000Z",
        ]

    @pytest.mark.asyncio
    async def test_list_expands_daily_recurring(self):
        gc = _get_gc()
        await gc.create_event(
            summary="Daily Standup",
            start={"dateTime": "2025-06-01T09:00:00Z"},
            end={"dateTime": "2025-06-01T09:15:00Z"},
            recurrence=["RRULE:FREQ=DAILY;COUNT=5"],
        )

        result = await gc.list_events(
            timeMin="2025-06-01T00:00:00Z",
            timeMax="2025-06-10T00:00:00Z",
        )
        # Should have 5 instances (June 1-5)
        standups = [e for e in result["events"] if "Standup" in e["summary"]]
        assert len(standups) == 5

    @pytest.mark.asyncio
    async def test_list_expands_weekly_recurring(self):
        gc = _get_gc()
        await gc.create_event(
            summary="Weekly Sync",
            start={"dateTime": "2025-06-02T10:00:00Z"},  # Monday
            end={"dateTime": "2025-06-02T11:00:00Z"},
            recurrence=["RRULE:FREQ=WEEKLY;COUNT=4"],
        )

        result = await gc.list_events(
            timeMin="2025-06-01T00:00:00Z",
            timeMax="2025-07-01T00:00:00Z",
        )
        syncs = [e for e in result["events"] if "Weekly Sync" in e["summary"]]
        assert len(syncs) == 4

    @pytest.mark.asyncio
    async def test_list_expands_weekly_with_byday(self):
        gc = _get_gc()
        await gc.create_event(
            summary="Tue/Thu Meeting",
            start={"dateTime": "2025-06-03T14:00:00Z"},  # Tuesday
            end={"dateTime": "2025-06-03T15:00:00Z"},
            recurrence=["RRULE:FREQ=WEEKLY;BYDAY=TU,TH;COUNT=6"],
        )

        result = await gc.list_events(
            timeMin="2025-06-01T00:00:00Z",
            timeMax="2025-06-30T00:00:00Z",
        )
        meetings = [e for e in result["events"] if "Tue/Thu" in e["summary"]]
        assert len(meetings) == 6

    @pytest.mark.asyncio
    async def test_recurring_instances_have_unique_ids(self):
        gc = _get_gc()
        result = await gc.create_event(
            summary="Daily",
            start={"dateTime": "2025-06-01T09:00:00Z"},
            end={"dateTime": "2025-06-01T09:30:00Z"},
            recurrence=["RRULE:FREQ=DAILY;COUNT=3"],
        )
        parent_id = result["event"]["id"]

        listed = await gc.list_events(
            timeMin="2025-06-01T00:00:00Z",
            timeMax="2025-06-05T00:00:00Z",
        )
        ids = [e["id"] for e in listed["events"] if "Daily" in e["summary"]]
        # Each instance has a unique ID derived from parent
        assert len(ids) == 3
        assert len(set(ids)) == 3  # all unique
        assert all(parent_id in eid for eid in ids)

    @pytest.mark.asyncio
    async def test_recurring_instances_have_recurringEventId(self):
        gc = _get_gc()
        result = await gc.create_event(
            summary="Recurring",
            start={"dateTime": "2025-06-01T09:00:00Z"},
            end={"dateTime": "2025-06-01T09:30:00Z"},
            recurrence=["RRULE:FREQ=DAILY;COUNT=2"],
        )
        parent_id = result["event"]["id"]

        listed = await gc.list_events(
            timeMin="2025-06-01T00:00:00Z",
            timeMax="2025-06-05T00:00:00Z",
        )
        instances = [e for e in listed["events"] if "Recurring" in e["summary"]]
        for inst in instances:
            assert inst["recurringEventId"] == parent_id
            assert "recurrence" not in inst  # instances don't carry the rule

    @pytest.mark.asyncio
    async def test_recurring_instances_preserve_parent_time_zone(self):
        gc = _get_gc()
        await gc.create_event(
            summary="Recurring with time zone",
            start={"dateTime": "2025-06-01T09:00:00", "timeZone": "America/New_York"},
            end={"dateTime": "2025-06-01T09:30:00", "timeZone": "America/New_York"},
            recurrence=["RRULE:FREQ=DAILY;COUNT=2"],
        )

        listed = await gc.list_events(
            timeMin="2025-06-01T00:00:00Z",
            timeMax="2025-06-05T00:00:00Z",
        )
        instances = [e for e in listed["events"] if "Recurring with time zone" in e["summary"]]
        assert len(instances) == 2
        for inst in instances:
            assert inst["start"]["timeZone"] == "America/New_York"
            assert inst["end"]["timeZone"] == "America/New_York"

    @pytest.mark.asyncio
    async def test_recurring_instances_use_named_time_zone_across_dst(self):
        gc = _get_gc()
        await gc.create_event(
            summary="DST recurring",
            start={"dateTime": "2025-03-08T09:00:00-05:00", "timeZone": "America/New_York"},
            end={"dateTime": "2025-03-08T09:30:00-05:00", "timeZone": "America/New_York"},
            recurrence=["RRULE:FREQ=DAILY;COUNT=3"],
        )

        listed = await gc.list_events(
            timeMin="2025-03-08T00:00:00Z",
            timeMax="2025-03-12T00:00:00Z",
        )
        instances = [e for e in listed["events"] if "DST recurring" in e["summary"]]

        assert [inst["start"]["dateTime"] for inst in instances] == [
            "2025-03-08T09:00:00-05:00",
            "2025-03-09T09:00:00-04:00",
            "2025-03-10T09:00:00-04:00",
        ]
        assert {inst["start"]["timeZone"] for inst in instances} == {"America/New_York"}

    @pytest.mark.asyncio
    async def test_recurring_only_within_query_range(self):
        gc = _get_gc()
        await gc.create_event(
            summary="Daily",
            start={"dateTime": "2025-06-01T09:00:00Z"},
            end={"dateTime": "2025-06-01T09:30:00Z"},
            recurrence=["RRULE:FREQ=DAILY;COUNT=30"],
        )

        # Query only June 5-7
        result = await gc.list_events(
            timeMin="2025-06-05T00:00:00Z",
            timeMax="2025-06-08T00:00:00Z",
        )
        daily = [e for e in result["events"] if "Daily" in e["summary"]]
        assert len(daily) == 3  # June 5, 6, 7

    @pytest.mark.asyncio
    async def test_monthly_recurring(self):
        gc = _get_gc()
        await gc.create_event(
            summary="Monthly Review",
            start={"dateTime": "2025-01-15T10:00:00Z"},
            end={"dateTime": "2025-01-15T11:00:00Z"},
            recurrence=["RRULE:FREQ=MONTHLY;COUNT=6"],
        )

        result = await gc.list_events(
            timeMin="2025-01-01T00:00:00Z",
            timeMax="2025-07-01T00:00:00Z",
        )
        reviews = [e for e in result["events"] if "Monthly Review" in e["summary"]]
        assert len(reviews) == 6

    @pytest.mark.asyncio
    async def test_recurring_with_until(self):
        gc = _get_gc()
        await gc.create_event(
            summary="Until Event",
            start={"dateTime": "2025-06-01T09:00:00Z"},
            end={"dateTime": "2025-06-01T09:30:00Z"},
            recurrence=["RRULE:FREQ=DAILY;UNTIL=2025-06-04T00:00:00Z"],
        )

        result = await gc.list_events(
            timeMin="2025-06-01T00:00:00Z",
            timeMax="2025-06-10T00:00:00Z",
        )
        events = [e for e in result["events"] if "Until Event" in e["summary"]]
        assert len(events) == 3  # June 1, 2, 3 (UNTIL is exclusive-ish)

    @pytest.mark.asyncio
    async def test_remove_recurrence_via_update(self):
        gc = _get_gc()
        result = await gc.create_event(
            summary="Was Recurring",
            start={"dateTime": "2025-06-01T09:00:00Z"},
            end={"dateTime": "2025-06-01T09:30:00Z"},
            recurrence=["RRULE:FREQ=DAILY;COUNT=5"],
        )
        event_id = result["event"]["id"]

        # Remove recurrence by passing empty list
        await gc.update_event(eventId=event_id, recurrence=[])

        listed = await gc.list_events(
            timeMin="2025-06-01T00:00:00Z",
            timeMax="2025-06-10T00:00:00Z",
        )
        matching = [e for e in listed["events"] if "Was Recurring" in e["summary"]]
        assert len(matching) == 1  # Just the single event now


class TestRecurringAvailability:
    @pytest.mark.asyncio
    async def test_recurring_events_block_availability(self):
        gc = _get_gc()
        await gc.create_event(
            summary="Daily Standup",
            start={"dateTime": "2025-06-01T09:00:00Z"},
            end={"dateTime": "2025-06-01T09:15:00Z"},
            recurrence=["RRULE:FREQ=DAILY;COUNT=5"],
        )

        result = await gc.check_availability(
            timeMin="2025-06-01T08:00:00Z",
            timeMax="2025-06-01T10:00:00Z",
        )
        assert len(result["busy"]) == 1
        assert "Daily Standup" in result["busy"][0]["events"]
        assert result["total_busy_minutes"] == 15
