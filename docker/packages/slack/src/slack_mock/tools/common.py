"""Shared Slack tool helpers."""

from __future__ import annotations

import time
from typing import Annotated, Any

from pydantic import BaseModel, Field

from slack_mock.models import (
    SlackChannel,
    SlackChannelId,
    SlackChannelTopic,
    SlackMessage,
    SlackTs,
    SlackUserId,
    SlackWorkspaceId,
)
from slack_mock.state import get_bot_user_id

ChannelIdArg = Annotated[SlackChannelId, Field(description="Slack channel, private channel, DM, or MPIM ID.")]
CursorArg = Annotated[str, Field(description="Pagination cursor returned by a previous response.")]
FileContentBase64Arg = Annotated[str, Field(description="Base64-encoded file content.")]
FilenameArg = Annotated[str, Field(min_length=1, description="File name to store in Slack.")]
LimitArg = Annotated[int, Field(ge=0, description="Maximum number of items to return.")]
MessageTextArg = Annotated[str, Field(min_length=1, description="Message text.")]
ReactionArg = Annotated[
    str, Field(min_length=1, description="Emoji reaction name, with or without surrounding colons.")
]
SearchQueryArg = Annotated[str, Field(min_length=1, description="Slack search query.")]
TimestampArg = Annotated[SlackTs, Field(description="Slack message timestamp, such as 1700000001.000.")]
UserIdArg = Annotated[SlackUserId, Field(description="Slack user ID.")]
UserIdsArg = Annotated[
    list[SlackUserId],
    Field(min_length=2, description="Slack user IDs to include in a multi-party direct message."),
]
WorkspaceIdArg = Annotated[
    SlackWorkspaceId, Field(description="Slack workspace ID. Defaults to the default workspace.")
]


def model_dump(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json", by_alias=True, exclude_none=True)
    if isinstance(value, list):
        return [model_dump(item) for item in value]
    if isinstance(value, dict):
        return {key: model_dump(item) for key, item in value.items() if item is not None}
    return value


def empty_message() -> dict[str, str]:
    return {"type": "message", "text": "", "ts": ""}


def now_seconds() -> int:
    return int(time.time())


def parse_cursor(cursor: str | None) -> int:
    if not cursor:
        return 0
    try:
        return int(cursor)
    except ValueError:
        return 0


def channel_payload(channel: SlackChannel) -> dict[str, Any]:
    return channel.model_dump(mode="json", by_alias=True, exclude_none=True)


def message_payload(message: SlackMessage) -> dict[str, Any]:
    return message.model_dump(mode="json", by_alias=True, exclude_none=True)


def create_channel_object(
    channel_id: str,
    name: str,
    *,
    is_private: bool,
    is_im: bool = False,
    is_mpim: bool = False,
    user_id: str | None = None,
    members: list[str] | None = None,
) -> SlackChannel:
    now = now_seconds()
    bot_user_id = get_bot_user_id()
    is_direct = is_im or is_mpim
    return SlackChannel(
        id=channel_id,
        name=name,
        name_normalized=name,
        is_channel=not is_private and not is_direct,
        is_group=is_private and not is_direct,
        is_im=is_im,
        is_mpim=is_mpim,
        is_private=is_private,
        created=now,
        is_archived=False,
        is_general=False,
        unlinked=0,
        is_shared=False,
        is_org_shared=False,
        is_ext_shared=False,
        is_pending_ext_shared=False,
        pending_shared=[],
        pending_connected_team_ids=[],
        context_team_id="T_MOCK",
        updated=now,
        creator=bot_user_id,
        shared_team_ids=["T_MOCK"],
        is_member=True,
        user=user_id,
        members=members,
        num_members=len(members) if members else (1 if not is_direct else None),
        topic=SlackChannelTopic(value="", creator="", last_set=0) if not is_direct else None,
        purpose=SlackChannelTopic(value="", creator="", last_set=0) if not is_direct else None,
        previous_names=[] if not is_direct else None,
    )


def is_named_channel(channel: SlackChannel) -> bool:
    return not channel.is_im and not channel.is_mpim and (channel.is_channel or channel.is_group)


def is_direct_conversation(channel: SlackChannel) -> bool:
    return channel.is_im or channel.is_mpim
