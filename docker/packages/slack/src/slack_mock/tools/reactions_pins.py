"""Reaction and pin tool handlers."""

from __future__ import annotations

from typing import Any

from slack_mock.models import SlackMessage, SlackReaction
from slack_mock.state import (
    find_message,
    get_bot_user_id,
    get_channel,
    get_channel_messages,
    update_message,
)
from slack_mock.tools.common import message_payload, now_seconds


def add_reaction(channel_id: str, timestamp: str, reaction: str) -> dict[str, Any]:
    if get_channel(channel_id) is None:
        return {"ok": False, "error": "channel_not_found"}
    if find_message(channel_id, timestamp) is None:
        return {"ok": False, "error": "message_not_found"}
    reaction_name = reaction.strip(":")

    def _add(msg: SlackMessage) -> None:
        msg.reactions = msg.reactions or []
        existing = next((item for item in msg.reactions if item.name == reaction_name), None)
        if existing is not None:
            if get_bot_user_id() not in existing.users:
                existing.users.append(get_bot_user_id())
                existing.count += 1
        else:
            msg.reactions.append(SlackReaction(name=reaction_name, users=[get_bot_user_id()], count=1))

    return {"ok": update_message(channel_id, timestamp, _add)}


def pin_message(channel_id: str, timestamp: str) -> dict[str, Any]:
    if get_channel(channel_id) is None:
        return {"ok": False, "error": "channel_not_found"}
    message = find_message(channel_id, timestamp)
    if message is None:
        return {"ok": False, "error": "message_not_found"}
    if message.pinned_to and channel_id in message.pinned_to:
        return {"ok": False, "error": "already_pinned"}

    def _pin(msg: SlackMessage) -> None:
        msg.pinned_to = msg.pinned_to or []
        msg.pinned_to.append(channel_id)
        msg.pinned_info = msg.pinned_info or {}
        msg.pinned_info[channel_id] = {"pinned_by": get_bot_user_id(), "pinned_ts": now_seconds()}

    update_message(channel_id, timestamp, _pin)
    return {"ok": True}


def unpin_message(channel_id: str, timestamp: str) -> dict[str, Any]:
    if get_channel(channel_id) is None:
        return {"ok": False, "error": "channel_not_found"}
    message = find_message(channel_id, timestamp)
    if message is None:
        return {"ok": False, "error": "message_not_found"}
    if not message.pinned_to or channel_id not in message.pinned_to:
        return {"ok": False, "error": "not_pinned"}

    def _unpin(msg: SlackMessage) -> None:
        msg.pinned_to = [pinned_id for pinned_id in msg.pinned_to or [] if pinned_id != channel_id] or None
        if msg.pinned_info:
            msg.pinned_info.pop(channel_id, None)

    update_message(channel_id, timestamp, _unpin)
    return {"ok": True}


def list_pins(channel_id: str) -> dict[str, Any]:
    if get_channel(channel_id) is None:
        return {"ok": False, "error": "channel_not_found", "items": []}
    items = []
    for message in get_channel_messages(channel_id):
        if message.pinned_to and channel_id in message.pinned_to:
            info = (message.pinned_info or {}).get(channel_id, {})
            items.append(
                {
                    "type": "message",
                    "channel": channel_id,
                    "message": message_payload(message),
                    "created": info.get("pinned_ts", 0),
                    "created_by": info.get("pinned_by", get_bot_user_id()),
                }
            )
    items.sort(key=lambda item: item["created"], reverse=True)
    return {"ok": True, "items": items}
