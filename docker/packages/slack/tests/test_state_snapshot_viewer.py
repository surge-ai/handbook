from __future__ import annotations

import json
from pathlib import Path

import pytest
from helpers import seed_state
from starlette.testclient import TestClient

from slack_mock import state as slack_state
from slack_mock.server import create_channel, upload_file
from slack_mock.state import (
    delete_message,
    dump_state,
    generate_timestamp,
    get_channel_messages,
    get_state,
    get_thread_replies,
    load_state,
    merge_inputdir_files,
    resolve_bundle_output_path,
    resolve_bundle_state_path,
    resolve_bundle_state_paths,
    resolve_input_paths,
    set_snapshot_paths,
    state_from_json,
    state_to_json,
    write_snapshots,
)
from slack_mock.viewer import create_slack_viewer_app


def test_resolve_input_paths(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "b.json").write_text("{}", encoding="utf-8")
    (tmp_path / "a.json").write_text("{}", encoding="utf-8")
    (tmp_path / "readme.txt").write_text("hello", encoding="utf-8")
    monkeypatch.setenv("INPUTDIR", str(tmp_path))
    assert resolve_input_paths() == [tmp_path / "a.json", tmp_path / "b.json"]

    monkeypatch.delenv("INPUTDIR")
    assert resolve_input_paths() == []


def test_resolve_bundle_output_path(monkeypatch) -> None:
    monkeypatch.setenv("BUNDLE_OUTPUT_DIR", "/some/output/services/slack")
    assert resolve_bundle_output_path() == Path("/some/output/services/slack/state.json")
    monkeypatch.delenv("BUNDLE_OUTPUT_DIR")
    assert resolve_bundle_output_path() is None


