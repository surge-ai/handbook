from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from slack_mock.models import SlackState
from slack_mock.server import (
    create_channel,
    delete_message,
    open_dm,
    post_message,
    reply_to_thread,
    search_messages,
    send_dm,
    set_user_status,
)
from slack_mock.state import get_bot_user_id, get_state, mutate_state, reset_state, state_from_json


def _seed_state() -> None:
    state_from_json(
        {
            "bot_user_id": "U_MOCK_BOT",
            "users": {
                "U_MOCK_BOT": {
                    "id": "U_MOCK_BOT",
                    "name": "slackbot",
                    "profile": {"display_name": "Mock Bot"},
                    "is_bot": True,
                },
                "U001": {
                    "id": "U001",
                    "name": "alice",
                    "real_name": "Alice Example",
                    "profile": {"display_name": "Alice", "email": "alice@example.com"},
                },
            },
            "channels": {
                "C001": {
                    "id": "C001",
                    "name": "general",
                    "context_team_id": "T_MOCK",
                }
            },
            "messages": {
                "C001": [
                    {
                        "type": "message",
                        "user": "U001",
                        "text": "March hours are ready",
                        "ts": "1700000001.000",
                        "team": "T_MOCK",
                        "reactions": [{"name": "eyes", "users": ["U_MOCK_BOT"], "count": 1}],
                    },
                    {
                        "type": "message",
                        "user": "U_MOCK_BOT",
                        "text": "Budget link https://example.com",
                        "ts": "1700000002.000",
                        "team": "T_MOCK",
                    },
                ]
            },
            "counters": {"channelId": 1000, "fileId": 1000},
        }
    )


@pytest.fixture(autouse=True)
def seeded_state() -> None:
    _seed_state()


@pytest.mark.asyncio
async def test_create_channel_preserves_existing_name_normalization_behavior() -> None:
    result = await create_channel("MyChannel")

    assert result["ok"] is True
    assert result["channel"]["name"] == "mychannel"
    assert result["channel"]["name_normalized"] == "mychannel"


@pytest.mark.asyncio
async def test_search_messages_keeps_warning_based_limit_and_cursor_behavior() -> None:
    result = await search_messages("budget", limit=999, cursor="not-a-number")

    assert result["ok"] is True
    assert result["messages"]["total"] == 1
    assert result["response_metadata"]["warnings"] == [
        "limit exceeds the maximum of 100; using 100.",
        "Invalid cursor 'not-a-number'; using the first page.",
    ]


@pytest.mark.asyncio
async def test_search_messages_keeps_empty_query_tool_error_shape() -> None:
    result = await search_messages("")

    assert result == {
        "ok": False,
        "error": "missing_query",
        "messages": {"matches": [], "total": 0},
        "response_metadata": {"warnings": []},
    }


@pytest.mark.asyncio
async def test_status_text_can_be_empty_to_clear_status() -> None:
    result = await set_user_status("")

    assert result["ok"] is True
    assert result["profile"]["status_text"] == ""


@pytest.mark.asyncio
async def test_open_and_send_dm_preserve_current_behavior() -> None:
    opened = await open_dm("U001")
    sent = await send_dm("U001", "hello")

    assert opened["ok"] is True
    assert opened["channel"]["is_im"] is True
    assert sent["ok"] is True
    assert sent["channel"] == opened["channel"]["id"]
    assert get_state().messages[sent["channel"]][-1].text == "hello"


@pytest.mark.asyncio
async def test_default_state_can_write_bot_messages() -> None:
    reset_state()
    created = await create_channel("general")
    assert created["ok"] is True

    posted = await post_message(created["channel"]["id"], "hello from default state")

    assert posted["ok"] is True
    assert posted["message"]["user"] == "U_MOCK_BOT"
    assert "U_MOCK_BOT" in get_state().users


