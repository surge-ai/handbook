from __future__ import annotations

import pytest
from helpers import seed_state

from slack_mock.server import delete_message, edit_message
from slack_mock.state import get_state


def setup_function() -> None:
    seed_state(
        {
            "is_admin": True,
            "users": {
                "U001": {"id": "U001", "name": "alice"},
                "U002": {"id": "U002", "name": "bob"},
            },
            "channels": {"C001": {"id": "C001", "name": "general", "context_team_id": "T_MOCK"}},
            "messages": {
                "C001": [
                    {"ts": "1700000001.000", "text": "Hello world", "user": "U001", "type": "message"},
                    {
                        "ts": "1700000002.000",
                        "text": "Reply in thread",
                        "user": "U002",
                        "type": "message",
                        "thread_ts": "1700000001.000",
                    },
                    {"ts": "1700000003.000", "text": "Another message", "user": "U001", "type": "message"},
                ]
            },
        }
    )


@pytest.mark.asyncio
async def test_edit_message() -> None:
    result = await edit_message("C001", "1700000001.000", "Updated text")
    assert result["ok"] is True
    assert result["text"] == "Updated text"
    assert result["message"]["text"] == "Updated text"
    assert result["channel"] == "C001"
    assert result["ts"] == "1700000001.000"

    msg = get_state().messages["C001"][0]
    assert msg.edited is not None
    assert msg.edited.user == "U_MOCK_BOT"
    assert msg.edited.ts


@pytest.mark.asyncio
async def test_edit_message_errors_and_thread_replies() -> None:
    assert (await edit_message("INVALID", "1700000001.000", "x"))["error"] == "channel_not_found"
    assert (await edit_message("C001", "9999999999.000", "x"))["error"] == "message_not_found"

    reply = await edit_message("C001", "1700000002.000", "Edited reply")
    assert reply["ok"] is True
    assert reply["message"]["text"] == "Edited reply"
    assert reply["message"]["thread_ts"] == "1700000001.000"


@pytest.mark.asyncio
async def test_delete_message() -> None:
    assert len(get_state().messages["C001"]) == 3
    result = await delete_message("C001", "1700000003.000")
    assert result == {"ok": True, "channel": "C001", "ts": "1700000003.000"}
    assert len(get_state().messages["C001"]) == 2
    assert all(message.ts != "1700000003.000" for message in get_state().messages["C001"])


@pytest.mark.asyncio
async def test_delete_message_errors_and_thread_replies() -> None:
    assert (await delete_message("INVALID", "1700000001.000"))["error"] == "channel_not_found"
    assert (await delete_message("C001", "9999999999.000"))["error"] == "message_not_found"

    result = await delete_message("C001", "1700000002.000")
    assert result["ok"] is True
    assert any(message.ts == "1700000001.000" for message in get_state().messages["C001"])
