from __future__ import annotations

import pytest
from helpers import seed_state

from slack_mock.models import SlackChannel
from slack_mock.server import (
    archive_channel,
    create_channel,
    list_dms,
    open_dm,
    open_mpim,
    rename_channel,
    send_dm,
    send_mpim,
)
from slack_mock.state import get_state


def setup_function() -> None:
    seed_state(
        {
            "channels": {"C001": {"id": "C001", "name": "general", "is_im": False, "context_team_id": "T_MOCK"}},
            "messages": {"C001": []},
            "users": {
                "U001": {
                    "id": "U001",
                    "name": "alice",
                    "real_name": "Alice Smith",
                    "profile": {"display_name": "alice"},
                },
                "U002": {"id": "U002", "name": "bob", "real_name": "Bob Jones", "profile": {"display_name": "bob"}},
                "U003": {
                    "id": "U003",
                    "name": "charlie",
                    "real_name": "Charlie Day",
                    "profile": {"display_name": "charlie"},
                },
            },
        }
    )


@pytest.mark.asyncio
async def test_open_dm() -> None:
    result = await open_dm("U001")
    assert result["ok"] is True
    assert result["channel"]["is_im"] is True
    assert result["channel"]["user"] == "U001"
    assert result["channel"]["is_private"] is True
    assert result["channel"]["is_channel"] is False
    assert get_state().messages[result["channel"]["id"]] == []


@pytest.mark.asyncio
async def test_open_dm_reuses_existing_and_separates_users() -> None:
    first = await open_dm("U001")
    second = await open_dm("U001")
    other = await open_dm("U002")

    assert second["channel"]["id"] == first["channel"]["id"]
    assert other["channel"]["id"] != first["channel"]["id"]
    assert (await open_dm("NONEXISTENT"))["error"] == "user_not_found"


@pytest.mark.asyncio
async def test_list_dms() -> None:
    assert (await list_dms())["channels"] == []
    await open_dm("U001")
    await open_dm("U002")
    result = await list_dms()
    assert len(result["channels"]) == 2
    assert all(channel["is_im"] for channel in result["channels"])
    assert all(channel["id"] != "C001" for channel in result["channels"])
    assert len((await list_dms(limit=1))["channels"]) == 1


@pytest.mark.asyncio
async def test_direct_conversations_cannot_be_archived_or_renamed() -> None:
    dm_id = (await open_dm("U001"))["channel"]["id"]
    state = get_state()
    state.channels["GMP001"] = SlackChannel.model_validate(
        {
            "id": "GMP001",
            "name": "mpdm-alice--bob-1",
            "is_channel": False,
            "is_group": False,
            "is_im": False,
            "is_mpim": True,
            "is_private": True,
            "context_team_id": "T_MOCK",
        }
    )
    state.messages["GMP001"] = []

    for channel_id in (dm_id, "GMP001"):
        archived = await archive_channel(channel_id)
        renamed = await rename_channel(channel_id, "new-name")

        assert archived == {"ok": False, "error": "method_not_supported_for_channel_type"}
        assert renamed == {"ok": False, "error": "method_not_supported_for_channel_type", "channel": {}}

    assert state.channels[dm_id].is_archived is False
    assert state.channels[dm_id].name == "alice"
    assert state.channels["GMP001"].is_archived is False
    assert state.channels["GMP001"].name == "mpdm-alice--bob-1"


@pytest.mark.asyncio
async def test_list_dms_includes_multi_party_direct_conversations() -> None:
    state = get_state()
    state.channels["GMP001"] = SlackChannel.model_validate(
        {
            "id": "GMP001",
            "name": "mpdm-alice--bob-1",
            "is_channel": False,
            "is_group": False,
            "is_im": False,
            "is_mpim": True,
            "is_private": True,
            "context_team_id": "T_MOCK",
        }
    )
    state.messages["GMP001"] = []

    result = await list_dms()

    assert [channel["id"] for channel in result["channels"]] == ["GMP001"]
    assert result["channels"][0]["is_mpim"] is True


@pytest.mark.asyncio
async def test_open_mpim_reuses_existing_conversation_by_members() -> None:
    first = await open_mpim(["U002", "U001"])
    second = await open_mpim(["U001", "U002"])

    assert first["ok"] is True
    assert first["channel"]["is_mpim"] is True
    assert first["channel"]["is_private"] is True
    assert first["channel"]["members"] == ["U_MOCK_BOT", "U001", "U002"]
    assert second["channel"]["id"] == first["channel"]["id"]
    assert [channel["id"] for channel in (await list_dms())["channels"]] == [first["channel"]["id"]]


@pytest.mark.asyncio
async def test_open_mpim_validates_user_list() -> None:
    assert await open_mpim(["U001"]) == {"ok": False, "error": "not_enough_users", "channel": {}}
    assert await open_mpim(["U001", "U001"]) == {"ok": False, "error": "not_enough_users", "channel": {}}
    assert await open_mpim(["U001", "U404"]) == {"ok": False, "error": "user_not_found", "channel": {}}


@pytest.mark.asyncio
async def test_send_mpim_posts_and_reuses_conversation() -> None:
    sent = await send_mpim(["U001", "U002"], "Hello group")
    second = await send_mpim(["U002", "U001"], "Second group note")

    assert sent["ok"] is True
    assert sent["message"]["text"] == "Hello group"
    assert second["channel"] == sent["channel"]
    assert [message.text for message in get_state().messages[sent["channel"]]] == ["Hello group", "Second group note"]
    assert (await send_mpim(["U001", "U404"], "Hi"))["ok"] is False


@pytest.mark.asyncio
async def test_dm_names_do_not_reserve_channel_names() -> None:
    opened = await open_dm("U001")

    created = await create_channel(opened["channel"]["name"])

    assert created["ok"] is True
    assert created["channel"]["name"] == "alice"
    assert created["channel"]["is_im"] is False


@pytest.mark.asyncio
async def test_send_dm() -> None:
    result = await send_dm("U001", "Hello Alice!")
    assert result["ok"] is True
    assert result["message"]["text"] == "Hello Alice!"
    assert result["channel"]
    assert result["ts"]

    assert len((await list_dms())["channels"]) == 1
    await send_dm("U001", "Second")
    assert len((await list_dms())["channels"]) == 1
    assert get_state().messages[result["channel"]][0].text == "Hello Alice!"
    assert (await send_dm("NONEXISTENT", "Hi"))["ok"] is False
