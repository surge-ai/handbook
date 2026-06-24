"""Direct message tool handlers."""

from __future__ import annotations

import re
from typing import Any

from slack_mock.models import SlackChannel, SlackMessage, SlackMessageType, SlackState
from slack_mock.state import (
    add_message,
    generate_mpim_channel_id,
    generate_timestamp,
    get_bot_user_id,
    get_state,
    get_user,
    mutate_state,
)
from slack_mock.tools.common import (
    channel_payload,
    create_channel_object,
    empty_message,
    is_direct_conversation,
    message_payload,
    model_dump,
    now_seconds,
)


def open_dm(user_id: str) -> dict[str, Any]:
    return _open_dm(user_id)


def _open_dm(user_id: str) -> dict[str, Any]:
    state = get_state()
    user = get_user(user_id)
    if user is None:
        return {"ok": False, "error": "user_not_found", "channel": {}}
    for channel in state.channels.values():
        if channel.is_im and channel.user == user_id:
            return {"ok": True, "channel": channel_payload(channel)}

    def _open(state: SlackState) -> SlackChannel:
        now = now_seconds()
        channel_id = f"D{str(now)[-6:]}{re.sub(r'[^a-zA-Z0-9]', '', user_id)}"
        channel = create_channel_object(channel_id, user.name, is_private=True, is_im=True, user_id=user_id)
        state.channels[channel_id] = channel
        state.messages[channel_id] = []
        return channel

    channel = mutate_state(_open)
    return {"ok": True, "channel": channel_payload(channel)}


def _mpim_members(user_ids: list[str]) -> tuple[list[str], list[str]]:
    unique_user_ids = list(dict.fromkeys(user_ids))
    bot_user_id = get_bot_user_id()
    other_user_ids = sorted(user_id for user_id in unique_user_ids if user_id != bot_user_id)
    return [bot_user_id, *other_user_ids], other_user_ids


def _mpim_name(other_user_ids: list[str]) -> str:
    state = get_state()
    names = [state.users[user_id].name for user_id in other_user_ids]
    normalized_names = [re.sub(r"[^a-z0-9_-]+", "-", name.casefold()).strip("-") for name in names]
    return f"mpdm-{'--'.join(normalized_names)}-1"


def open_mpim(user_ids: list[str]) -> dict[str, Any]:
    return _open_mpim(user_ids)


def _open_mpim(user_ids: list[str]) -> dict[str, Any]:
    state = get_state()
    member_ids, other_user_ids = _mpim_members(user_ids)
    if len(other_user_ids) < 2:
        return {"ok": False, "error": "not_enough_users", "channel": {}}
    missing_user_ids = [user_id for user_id in other_user_ids if user_id not in state.users]
    if missing_user_ids:
        return {"ok": False, "error": "user_not_found", "channel": {}}

    member_set = set(member_ids)
    channel_name = _mpim_name(other_user_ids)
    for channel in state.channels.values():
        if not channel.is_mpim:
            continue
        if channel.members and set(channel.members) == member_set:
            return {"ok": True, "channel": channel_payload(channel)}
        if channel.members is None and channel.name == channel_name:
            return {"ok": True, "channel": channel_payload(channel)}

    def _open(state: SlackState) -> SlackChannel:
        channel_id = generate_mpim_channel_id()
        channel = create_channel_object(
            channel_id,
            channel_name,
            is_private=True,
            is_mpim=True,
            members=member_ids,
        )
        state.channels[channel_id] = channel
        state.messages[channel_id] = []
        return channel

    channel = mutate_state(_open)
    return {"ok": True, "channel": channel_payload(channel)}


def list_dms(limit: int = 20) -> dict[str, Any]:
    dms = sorted(
        (channel for channel in get_state().channels.values() if is_direct_conversation(channel)),
        key=lambda channel: channel.updated,
        reverse=True,
    )
    return {"ok": True, "channels": model_dump(dms[: limit or 20])}


def send_dm(user_id: str, text: str) -> dict[str, Any]:
    opened = _open_dm(user_id)
    if not opened.get("ok"):
        return {"ok": False, "error": opened.get("error"), "channel": "", "ts": "", "message": empty_message()}
    channel_id = opened["channel"]["id"]
    ts = generate_timestamp()
    message = SlackMessage(type=SlackMessageType.MESSAGE, user=get_bot_user_id(), text=text, ts=ts, team="T_MOCK")
    add_message(channel_id, message)
    return {"ok": True, "channel": channel_id, "ts": ts, "message": message_payload(message)}


def send_mpim(user_ids: list[str], text: str) -> dict[str, Any]:
    opened = _open_mpim(user_ids)
    if not opened.get("ok"):
        return {"ok": False, "error": opened.get("error"), "channel": "", "ts": "", "message": empty_message()}
    channel_id = opened["channel"]["id"]
    ts = generate_timestamp()
    message = SlackMessage(type=SlackMessageType.MESSAGE, user=get_bot_user_id(), text=text, ts=ts, team="T_MOCK")
    add_message(channel_id, message)
    return {"ok": True, "channel": channel_id, "ts": ts, "message": message_payload(message)}