@pytest.mark.asyncio
async def test_thread_reply_metadata_updates_atomically() -> None:
    first = await reply_to_thread("C001", "1700000001.000", "first reply")
    second = await reply_to_thread("C001", "1700000001.000", "second reply")

    assert first["ok"] is True
    assert second["ok"] is True
    parent = get_state().messages["C001"][0]
    assert parent.reply_count == 2
    assert parent.reply_users == ["U_MOCK_BOT"]
    assert parent.reply_users_count == 1
    assert parent.latest_reply == max(first["ts"], second["ts"], key=float)


@pytest.mark.asyncio
async def test_deleting_thread_reply_refreshes_parent_metadata() -> None:
    first = await reply_to_thread("C001", "1700000001.000", "first reply")
    second = await reply_to_thread("C001", "1700000001.000", "second reply")

    deleted = await delete_message("C001", second["ts"])

    assert deleted["ok"] is True
    parent = get_state().messages["C001"][0]
    assert parent.reply_count == 1
    assert parent.reply_users == ["U_MOCK_BOT"]
    assert parent.reply_users_count == 1
    assert parent.latest_reply == first["ts"]


def test_failed_state_mutation_rolls_back_live_state() -> None:
    before = get_state().model_dump(mode="json", by_alias=True, exclude_none=True)

    with pytest.raises(ValidationError, match="bot_user_id"):
        mutate_state(lambda state: state.users.pop("U_MOCK_BOT"))

    assert get_state().model_dump(mode="json", by_alias=True, exclude_none=True) == before


def test_bot_user_id_reads_current_state_after_direct_mutation() -> None:
    mutate_state(lambda state: setattr(state, "bot_user_id", "U001"))

    assert get_bot_user_id() == "U001"


def test_state_rejects_message_channel_without_matching_channel() -> None:
    with pytest.raises(ValidationError, match="does not reference an existing channel"):
        SlackState.model_validate(
            {
                "channels": {},
                "messages": {"C404": [{"type": "message", "text": "orphan", "ts": "1700000001.000"}]},
            }
        )


def test_state_rejects_reaction_count_drift() -> None:
    with pytest.raises(ValidationError, match="reaction.count"):
        SlackState.model_validate(
            {
                "channels": {"C001": {"id": "C001", "name": "general"}},
                "messages": {
                    "C001": [
                        {
                            "type": "message",
                            "text": "bad reaction",
                            "ts": "1700000001.000",
                            "reactions": [{"name": "eyes", "users": ["U001"], "count": 2}],
                        }
                    ]
                },
            }
        )


def test_state_rejects_unknown_extra_fields() -> None:
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        SlackState.model_validate({"channels": {}, "messages": {}, "users": {}, "unexpected": True})


def test_state_rejects_duplicate_usernames_but_allows_duplicate_display_names() -> None:
    state = {
        "users": {
            "U001": {"id": "U001", "name": "alice", "profile": {"display_name": "Alex"}},
            "U002": {"id": "U002", "name": "bob", "profile": {"display_name": "Alex"}},
        }
    }
    assert SlackState.model_validate(state).users["U002"].profile.display_name == "Alex"

    state["users"]["U002"]["name"] = "Alice"
    with pytest.raises(ValidationError, match="user name"):
        SlackState.model_validate(state)


