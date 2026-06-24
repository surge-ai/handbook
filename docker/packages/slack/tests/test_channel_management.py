from __future__ import annotations

import pytest
from helpers import seed_state

from slack_mock.server import archive_channel, create_channel, list_channels, rename_channel, set_channel_topic
from slack_mock.state import get_state


def setup_function() -> None:
    seed_state(
        {
            "users": {"U001": {"id": "U001", "name": "alice"}},
            "channels": {
                "C001": {
                    "id": "C001",
                    "name": "general",
                    "name_normalized": "general",
                    "is_general": True,
                    "context_team_id": "T_MOCK",
                    "updated": 1700000000,
                    "creator": "U001",
                    "num_members": 5,
                    "previous_names": [],
                    "topic": {"value": "General chat", "creator": "U001", "last_set": 1700000000},
                    "purpose": {"value": "Team comms", "creator": "U001", "last_set": 1700000000},
                },
                "C002": {
                    "id": "C002",
                    "name": "random",
                    "name_normalized": "random",
                    "is_archived": True,
                    "context_team_id": "T_MOCK",
                    "updated": 1700000000,
                    "creator": "U001",
                    "num_members": 3,
                    "previous_names": [],
                    "topic": {"value": "", "creator": "", "last_set": 0},
                    "purpose": {"value": "", "creator": "", "last_set": 0},
                },
                "C003": {
                    "id": "C003",
                    "name": "external",
                    "context_team_id": "T_MOCK",
                    "is_member": False,
                },
                "D001": {
                    "id": "D001",
                    "name": "alice",
                    "context_team_id": "T_MOCK",
                    "is_channel": False,
                    "is_im": True,
                    "is_private": True,
                    "user": "U001",
                },
                "G001": {
                    "id": "G001",
                    "name": "private",
                    "context_team_id": "T_MOCK",
                    "is_channel": False,
                    "is_group": True,
                    "is_private": True,
                },
            },
            "messages": {"C001": [], "C002": [], "C003": [], "D001": [], "G001": []},
            "counters": {"channelId": 1000},
        }
    )


@pytest.mark.asyncio
async def test_list_channels_filters_to_active_channel_memberships() -> None:
    listed = await list_channels()

    assert listed["ok"] is True
    assert [channel["id"] for channel in listed["channels"]] == ["C001", "G001"]


@pytest.mark.asyncio
async def test_create_channel_public_private_and_messages() -> None:
    public = await create_channel("engineering")
    private = await create_channel("secret", is_private=True)

    assert public["ok"] is True
    assert public["channel"]["name"] == "engineering"
    assert public["channel"]["is_private"] is False
    assert public["channel"]["is_channel"] is True
    assert get_state().messages[public["channel"]["id"]] == []

    assert private["ok"] is True
    assert private["channel"]["is_private"] is True
    assert private["channel"]["is_group"] is True
    assert private["channel"]["is_channel"] is False


@pytest.mark.asyncio
async def test_create_channel_normalizes_and_rejects_bad_names() -> None:
    normalized = await create_channel("MyChannel")
    duplicate = await create_channel("general")
    empty = await create_channel("  ")
    second = await create_channel("chan-b")

    assert normalized["channel"]["name"] == "mychannel"
    assert normalized["channel"]["name_normalized"] == "mychannel"
    assert duplicate["ok"] is False
    assert duplicate["error"] == "name_taken"
    assert empty["ok"] is False
    assert empty["error"] == "invalid_name"
    assert normalized["channel"]["id"] != second["channel"]["id"]


@pytest.mark.asyncio
async def test_archive_channel() -> None:
    before = get_state().channels["C001"].updated
    archived = await archive_channel("C001")
    already = await archive_channel("C002")
    missing = await archive_channel("INVALID")

    assert archived["ok"] is True
    assert get_state().channels["C001"].is_archived is True
    assert get_state().channels["C001"].updated >= before
    assert already == {"ok": False, "error": "already_archived"}
    assert missing == {"ok": False, "error": "channel_not_found"}


@pytest.mark.asyncio
async def test_rename_channel() -> None:
    renamed = await rename_channel("C001", "announcements")
    normalized = await rename_channel("C001", "NewName")
    duplicate = await rename_channel("C001", "random")
    missing = await rename_channel("INVALID", "test")
    empty = await rename_channel("C001", "  ")

    assert renamed["ok"] is True
    assert renamed["channel"]["name"] == "announcements"
    previous_names = get_state().channels["C001"].previous_names
    assert previous_names is not None
    assert "general" in previous_names
    assert normalized["channel"]["name"] == "newname"
    assert duplicate["error"] == "name_taken"
    assert missing["error"] == "channel_not_found"
    assert empty["error"] == "invalid_name"


@pytest.mark.asyncio
async def test_set_channel_topic_and_purpose() -> None:
    topic = await set_channel_topic("C001", topic="New topic")
    assert topic["ok"] is True
    assert topic["channel"]["topic"]["value"] == "New topic"
    assert topic["channel"]["topic"]["creator"] == "U_MOCK_BOT"
    assert topic["channel"]["purpose"]["value"] == "Team comms"

    purpose = await set_channel_topic("C001", purpose="New purpose")
    assert purpose["channel"]["purpose"]["value"] == "New purpose"

    both = await set_channel_topic("C001", topic="T", purpose="P")
    assert both["channel"]["topic"]["value"] == "T"
    assert both["channel"]["purpose"]["value"] == "P"
    assert (await set_channel_topic("INVALID", topic="test"))["error"] == "channel_not_found"
