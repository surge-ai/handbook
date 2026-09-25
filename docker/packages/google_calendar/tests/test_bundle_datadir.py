"""Tests for the nested bundle-input layout: <BUNDLEDIR>/services/<name>/state.json."""

import importlib
import json

import pytest


def _get_state():
    return importlib.import_module("google_calendar.state")


# A minimal, valid CalendarState seed. CalendarState has all-default fields, so
# an empty events dict is valid; one event keeps the round-trip meaningful.
SEED = {
    "events": {
        "evt-1": {
            "id": "evt-1",
            "summary": "Seeded Event",
            "start": {"dateTime": "2025-06-01T10:00:00Z"},
            "end": {"dateTime": "2025-06-01T11:00:00Z"},
        }
    }
}


@pytest.fixture(autouse=True)
def _reset_state():
    """Keep state in-memory and reset the account registry around each test."""
    state = _get_state()
    state.set_agent_workspace(None)
    state._accounts.clear()
    state._active_account_id = "default"
    yield
    state.set_agent_workspace(None)
    state._accounts.clear()
    state._active_account_id = "default"


def test_resolve_bundle_state_path_prefers_state_json(tmp_path, monkeypatch):
    state = _get_state()
    service_dir = tmp_path / "services" / "google_calendar"
    service_dir.mkdir(parents=True)
    state_json = service_dir / "state.json"
    state_json.write_text(json.dumps(SEED))
    (service_dir / "events.json").write_text(json.dumps(SEED))

    monkeypatch.setenv("BUNDLEDIR", str(tmp_path))

    assert state.resolve_bundle_state_path() == state_json


def test_resolve_bundle_state_path_globs_when_no_state_json(tmp_path, monkeypatch):
    state = _get_state()
    service_dir = tmp_path / "services" / "google_calendar"
    service_dir.mkdir(parents=True)
    a_json = service_dir / "a.json"
    a_json.write_text(json.dumps(SEED))
    (service_dir / "b.json").write_text(json.dumps(SEED))

    monkeypatch.setenv("BUNDLEDIR", str(tmp_path))

    assert state.resolve_bundle_state_path() == a_json


def test_resolve_bundle_state_path_missing_subdir(tmp_path, monkeypatch):
    state = _get_state()
    (tmp_path / "services").mkdir()

    monkeypatch.setenv("BUNDLEDIR", str(tmp_path))
    assert state.resolve_bundle_state_path() is None

    monkeypatch.delenv("BUNDLEDIR", raising=False)
    assert state.resolve_bundle_state_path() is None


def test_resolve_bundle_output_path(tmp_path, monkeypatch):
    state = _get_state()
    output_dir = tmp_path / "services" / "google_calendar"

    monkeypatch.setenv("BUNDLE_OUTPUT_DIR", str(output_dir))
    assert state.resolve_bundle_output_path() == output_dir / "state.json"

    monkeypatch.delenv("BUNDLE_OUTPUT_DIR", raising=False)
    assert state.resolve_bundle_output_path() is None


def test_bundle_state_json_matches_inputdir(tmp_path, monkeypatch):
    state = _get_state()

    bundle_dir = tmp_path / "bundle"
    service_dir = bundle_dir / "services" / "google_calendar"
    service_dir.mkdir(parents=True)
    (service_dir / "state.json").write_text(json.dumps(SEED))

    inputdir = tmp_path / "input"
    inputdir.mkdir()
    (inputdir / "calendar.json").write_text(json.dumps(SEED))

    # Load from the nested bundle layout.
    state.set_agent_workspace(None)
    state._accounts.clear()
    state._active_account_id = "default"
    monkeypatch.setenv("BUNDLEDIR", str(bundle_dir))
    monkeypatch.delenv("INPUTDIR", raising=False)
    state.load_seed_state_from_env()
    bundle_state = state.state_to_json()

    # Reset and load the same seed from the INPUTDIR fallback.
    state._accounts.clear()
    state._active_account_id = "default"
    state.set_agent_workspace(None)
    monkeypatch.delenv("BUNDLEDIR", raising=False)
    monkeypatch.setenv("INPUTDIR", str(inputdir))
    state.load_seed_state_from_env()
    inputdir_state = state.state_to_json()

    assert bundle_state == inputdir_state


