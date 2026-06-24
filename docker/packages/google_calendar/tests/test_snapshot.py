"""Tests for the _snapshot_on_write decorator — final.json written after every write tool call."""

import importlib
import json

import pytest


def _get_gc():
    """Import the server module lazily so tests control its module globals."""
    return importlib.import_module("google_calendar.server")


def _get_state():
    return importlib.import_module("google_calendar.state")


@pytest.fixture
def calendar_data(tmp_path):
    """Seed a minimal calendar data file."""
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
    """Patch google_calendar module globals for isolated testing."""
    state = _get_state()
    workspace = calendar_data.parent.parent / "workspace"
    workspace.mkdir()
    state.set_agent_workspace(str(workspace))
    state.set_snapshot_paths(final_path=outputdir / "final.json", bundle_state_path=None)
    yield
    state.set_agent_workspace(None)
    state.set_snapshot_paths(final_path=None, bundle_state_path=None)


@pytest.mark.asyncio
async def test_create_event_writes_final_json(outputdir):
    gc = _get_gc()
    final = outputdir / "final.json"
    assert not final.exists()

    result = await gc.create_event(
        summary="Test Event",
        start={"dateTime": "2025-06-01T10:00:00Z"},
        end={"dateTime": "2025-06-01T11:00:00Z"},
    )
    assert result["event"]["summary"] == "Test Event"
    assert final.exists(), "final.json must be written after create_event"

    snapshot = json.loads(final.read_text())
    assert len(snapshot["events"]) == 1


@pytest.mark.asyncio
async def test_update_event_writes_final_json(outputdir):
    gc = _get_gc()
    result = await gc.create_event(
        summary="Original",
        start={"dateTime": "2025-06-01T10:00:00Z"},
        end={"dateTime": "2025-06-01T11:00:00Z"},
    )
    event_id = result["event"]["id"]

    final = outputdir / "final.json"
    final.unlink()  # isolate the update's snapshot

    await gc.update_event(eventId=event_id, summary="Updated")

    assert final.exists(), "final.json must be written after update_event"
    snapshot = json.loads(final.read_text())
    assert snapshot["events"][event_id]["summary"] == "Updated"


@pytest.mark.asyncio
async def test_delete_event_writes_final_json(outputdir):
    gc = _get_gc()
    result = await gc.create_event(
        summary="To Delete",
        start={"dateTime": "2025-06-01T10:00:00Z"},
        end={"dateTime": "2025-06-01T11:00:00Z"},
    )
    event_id = result["event"]["id"]

    final = outputdir / "final.json"
    final.unlink()

    await gc.delete_event(eventId=event_id)

    assert final.exists(), "final.json must be written after delete_event"
    snapshot = json.loads(final.read_text())
    assert event_id not in snapshot["events"]


@pytest.mark.asyncio
async def test_list_events_does_not_write_final_json(outputdir):
    gc = _get_gc()
    final = outputdir / "final.json"
    await gc.list_events(timeMin="2025-01-01T00:00:00Z", timeMax="2025-12-31T23:59:59Z")

    assert not final.exists(), "final.json must NOT be written after a read-only tool"


@pytest.mark.asyncio
async def test_search_events_does_not_write_final_json(outputdir):
    gc = _get_gc()
    final = outputdir / "final.json"
    await gc.search_events(query="test")

    assert not final.exists(), "final.json must NOT be written after a read-only tool"


@pytest.mark.asyncio
async def test_no_final_path_skips_snapshot():
    gc = _get_gc()
    state = _get_state()
    state.set_snapshot_paths(final_path=None)

    result = await gc.create_event(
        summary="No Output",
        start={"dateTime": "2025-06-01T10:00:00Z"},
        end={"dateTime": "2025-06-01T11:00:00Z"},
    )
    assert result["event"]["summary"] == "No Output"
