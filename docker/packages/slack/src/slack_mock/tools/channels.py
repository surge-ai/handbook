"""Channel tool handlers."""

from __future__ import annotations

from typing import Annotated, Any

from pydantic import Field

from slack_mock.models import SlackChannel, SlackChannelTopic, SlackState
from slack_mock.state import generate_channel_id, get_bot_user_id, get_channel, get_state, mutate_state
from slack_mock.tools.common import (
    channel_payload,
    create_channel_object,
    is_direct_conversation,
    is_named_channel,
    model_dump,
    now_seconds,
    parse_cursor,
)


def list_channels(limit: int = 100, cursor: str | None = None) -> dict[str, Any]:
    state = get_state()
    normalized_limit = min(limit or 100, 200)
    start_index = parse_cursor(cursor)
    all_channels = [
        channel
        for channel in state.channels.values()
        if channel.is_member and not channel.is_archived and (channel.is_channel or channel.is_group)
    ]
    paginated = all_channels[start_index : start_index + normalized_limit]
    next_index = start_index + normalized_limit
    return {
        "ok": True,
        "channels": model_dump(paginated),
        "response_metadata": {"next_cursor": str(next_index) if next_index < len(all_channels) else ""},
    }


def create_channel(
    name: Annotated[str, Field(min_length=1, description="Channel name.")], is_private: bool = False
) -> dict[str, Any]:
    state = get_state()
    normalized = name.strip().lower()
    if not normalized:
        return {"ok": False, "error": "invalid_name", "channel": {}}
    if any(is_named_channel(channel) and channel.name == normalized for channel in state.channels.values()):
        return {"ok": False, "error": "name_taken", "channel": {}}

    def _create(state: SlackState) -> SlackChannel:
        channel_id = generate_channel_id()
        channel = create_channel_object(channel_id, normalized, is_private=is_private)
        state.channels[channel_id] = channel
        state.messages[channel_id] = []
        return channel

    channel = mutate_state(_create)
    return {"ok": True, "channel": channel_payload(channel)}


def archive_channel(channel_id: str) -> dict[str, Any]:
    channel = get_channel(channel_id)
    if channel is None:
        return {"ok": False, "error": "channel_not_found"}
    if is_direct_conversation(channel):
        return {"ok": False, "error": "method_not_supported_for_channel_type"}
    if channel.is_archived:
        return {"ok": False, "error": "already_archived"}

    def _archive(state: SlackState) -> None:
        channel = state.channels[channel_id]
        channel.is_archived = True
        channel.updated = now_seconds()

    mutate_state(_archive)
    return {"ok": True}


def rename_channel(
    channel_id: str, name: Annotated[str, Field(min_length=1, description="New channel name.")]
) -> dict[str, Any]:
    state = get_state()
    channel = get_channel(channel_id)
    if channel is None:
        return {"ok": False, "error": "channel_not_found", "channel": {}}
    if is_direct_conversation(channel):
        return {"ok": False, "error": "method_not_supported_for_channel_type", "channel": {}}
    new_name = name.strip().lower()
    if not new_name:
        return {"ok": False, "error": "invalid_name", "channel": {}}
    if any(
        other.id != channel_id and is_named_channel(other) and other.name == new_name
        for other in state.channels.values()
    ):
        return {"ok": False, "error": "name_taken", "channel": {}}

    def _rename(state: SlackState) -> SlackChannel:
        channel = state.channels[channel_id]
        channel.previous_names = channel.previous_names or []
        channel.previous_names.append(channel.name)
        channel.name = new_name
        channel.name_normalized = new_name
        channel.updated = now_seconds()
        return channel

    channel = mutate_state(_rename)
    return {"ok": True, "channel": channel_payload(channel)}


def set_channel_topic(
    channel_id: str,
    topic: Annotated[str | None, Field(description="New channel topic.")] = None,
    purpose: Annotated[str | None, Field(description="New channel purpose.")] = None,
) -> dict[str, Any]:
    channel = get_channel(channel_id)
    if channel is None:
        return {"ok": False, "error": "channel_not_found", "channel": {}}

    def _set_topic(state: SlackState) -> SlackChannel:
        channel = state.channels[channel_id]
        now = now_seconds()
        if topic is not None:
            channel.topic = SlackChannelTopic(value=topic, creator=get_bot_user_id(), last_set=now)
        if purpose is not None:
            channel.purpose = SlackChannelTopic(value=purpose, creator=get_bot_user_id(), last_set=now)
        channel.updated = now
        return channel

    channel = mutate_state(_set_topic)
    return {"ok": True, "channel": channel_payload(channel)}