# Two distinguishable accounts: different seeded event so a swap would be caught.
ACCT_A = {
    "events": {
        "evt-a": {
            "id": "evt-a",
            "summary": "Account A Event",
            "start": {"dateTime": "2025-06-01T10:00:00Z"},
            "end": {"dateTime": "2025-06-01T11:00:00Z"},
        }
    }
}
ACCT_B = {
    "events": {
        "evt-b": {
            "id": "evt-b",
            "summary": "Account B Event",
            "start": {"dateTime": "2025-07-02T14:00:00Z"},
            "end": {"dateTime": "2025-07-02T15:00:00Z"},
        }
    }
}


def _load_from_bundle(state, monkeypatch, bundle_dir):
    """Reset the registry and load seed state from *bundle_dir* in isolation."""
    state.set_agent_workspace(None)
    state._accounts.clear()
    state._active_account_id = "default"
    monkeypatch.setenv("BUNDLEDIR", str(bundle_dir))
    monkeypatch.delenv("INPUTDIR", raising=False)
    monkeypatch.delenv("OUTPUTDIR", raising=False)
    monkeypatch.delenv("BUNDLE_OUTPUT_DIR", raising=False)
    state.load_seed_state_from_env()
    return state.state_to_json()


def test_bundle_multifile_folder_matches_consolidated_state(tmp_path, monkeypatch):
    """A multi-file bundle folder coalesces to the same state as a single
    consolidated state.json with the same accounts."""
    state = _get_state()

    # (a) Consolidated: one state.json with both accounts.
    consolidated = tmp_path / "consolidated"
    consolidated_dir = consolidated / "services" / "google_calendar"
    consolidated_dir.mkdir(parents=True)
    (consolidated_dir / "state.json").write_text(json.dumps({"accounts": {"default": ACCT_A, "work": ACCT_B}}))

    # (b) Split: two wrapper files, no state.json.
    split = tmp_path / "split"
    split_dir = split / "services" / "google_calendar"
    split_dir.mkdir(parents=True)
    (split_dir / "a.json").write_text(json.dumps({"accounts": {"default": ACCT_A}}))
    (split_dir / "b.json").write_text(json.dumps({"accounts": {"work": ACCT_B}}))

    consolidated_state = _load_from_bundle(state, monkeypatch, consolidated)
    split_state = _load_from_bundle(state, monkeypatch, split)

    assert consolidated_state == split_state
    assert set(consolidated_state["accounts"]) == {"default", "work"}
    assert set(split_state["accounts"]) == {"default", "work"}


def test_resolve_bundle_state_paths_returns_whole_folder(tmp_path, monkeypatch):
    """The plural resolver returns ALL sorted *.json when there's no state.json,
    and exactly [state.json] when one is present."""
    state = _get_state()
    service_dir = tmp_path / "services" / "google_calendar"
    service_dir.mkdir(parents=True)
    a_json = service_dir / "a.json"
    b_json = service_dir / "b.json"
    a_json.write_text(json.dumps({"accounts": {"default": ACCT_A}}))
    b_json.write_text(json.dumps({"accounts": {"work": ACCT_B}}))

    monkeypatch.setenv("BUNDLEDIR", str(tmp_path))

    assert state.resolve_bundle_state_paths() == [a_json, b_json]

    state_json = service_dir / "state.json"
    state_json.write_text(json.dumps({"accounts": {"default": ACCT_A}}))
    assert state.resolve_bundle_state_paths() == [state_json]


def test_bundle_flat_files_merge_into_one_account(tmp_path, monkeypatch):
    """the raw entities layout splits ONE account across per-entity files
    (e.g. events.json + calendars.json, no {accounts} wrapper). Flat files must
    merge into a single default account, not fragment into a phantom account
    per file that the server never activates."""
    state = _get_state()

    service_dir = tmp_path / "bundle" / "services" / "google_calendar"
    service_dir.mkdir(parents=True)
    # One account split into its two collection halves.
    (service_dir / "events.json").write_text(json.dumps({"events": ACCT_A["events"]}))
    (service_dir / "calendars.json").write_text(
        json.dumps(
            {
                "calendars": {
                    "team": {
                        "summary": "Team",
                        "events": {
                            "evt-team": {
                                "id": "evt-team",
                                "summary": "Team Event",
                                "start": {"dateTime": "2025-08-03T09:00:00Z"},
                                "end": {"dateTime": "2025-08-03T10:00:00Z"},
                            }
                        },
                    }
                }
            }
        )
    )

    merged = _load_from_bundle(state, monkeypatch, tmp_path / "bundle")

    # Single (default) account holding BOTH halves — events and the calendar.
    assert "accounts" not in merged, "flat per-entity files must merge into ONE account"
    assert "evt-a" in merged["events"]
    assert "team" in merged["calendars"]