@pytest.mark.parametrize(
    ("field", "value", "match"),
    [
        ("user_id", "bad-user", "String should match pattern"),
        ("channel_id", "X001", "String should match pattern"),
        ("file_id", "BAD001", "String should match pattern"),
        ("timestamp", "1700000001", "String should match pattern"),
        ("status_emoji", "palm_tree", "String should match pattern"),
        ("message_type", "event", "Input should be"),
        ("message_subtype", "unknown_subtype", "Input should be"),
        ("file_mode", "mystery", "Input should be"),
    ],
)
def test_state_rejects_bad_primitive_shapes(field: str, value: str, match: str) -> None:
    state: dict[str, Any] = {
        "users": {
            "U001": {
                "id": "U001",
                "name": "alice",
                "profile": {"status_emoji": ":palm_tree:"},
            }
        },
        "channels": {"C001": {"id": "C001", "name": "general"}},
        "messages": {
            "C001": [
                {
                    "type": "message",
                    "text": "hello",
                    "ts": "1700000001.000",
                    "user": "U001",
                    "files": [{"id": "F001", "mode": "hosted"}],
                }
            ]
        },
    }
    if field == "user_id":
        state["users"] = {value: {"id": value, "name": "alice"}}
    elif field == "channel_id":
        state["channels"] = {value: {"id": value, "name": "general"}}
        state["messages"] = {value: state["messages"]["C001"]}
    elif field == "file_id":
        state["messages"]["C001"][0]["files"][0]["id"] = value
    elif field == "timestamp":
        state["messages"]["C001"][0]["ts"] = value
    elif field == "status_emoji":
        state["users"]["U001"]["profile"]["status_emoji"] = value
    elif field == "message_type":
        state["messages"]["C001"][0]["type"] = value
    elif field == "message_subtype":
        state["messages"]["C001"][0]["subtype"] = value
    elif field == "file_mode":
        state["messages"]["C001"][0]["files"][0]["mode"] = value

    with pytest.raises(ValidationError, match=match):
        SlackState.model_validate(state)


def _valid_relationship_state() -> dict[str, Any]:
    return {
        "bot_user_id": "U_MOCK_BOT",
        "users": {
            "U_MOCK_BOT": {"id": "U_MOCK_BOT", "name": "bot"},
            "U001": {"id": "U001", "name": "alice"},
            "U002": {"id": "U002", "name": "bob"},
        },
        "channels": {"C001": {"id": "C001", "name": "general"}},
        "messages": {
            "C001": [
                {
                    "type": "message",
                    "text": "parent",
                    "ts": "1700000001.000",
                    "user": "U001",
                    "reply_count": 1,
                    "reply_users": ["U002"],
                    "reply_users_count": 1,
                    "latest_reply": "1700000002.000",
                    "reactions": [{"name": "eyes", "users": ["U_MOCK_BOT"], "count": 1}],
                },
                {
                    "type": "message",
                    "text": "reply",
                    "ts": "1700000002.000",
                    "user": "U002",
                    "thread_ts": "1700000001.000",
                    "files": [{"id": "F001", "user": "U002", "channels": ["C001"]}],
                },
            ]
        },
    }


def test_state_accepts_consistent_relationships() -> None:
    assert SlackState.model_validate(_valid_relationship_state()).messages["C001"][0].reply_count == 1


@pytest.mark.parametrize(
    ("mutate", "match"),
    [
        (lambda state: state.__setitem__("bot_user_id", "U404"), "bot_user_id"),
        (lambda state: state["messages"]["C001"][0].__setitem__("user", "U404"), "does not reference an existing user"),
        (lambda state: state["channels"]["C001"].__setitem__("creator", "U404"), "creator"),
        (
            lambda state: state["channels"]["C001"].__setitem__(
                "topic", {"value": "general", "creator": "U404", "last_set": 1}
            ),
            "topic.creator",
        ),
        (lambda state: state["messages"]["C001"][0]["reactions"][0]["users"].append("U404"), "reaction user"),
        (lambda state: state["messages"]["C001"][1]["files"][0].__setitem__("channels", ["C404"]), "file channel"),
        (lambda state: state["messages"]["C001"][0].__setitem__("reply_count", 2), "reply_count"),
        (lambda state: state["messages"]["C001"][0].__setitem__("latest_reply", "1700000003.000"), "latest_reply"),
        (
            lambda state: state["messages"]["C001"][0].update(
                {"pinned_to": ["C001"], "pinned_info": {"C001": {"pinned_by": "U404", "pinned_ts": 1}}}
            ),
            "pinned_by",
        ),
        (
            lambda state: state["messages"]["C001"][0].update(
                {"pinned_to": ["C001"], "pinned_info": {"C404": {"pinned_by": "U001", "pinned_ts": 1}}}
            ),
            "pinned_info contains channels",
        ),
    ],
)
def test_state_rejects_inconsistent_relationships(mutate, match: str) -> None:
    state = _valid_relationship_state()
    mutate(state)

    with pytest.raises(ValidationError, match=match):
        SlackState.model_validate(state)
