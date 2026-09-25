from __future__ import annotations

import pytest
from helpers import seed_state

from slack_mock.server import (
    delete_message,
    edit_message,
    get_user_presence,
    list_pins,
    pin_message,
    set_user_status,
    unpin_message,
)
from slack_mock.state import get_state


def seed_pins_state() -> None:
    seed_state(
        {
            "channels": {"C001": {"id": "C001", "name": "general", "context_team_id": "T_MOCK"}},
            "messages": {
                "C001": [
                    {"ts": "1700000001.000", "text": "Hello world", "user": "U001", "type": "message"},
                    {
                        "ts": "1700000002.000",
                        "text": "Pinned already",
                        "user": "U001",
                        "type": "message",
                        "pinned_to": ["C001"],
                        "pinned_info": {"C001": {"pinned_by": "U001", "pinned_ts": 1700000000}},
                    },
                    {"ts": "1700000003.000", "text": "Another message", "user": "U002", "type": "message"},
                ]
            },
            "users": {
                "U001": {
                    "id": "U001",
                    "name": "alice",
                    "deleted": False,
                    "profile": {"display_name": "alice", "status_text": "", "status_emoji": ""},
                },
                "U002": {
                    "id": "U002",
                    "name": "bob",
                    "deleted": True,
                    "profile": {"display_name": "bob", "status_text": "", "status_emoji": ""},
                },
            },
        }
    )


@pytest.mark.asyncio
async def test_pin_unpin_and_list_pins() -> None:
    seed_pins_state()
    pinned = await pin_message("C001", "1700000001.000")
    assert pinned["ok"] is True
    msg = get_state().messages["C001"][0]
    assert msg.pinned_to is not None
    assert msg.pinned_info is not None
    assert "C001" in msg.pinned_to
    assert msg.pinned_info["C001"]["pinned_by"] == "U_MOCK_BOT"

    assert (await pin_message("C001", "1700000002.000"))["error"] == "already_pinned"
    assert (await pin_message("INVALID", "1700000001.000"))["error"] == "channel_not_found"
    assert (await pin_message("C001", "9999999999.000"))["error"] == "message_not_found"

    listed = await list_pins("C001")
    assert listed["ok"] is True
    assert len(listed["items"]) == 2

    unpinned = await unpin_message("C001", "1700000002.000")
    assert unpinned["ok"] is True
    assert get_state().messages["C001"][1].pinned_to is None
    assert (await unpin_message("C001", "1700000001.000"))["ok"] is True
    assert (await unpin_message("C001", "1700000001.000"))["error"] == "not_pinned"
    assert (await unpin_message("INVALID", "1700000002.000"))["error"] == "channel_not_found"
    assert (await list_pins("INVALID"))["error"] == "channel_not_found"


@pytest.mark.asyncio
async def test_status_and_presence() -> None:
    seed_pins_state()
    default = await set_user_status("In a meeting")
    assert default["ok"] is True
    assert default["profile"]["status_text"] == "In a meeting"
    assert get_state().users["U_MOCK_BOT"].profile.status_text == "In a meeting"

    emoji = await set_user_status("Vacation", status_emoji=":palm_tree:")
    assert emoji["profile"]["status_emoji"] == ":palm_tree:"
    denied = await set_user_status("Busy", user_id="U001")
    assert denied["ok"] is False
    assert denied["error"] == "cant_update_profile"
    assert get_state().users["U001"].profile.status_text == ""
    assert (await set_user_status("test", user_id="INVALID"))["error"] == "user_not_found"
    assert (await set_user_status(""))["profile"]["status_text"] == ""

    active = await get_user_presence("U001")
    away = await get_user_presence("U002")
    missing = await get_user_presence("INVALID")
    assert active["presence"] == "active"
    assert active["online"] is True
    assert active["manual_away"] is False
    assert away["presence"] == "away"
    assert away["online"] is False
    assert away["manual_away"] is True
    assert missing["error"] == "user_not_found"


@pytest.mark.asyncio
async def test_admin_can_set_other_user_status() -> None:
    seed_pins_state()
    get_state().is_admin = True

    result = await set_user_status("Busy", user_id="U001")

    assert result["ok"] is True
    assert get_state().users["U001"].profile.status_text == "Busy"


def seed_permissions_state(is_admin: bool = False) -> None:
    seed_state(
        {
            "is_admin": is_admin,
            "channels": {"C001": {"id": "C001", "name": "general"}},
            "messages": {
                "C001": [
                    {"ts": "1700000001.000", "text": "Bot message", "user": "U_MOCK_BOT", "type": "message"},
                    {"ts": "1700000002.000", "text": "Someone else wrote this", "user": "U_OTHER", "type": "message"},
                ]
            },
            "users": {"U_OTHER": {"id": "U_OTHER", "name": "other"}},
        }
    )


@pytest.mark.asyncio
async def test_edit_delete_permissions() -> None:
    seed_permissions_state(is_admin=False)
    own_edit = await edit_message("C001", "1700000001.000", "Updated")
    other_edit = await edit_message("C001", "1700000002.000", "Altered")
    assert own_edit["ok"] is True
    assert get_state().messages["C001"][0].text == "Updated"
    assert other_edit["ok"] is False
    assert other_edit["error"] == "cant_update_message"
    assert get_state().messages["C001"][1].text == "Someone else wrote this"

    own_delete = await delete_message("C001", "1700000001.000")
    assert own_delete["ok"] is True
    assert len(get_state().messages["C001"]) == 1

    seed_permissions_state(is_admin=False)
    other_delete = await delete_message("C001", "1700000002.000")
    assert other_delete["ok"] is False
    assert other_delete["error"] == "cant_delete_message"
    assert len(get_state().messages["C001"]) == 2

    seed_permissions_state(is_admin=True)
    assert (await edit_message("C001", "1700000002.000", "Altered"))["ok"] is True
    assert (await delete_message("C001", "1700000002.000"))["ok"] is True
    assert len(get_state().messages["C001"]) == 1