def test_resolve_bundle_state_path_prefers_state_json(tmp_path: Path, monkeypatch) -> None:
    """state.json wins over a bare *.json sibling in the per-service subdir."""
    service_dir = tmp_path / "services" / "slack"
    service_dir.mkdir(parents=True)
    (service_dir / "state.json").write_text("{}", encoding="utf-8")
    (service_dir / "channels.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("BUNDLEDIR", str(tmp_path))
    assert resolve_bundle_state_path() == service_dir / "state.json"


def test_resolve_bundle_state_path_globs_when_no_state_json(tmp_path: Path, monkeypatch) -> None:
    """The singular back-compat accessor returns the first *.json when there's
    no state.json. (The loader itself reads the whole folder — see
    resolve_bundle_state_paths.)"""
    service_dir = tmp_path / "services" / "slack"
    service_dir.mkdir(parents=True)
    (service_dir / "b.json").write_text("{}", encoding="utf-8")
    (service_dir / "a.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("BUNDLEDIR", str(tmp_path))
    assert resolve_bundle_state_path() == service_dir / "a.json"


def test_resolve_bundle_state_path_missing_subdir(tmp_path: Path, monkeypatch) -> None:
    """A partial bundle without this service's subdir resolves to None so the
    loader falls back to INPUTDIR."""
    (tmp_path / "services").mkdir()
    monkeypatch.setenv("BUNDLEDIR", str(tmp_path))
    assert resolve_bundle_state_path() is None
    monkeypatch.delenv("BUNDLEDIR")
    assert resolve_bundle_state_path() is None


def test_resolve_bundle_state_paths_returns_whole_folder(tmp_path: Path, monkeypatch) -> None:
    """Without state.json the resolver returns ALL *.json (the folder is the
    unit), not just the first — that's what the loader coalesces."""
    service_dir = tmp_path / "services" / "slack"
    service_dir.mkdir(parents=True)
    (service_dir / "b.json").write_text("{}", encoding="utf-8")
    (service_dir / "a.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("BUNDLEDIR", str(tmp_path))
    assert resolve_bundle_state_paths() == [service_dir / "a.json", service_dir / "b.json"]
    # state.json present → just the canonical single file.
    (service_dir / "state.json").write_text("{}", encoding="utf-8")
    assert resolve_bundle_state_paths() == [service_dir / "state.json"]


def _reset_slack_state() -> None:
    slack_state._current_state = None
    slack_state._workspaces.clear()
    slack_state._active_workspace_id = "default"


def test_bundle_multifile_folder_matches_consolidated_state(tmp_path: Path, monkeypatch) -> None:
    """A folder of per-entity *.json (no state.json) loads to the SAME merged
    state as the single consolidated file — no file is dropped."""
    monkeypatch.delenv("INPUTDIR", raising=False)
    monkeypatch.delenv("OUTPUTDIR", raising=False)
    monkeypatch.delenv("BUNDLE_OUTPUT_DIR", raising=False)

    user = {
        "id": "U1",
        "name": "alice",
        "real_name": "Alice",
        "profile": {"display_name": "alice", "status_text": "", "status_emoji": ""},
        "is_bot": False,
        "deleted": False,
    }
    channel = {"id": "C1", "name": "general", "context_team_id": "T_MOCK"}

    # (a) One consolidated state.json.
    consolidated_dir = tmp_path / "consolidated" / "services" / "slack"
    consolidated_dir.mkdir(parents=True)
    (consolidated_dir / "state.json").write_text(
        json.dumps({"users": {"U1": user}, "channels": {"C1": channel}}), encoding="utf-8"
    )

    # (b) The same seed split across per-entity files (no state.json).
    split_dir = tmp_path / "split" / "services" / "slack"
    split_dir.mkdir(parents=True)
    (split_dir / "users.json").write_text(json.dumps({"users": {"U1": user}}), encoding="utf-8")
    (split_dir / "channels.json").write_text(json.dumps({"channels": {"C1": channel}}), encoding="utf-8")

    monkeypatch.setenv("BUNDLEDIR", str(tmp_path / "consolidated"))
    _reset_slack_state()
    load_state()
    consolidated = state_to_json()

    monkeypatch.setenv("BUNDLEDIR", str(tmp_path / "split"))
    _reset_slack_state()
    load_state()
    split = state_to_json()

    assert split == consolidated
    assert "U1" in split["users"]
    assert "C1" in split["channels"]


def test_set_snapshot_paths_supports_partial_updates(tmp_path: Path) -> None:
    seed_state({})
    final = tmp_path / "final.json"
    bundle = tmp_path / "services" / "slack" / "state.json"

    set_snapshot_paths(final_path=final)
    write_snapshots()
    assert final.exists()
    assert not bundle.exists()

    set_snapshot_paths(bundle_state_path=bundle)
    write_snapshots()
    assert final.exists()
    assert bundle.exists()

    final.unlink()
    set_snapshot_paths(final_path=None)
    write_snapshots()
    assert not final.exists()
    assert bundle.exists()


def test_dump_state_writes_overwrites_and_creates_parent_dirs(tmp_path: Path) -> None:
    seed_state({})
    dest = tmp_path / "nested" / "dir" / "final.json"
    dump_state(dest)
    assert dest.exists()
    snapshot = json.loads(dest.read_text(encoding="utf-8"))
    assert {"users", "channels", "messages"}.issubset(snapshot)
    first = dest.read_text(encoding="utf-8")
    dump_state(dest)
    assert dest.read_text(encoding="utf-8") == first

    legacy = tmp_path / "final.json"
    bundle = tmp_path / "services" / "slack" / "state.json"
    dump_state(legacy)
    dump_state(bundle)
    assert legacy.read_text(encoding="utf-8") == bundle.read_text(encoding="utf-8")


def test_delete_message_cascades_thread_replies() -> None:
    seed_state(
        {
            "channels": {"C1": {"id": "C1", "name": "general", "context_team_id": "T_MOCK"}},
            "messages": {
                "C1": [
                    {"ts": "1700000001.000", "text": "parent", "user": "U_MOCK_BOT", "type": "message"},
                    {
                        "ts": "1700000002.000",
                        "text": "reply",
                        "user": "U_MOCK_BOT",
                        "type": "message",
                        "thread_ts": "1700000001.000",
                    },
                    {"ts": "1700000003.000", "text": "unrelated", "user": "U_MOCK_BOT", "type": "message"},
                ]
            },
        }
    )
    assert delete_message("C1", "1700000001.000") is True
    assert [message.ts for message in get_channel_messages("C1")] == ["1700000003.000"]
    assert get_thread_replies("C1", "1700000001.000") == []

    seed_state(
        {
            "channels": {"C1": {"id": "C1", "name": "general", "context_team_id": "T_MOCK"}},
            "messages": {
                "C1": [
                    {"ts": "1700000001.000", "text": "parent", "user": "U_MOCK_BOT", "type": "message"},
                    {
                        "ts": "1700000002.000",
                        "text": "reply",
                        "user": "U_MOCK_BOT",
                        "type": "message",
                        "thread_ts": "1700000001.000",
                    },
                    {"ts": "1700000003.000", "text": "unrelated", "user": "U_MOCK_BOT", "type": "message"},
                ]
            },
        }
    )
    assert delete_message("C1", "1700000003.000") is True
    assert [message.ts for message in get_channel_messages("C1")] == ["1700000001.000", "1700000002.000"]
    assert delete_message("C1", "1700000002.000") is True
    assert [message.ts for message in get_channel_messages("C1")] == ["1700000001.000"]


@pytest.mark.asyncio
async def test_file_id_persistence() -> None:
    seed_state(
        {"channels": {"C1": {"id": "C1", "name": "general", "context_team_id": "T_MOCK"}}, "messages": {"C1": []}}
    )
    first = await upload_file("C1", "a.txt", "YQ==")
    snapshot = get_state().model_dump(mode="json", exclude_none=True)
    state_from_json(snapshot)
    second = await upload_file("C1", "b.txt", "Yg==")
    assert second["file"]["id"] != first["file"]["id"]

    state_from_json(
        {
            "bot_user_id": "U_MOCK_BOT",
            "users": {"U_MOCK_BOT": {"id": "U_MOCK_BOT", "name": "bot"}},
            "channels": {"C1": {"id": "C1", "name": "general", "context_team_id": "T_MOCK"}},
            "messages": {
                "C1": [
                    {
                        "ts": "1700000001.000",
                        "text": "old",
                        "user": "U_MOCK_BOT",
                        "type": "message",
                        "files": [{"id": "F050000", "name": "old.txt", "created": 1, "timestamp": 1}],
                    }
                ]
            },
        }
    )
    assert get_state().counters.fileId == 50000
    next_file = await upload_file("C1", "next.txt", "Yg==")
    assert int(next_file["file"]["id"].replace("F", "")) > 50000


@pytest.mark.asyncio
async def test_channel_id_persistence() -> None:
    state_from_json(
        {
            "bot_user_id": "U_MOCK_BOT",
            "users": {"U_MOCK_BOT": {"id": "U_MOCK_BOT", "name": "bot"}},
            "channels": {"C050000": {"id": "C050000", "name": "general", "context_team_id": "T_MOCK"}},
            "messages": {"C050000": []},
        }
    )
    assert get_state().counters.channelId == 50001
    next_channel = await create_channel("next")
    assert int(next_channel["channel"]["id"].replace("C", "")) > 50000


def test_generated_timestamps_are_monotonic_after_seeded_state(monkeypatch) -> None:
    state_from_json(
        {
            "bot_user_id": "U_MOCK_BOT",
            "users": {"U_MOCK_BOT": {"id": "U_MOCK_BOT", "name": "bot"}},
            "channels": {"C1": {"id": "C1", "name": "general", "context_team_id": "T_MOCK"}},
            "messages": {
                "C1": [
                    {
                        "ts": "2000000000.999999",
                        "text": "seeded future message",
                        "user": "U_MOCK_BOT",
                        "type": "message",
                    }
                ]
            },
        }
    )
    monkeypatch.setattr("slack_mock.state.time.time", lambda: 1_700_000_001.123456)

    generated = [generate_timestamp() for _ in range(5)]

    assert generated == [
        "2000000001.000000",
        "2000000001.000001",
        "2000000001.000002",
        "2000000001.000003",
        "2000000001.000004",
    ]


def test_state_import_replaces_timestamp_cursor(monkeypatch) -> None:
    state_from_json(
        {
            "channels": {"C1": {"id": "C1", "name": "general", "context_team_id": "T_MOCK"}},
            "messages": {
                "C1": [{"ts": "2000000000.000000", "text": "future", "user": "U_MOCK_BOT", "type": "message"}]
            },
        }
    )
    state_from_json(
        {
            "channels": {"C1": {"id": "C1", "name": "general", "context_team_id": "T_MOCK"}},
            "messages": {"C1": [{"ts": "1700000001.000000", "text": "older", "user": "U_MOCK_BOT", "type": "message"}]},
        }
    )
    monkeypatch.setattr("slack_mock.state.time.time", lambda: 1_700_000_001.123456)

    assert generate_timestamp() == "1700000001.123456"


def test_merge_inputdir_files_combines_workspace_shaped_files(tmp_path: Path) -> None:
    first = tmp_path / "a.json"
    second = tmp_path / "b.json"
    first.write_text(
        json.dumps(
            {
                "workspaces": {
                    "acme": {
                        "channels": {"C1": {"id": "C1", "name": "general", "context_team_id": "T_MOCK"}},
                        "messages": {},
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    second.write_text(
        json.dumps(
            {
                "workspaces": {
                    "globex": {
                        "channels": {"C2": {"id": "C2", "name": "ops", "context_team_id": "T_MOCK"}},
                        "messages": {},
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    merged = merge_inputdir_files([first, second])

    assert set(merged["workspaces"]) == {"acme", "globex"}


def seed_viewer_state() -> None:
    seed_state(
        {
            "channels": {
                "C001": {
                    "id": "C001",
                    "name": "general",
                    "topic": {"value": "General chat"},
                    "purpose": {"value": "Team comms"},
                    "is_private": False,
                    "is_archived": False,
                    "num_members": 5,
                },
                "C002": {
                    "id": "C002",
                    "name": "random",
                    "topic": {"value": ""},
                    "purpose": {"value": ""},
                    "is_private": False,
                    "is_archived": False,
                    "num_members": 3,
                },
            },
            "messages": {
                "C001": [
                    {
                        "ts": "1700000001.000",
                        "text": "Hello world",
                        "user": "U001",
                        "type": "message",
                        "reply_count": 1,
                        "reactions": [{"name": "wave", "users": ["U002", "U001", "U_MOCK_BOT"], "count": 3}],
                    },
                    {
                        "ts": "1700000002.000",
                        "text": "Reply in thread",
                        "user": "U002",
                        "type": "message",
                        "thread_ts": "1700000001.000",
                    },
                    {
                        "ts": "1700000003.000",
                        "text": "Another message",
                        "user": "U001",
                        "type": "message",
                        "files": [{"id": "F100", "name": "report.txt", "mimetype": "text/plain", "size": 11}],
                    },
                ],
                "C002": [],
            },
            "users": {
                "U001": {
                    "id": "U001",
                    "name": "alice",
                    "real_name": "Alice Smith",
                    "profile": {
                        "display_name": "alice",
                        "title": "Engineer",
                        "email": "alice@test.com",
                        "status_text": "Coding",
                        "status_emoji": ":computer:",
                    },
                    "is_bot": False,
                    "deleted": False,
                },
                "U002": {
                    "id": "U002",
                    "name": "bob",
                    "real_name": "Bob Jones",
                    "profile": {
                        "display_name": "bob",
                        "title": "Manager",
                        "email": "bob@test.com",
                        "status_text": "",
                        "status_emoji": "",
                    },
                    "is_bot": False,
                    "deleted": False,
                },
            },
        }
    )


def test_viewer_api() -> None:
    seed_viewer_state()
    client = TestClient(create_slack_viewer_app())

    channels = client.get("/api/channels")
    assert channels.status_code == 200
    assert [channel["name"] for channel in channels.json()["channels"]] == ["general", "random"]
    general = next(channel for channel in channels.json()["channels"] if channel["id"] == "C001")
    assert general["messageCount"] == 3

    messages = client.get("/api/channels/C001/messages")
    assert messages.status_code == 200
    assert messages.json()["channel"]["name"] == "general"
    assert len(messages.json()["messages"]) >= 1
    first_message = messages.json()["messages"][0]
    assert {"text", "time", "user_name", "has_thread"}.issubset(first_message)
    assert client.get("/api/channels/NOPE/messages").status_code == 404
    assert len(client.get("/api/channels/C001/messages?limit=1").json()["messages"]) <= 1

    thread = client.get("/api/threads/C001/1700000001.000")
    assert thread.status_code == 200
    assert isinstance(thread.json()["messages"], list)

    users = client.get("/api/users")
    assert users.status_code == 200
    assert len(users.json()["users"]) == 3
    alice = next(user for user in users.json()["users"] if user["id"] == "U001")
    assert alice["display_name"] == "alice"
    assert alice["title"] == "Engineer"


def test_viewer_serves_html_index() -> None:
    seed_viewer_state()
    client = TestClient(create_slack_viewer_app())

    response = client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    # Body is loaded from the sibling viewer.html asset, not inlined in the module.
    assert "<title>Slack</title>" in response.text


def test_viewer_surfaces_message_file_metadata() -> None:
    seed_viewer_state()
    client = TestClient(create_slack_viewer_app())

    messages = client.get("/api/channels/C001/messages").json()["messages"]
    with_files = next(message for message in messages if message.get("files"))
    file_obj = with_files["files"][0]
    assert file_obj["name"] == "report.txt"
    assert file_obj["size"] == 11
    # Only display metadata is surfaced; stored bytes are never exposed.
    assert "content_base64" not in file_obj

    home = client.get("/")
    assert home.status_code == 200
    assert "html" in home.headers["content-type"]
    assert "channel-list" in home.text
    assert "message-pane" in home.text


def test_viewer_derives_thread_count_when_reply_metadata_missing() -> None:
    # Hand-authored seed: parent has replies but omits reply_count (as the
    # sample bundle's C004 thread does). The viewer must still surface the
    # thread so the reply -- filtered out of the main list -- stays reachable.
    seed_state(
        {
            "channels": {"C1": {"id": "C1", "name": "general", "context_team_id": "T_MOCK"}},
            "messages": {
                "C1": [
                    {"ts": "1700000001.000", "text": "parent", "user": "U_MOCK_BOT", "type": "message"},
                    {
                        "ts": "1700000002.000",
                        "text": "reply",
                        "user": "U_MOCK_BOT",
                        "type": "message",
                        "thread_ts": "1700000001.000",
                    },
                ]
            },
        }
    )
    client = TestClient(create_slack_viewer_app())

    messages = client.get("/api/channels/C1/messages").json()["messages"]
    # The reply is filtered out; only the parent remains in the main list.
    assert [message["ts"] for message in messages] == ["1700000001.000"]
    parent = messages[0]
    assert parent["has_thread"] is True
    assert parent["reply_count"] == 1

    # The thread endpoint can still return the parent + reply.
    thread = client.get("/api/threads/C1/1700000001.000").json()["messages"]
    assert [message["ts"] for message in thread] == ["1700000001.000", "1700000002.000"]
