"""Tests for google_calendar utility functions."""

import json
from pathlib import Path

from google_calendar.utils import get_calendar_data_path, load_events_from_json


class TestGetCalendarDataPath:
    def test_computes_external_services_path(self):
        result = get_calendar_data_path("/workspace/dumps/workspace")
        assert result == Path("/workspace/dumps/external_services/calendar_data.json")

    def test_path_is_sibling_to_workspace(self):
        result = get_calendar_data_path("/a/b/workspace")
        assert result.parent == Path("/a/b/external_services")


class TestLoadEventsFromJson:
    def test_loads_events_and_adds_timestamps(self, tmp_path):
        data = {"events": {"evt1": {"summary": "Meeting"}}}
        json_file = tmp_path / "calendar.json"
        json_file.write_text(json.dumps(data))

        result = load_events_from_json(json_file)

        assert "evt1" in result["events"]
        assert result["events"]["evt1"]["id"] == "evt1"
        assert "created" in result["events"]["evt1"]
        assert "updated" in result["events"]["evt1"]

    def test_wraps_bare_dict_in_events(self, tmp_path):
        data = {"evt1": {"summary": "Meeting"}}
        json_file = tmp_path / "calendar.json"
        json_file.write_text(json.dumps(data))

        result = load_events_from_json(json_file)
        assert "events" in result
        assert "evt1" in result["events"]

    def test_preserves_existing_timestamps(self, tmp_path):
        data = {"events": {"evt1": {"summary": "Meeting", "created": "2024-01-01", "updated": "2024-01-01"}}}
        json_file = tmp_path / "calendar.json"
        json_file.write_text(json.dumps(data))

        result = load_events_from_json(json_file)
        assert result["events"]["evt1"]["created"] == "2024-01-01"
