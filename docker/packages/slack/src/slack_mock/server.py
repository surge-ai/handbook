"""Python Slack mock MCP server."""

from __future__ import annotations

import functools
import inspect
from typing import Annotated, Any

from fastmcp import FastMCP
from pydantic import Field

from slack_mock.models import SlackMockState, SlackStatusEmoji
from slack_mock.state import set_active_workspace, write_snapshots
from slack_mock.tools import channels, dms, files, messages, reactions_pins, users
from slack_mock.tools import state as state_tools
from slack_mock.tools.common import (
    ChannelIdArg,
    CursorArg,
    FileContentBase64Arg,
    FilenameArg,
    LimitArg,
    MessageTextArg,
    ReactionArg,
    SearchQueryArg,
    TimestampArg,
    UserIdArg,
    UserIdsArg,
    WorkspaceIdArg,
)

mcp = FastMCP("slack-mock-service")


def _snapshot_on_write(fn):
    @functools.wraps(fn)
    async def wrapper(*args, **kwargs):
        result = fn(*args, **kwargs)
        if inspect.isawaitable(result):
            result = await result
        write_snapshots()
        return result

    return wrapper


def _with_workspace(fn):
    @functools.wraps(fn)
    async def wrapper(*args, **kwargs):
        workspace_id = kwargs.pop("workspace_id", None)
        if workspace_id is not None:
            set_active_workspace(workspace_id)
        result = fn(*args, **kwargs)
        if inspect.isawaitable(result):
            result = await result
        return result

    return wrapper


@mcp.tool()
@_with_workspace
def list_channels(
    limit: LimitArg = 100,
    cursor: CursorArg | None = None,
    workspace_id: WorkspaceIdArg | None = None,
) -> dict[str, Any]:
    """List active public/private channels visible to the bot.

    This mirrors Slack's default conversations.list shape for normal named
    channels: archived channels, DMs, and MPIMs are excluded. Results are
    paginated with a numeric cursor returned in response_metadata.next_cursor.

    Args:
        limit: Maximum number of channels to return, capped at 200.
        cursor: Pagination cursor from a previous response.

    Returns:
        Slack-style response with channels and response_metadata.next_cursor.
    """
    return channels.list_channels(limit=limit, cursor=cursor)


@mcp.tool()
@_with_workspace
@_snapshot_on_write
def post_message(
    channel_id: ChannelIdArg,
    text: MessageTextArg,
    workspace_id: WorkspaceIdArg | None = None,
) -> dict[str, Any]:
    """Post a new top-level message to a channel, DM, or MPIM."""
    return messages.post_message(channel_id=channel_id, text=text)


@mcp.tool()
@_with_workspace
@_snapshot_on_write
def reply_to_thread(
    channel_id: ChannelIdArg,
    thread_ts: TimestampArg,
    text: MessageTextArg,
    workspace_id: WorkspaceIdArg | None = None,
) -> dict[str, Any]:
    """Reply to an existing message thread.

    Args:
        channel_id: Channel, DM, or MPIM containing the parent message.
        thread_ts: Timestamp of the parent message to reply to.
        text: Reply text.

    Returns:
        Slack-style response with the new reply timestamp and message.
    """
    return messages.reply_to_thread(channel_id=channel_id, thread_ts=thread_ts, text=text)


@mcp.tool()
@_with_workspace
@_snapshot_on_write
def add_reaction(
    channel_id: ChannelIdArg,
    timestamp: TimestampArg,
    reaction: ReactionArg,
    workspace_id: WorkspaceIdArg | None = None,
) -> dict[str, Any]:
    """Add a reaction emoji to a message.

    The reaction may be provided with or without surrounding colons, e.g.
    `thumbsup` and `:thumbsup:` are equivalent.
    """
    return reactions_pins.add_reaction(channel_id=channel_id, timestamp=timestamp, reaction=reaction)


@mcp.tool()
@_with_workspace
def get_channel_history(
    channel_id: ChannelIdArg,
    limit: LimitArg = 10,
    workspace_id: WorkspaceIdArg | None = None,
) -> dict[str, Any]:
    """Get recent top-level messages from a channel, DM, or MPIM.

    Thread replies are not returned here unless the reply is itself the
    thread parent. Use get_thread_replies with the parent timestamp to inspect
    a full thread.

    Args:
        channel_id: Channel, DM, or MPIM to read.
        limit: Maximum number of top-level messages to return.

    Returns:
        Slack-style response with messages ordered newest first.
    """
    return messages.get_channel_history(channel_id=channel_id, limit=limit)


@mcp.tool()
@_with_workspace
def get_thread_replies(
    channel_id: ChannelIdArg,
    thread_ts: TimestampArg,
    workspace_id: WorkspaceIdArg | None = None,
) -> dict[str, Any]:
    """Get a thread parent and all replies.

    Args:
        channel_id: Channel, DM, or MPIM containing the thread.
        thread_ts: Timestamp of the thread parent message.

    Returns:
        Slack-style response with the parent message followed by replies.
    """
    return messages.get_thread_replies(channel_id=channel_id, thread_ts=thread_ts)


@mcp.tool()
@_with_workspace
def get_users(
    cursor: CursorArg | None = None,
    limit: LimitArg = 100,
    workspace_id: WorkspaceIdArg | None = None,
) -> dict[str, Any]:
    """Get workspace users with basic profile information.

    Args:
        cursor: Pagination cursor from a previous response.
        limit: Maximum number of users to return, capped at 200.

    Returns:
        Slack-style response with members and response_metadata.next_cursor.
    """
    return users.get_users(cursor=cursor, limit=limit)


@mcp.tool()
@_with_workspace
def get_user_profile(user_id: UserIdArg, workspace_id: WorkspaceIdArg | None = None) -> dict[str, Any]:
    """Get detailed profile information for a specific user."""
    return users.get_user_profile(user_id=user_id)


@mcp.tool()
@_with_workspace
def search_messages(
    query: SearchQueryArg,
    channel_id: ChannelIdArg | None = None,
    limit: Annotated[
        int | None,
        Field(ge=0, description="Maximum number of matches to return. Values above 100 are capped."),
    ] = None,
    cursor: CursorArg | None = None,
    workspace_id: WorkspaceIdArg | None = None,
) -> dict[str, Any]:
    """Search messages across channels, DMs, and MPIMs.

    Bare words are ANDed together and matched against message text plus sender
    names/profile fields. Double-quoted text requires exact adjacency.

    Supported filters:
      in:general          match channel name or channel ID
      from:alice          match sender by username, display name, real name, email, or user ID
      from:@alice         match sender username exactly
      after:2026-05-01    messages at or after a date or Unix timestamp
      before:2026-05-02   messages before a date or Unix timestamp
      during:2026-05      messages during a year, month, day, or parseable date
      has:link            messages containing a URL or linked attachment/file
      has:reaction        messages with reactions
      has:pin             pinned messages

    Unsupported or malformed operators are reported in response_metadata.warnings
    while preserving best-effort search behavior. `from:me` is unsupported
    because caller identity is not modeled and will match no users.

    Args:
        query: Search text with optional Slack-style filters.
        channel_id: Optional channel/DM/MPIM scope.
        limit: Maximum matches to return, capped at 100.
        cursor: Pagination cursor from a previous response.

    Returns:
        Slack-style search response with matches, total, next_cursor, and warnings.
    """
    return messages.search_messages(query=query, channel_id=channel_id, limit=limit, cursor=cursor)


@mcp.tool()
@_with_workspace
@_snapshot_on_write
def create_channel(
    name: Annotated[str, Field(min_length=1, description="Channel name.")],
    is_private: bool = False,
    workspace_id: WorkspaceIdArg | None = None,
) -> dict[str, Any]:
    """Create a new public or private channel.

    Channel names are normalized to lowercase. Duplicates are rejected against
    existing named public/private channels, but DMs and MPIMs are ignored for
    name uniqueness.
    """
    return channels.create_channel(name=name, is_private=is_private)


@mcp.tool()
@_with_workspace
@_snapshot_on_write
def archive_channel(channel_id: ChannelIdArg, workspace_id: WorkspaceIdArg | None = None) -> dict[str, Any]:
    """Archive a named public/private channel.

    DMs and MPIMs cannot be archived and return
    method_not_supported_for_channel_type.
    """
    return channels.archive_channel(channel_id=channel_id)


@mcp.tool()
@_with_workspace
@_snapshot_on_write
def rename_channel(
    channel_id: ChannelIdArg,
    name: Annotated[str, Field(min_length=1, description="New channel name.")],
    workspace_id: WorkspaceIdArg | None = None,
) -> dict[str, Any]:
    """Rename a named public/private channel.

    Names are normalized to lowercase. DMs and MPIMs cannot be renamed.
    Duplicate names among named channels are rejected.
    """
    return channels.rename_channel(channel_id=channel_id, name=name)


@mcp.tool()
@_with_workspace
@_snapshot_on_write
def set_channel_topic(
    channel_id: ChannelIdArg,
    topic: Annotated[str | None, Field(description="New channel topic.")] = None,
    purpose: Annotated[str | None, Field(description="New channel purpose.")] = None,
    workspace_id: WorkspaceIdArg | None = None,
) -> dict[str, Any]:
    """Set a channel's topic and/or purpose.

    Pass either `topic`, `purpose`, or both. Omitted fields are left unchanged.

    Args:
        channel_id: Channel to update.
        topic: New topic text, if changing the topic.
        purpose: New purpose text, if changing the purpose.

    Returns:
        Slack-style response with the updated channel.
    """
    return channels.set_channel_topic(channel_id=channel_id, topic=topic, purpose=purpose)


@mcp.tool()
@_with_workspace
@_snapshot_on_write
def edit_message(
    channel_id: ChannelIdArg,
    ts: TimestampArg,
    text: MessageTextArg,
    workspace_id: WorkspaceIdArg | None = None,
) -> dict[str, Any]:
    """Edit an existing message.

    In normal mode, only messages authored by the bot can be edited. In admin
    mode (`is_admin: true` in state), any message can be edited.
    """
    return messages.edit_message(channel_id=channel_id, ts=ts, text=text)


@mcp.tool()
@_with_workspace
@_snapshot_on_write
def delete_message(
    channel_id: ChannelIdArg,
    ts: TimestampArg,
    workspace_id: WorkspaceIdArg | None = None,
) -> dict[str, Any]:
    """Delete a message from a channel, DM, or MPIM.

    In normal mode, only messages authored by the bot can be deleted. In admin
    mode (`is_admin: true` in state), any message can be deleted. Deleting a
    thread parent also removes its replies.
    """
    return messages.delete_message(channel_id=channel_id, ts=ts)


@mcp.tool()
@_with_workspace
@_snapshot_on_write
def pin_message(
    channel_id: ChannelIdArg,
    timestamp: TimestampArg,
    workspace_id: WorkspaceIdArg | None = None,
) -> dict[str, Any]:
    """Pin a message to a channel, DM, or MPIM."""
    return reactions_pins.pin_message(channel_id=channel_id, timestamp=timestamp)


@mcp.tool()
@_with_workspace
@_snapshot_on_write
def unpin_message(
    channel_id: ChannelIdArg,
    timestamp: TimestampArg,
    workspace_id: WorkspaceIdArg | None = None,
) -> dict[str, Any]:
    """Unpin a previously pinned message from a channel, DM, or MPIM."""
    return reactions_pins.unpin_message(channel_id=channel_id, timestamp=timestamp)


@mcp.tool()
@_with_workspace
def list_pins(channel_id: ChannelIdArg, workspace_id: WorkspaceIdArg | None = None) -> dict[str, Any]:
    """List all pinned messages in a channel, DM, or MPIM."""
    return reactions_pins.list_pins(channel_id=channel_id)


@mcp.tool()
@_with_workspace
@_snapshot_on_write
def set_user_status(
    status_text: str,
    status_emoji: SlackStatusEmoji | None = None,
    user_id: UserIdArg | None = None,
    workspace_id: WorkspaceIdArg | None = None,
) -> dict[str, Any]:
    """Set a Slack user's status text and emoji.

    By default this updates the bot user's profile. Passing `user_id` updates
    another user only when admin mode is enabled (`is_admin: true` in state).
    Status emoji must be empty or colon-wrapped, e.g. `:spiral_calendar_pad:`.

    Args:
        status_text: Status text to show on the user profile.
        status_emoji: Optional Slack emoji code.
        user_id: Optional target user ID. Requires admin mode unless it is the bot.

    Returns:
        Slack-style response with the updated profile.
    """
    return users.set_user_status(status_text=status_text, status_emoji=status_emoji, user_id=user_id)


@mcp.tool()
@_with_workspace
def get_user_presence(user_id: UserIdArg, workspace_id: WorkspaceIdArg | None = None) -> dict[str, Any]:
    """Get a user's current presence status."""
    return users.get_user_presence(user_id=user_id)


@mcp.tool()
@_with_workspace
@_snapshot_on_write
def upload_file(
    channel_id: ChannelIdArg,
    filename: FilenameArg,
    content_base64: FileContentBase64Arg,
    title: str | None = None,
    initial_comment: str | None = None,
    workspace_id: WorkspaceIdArg | None = None,
) -> dict[str, Any]:
    """Upload a file to a channel, DM, or MPIM.

    The file body must be base64-encoded. The mock infers mimetype/filetype
    from the filename extension, creates a hosted Slack file object, and posts
    a file_share message in the target conversation.

    Args:
        channel_id: Conversation to upload into.
        filename: Stored filename, including extension when known.
        content_base64: Base64-encoded file bytes.
        title: Optional display title. Defaults to filename.
        initial_comment: Optional message text for the file_share post.

    Returns:
        Slack-style response with the created file object.
    """
    return files.upload_file(
        channel_id=channel_id,
        filename=filename,
        content_base64=content_base64,
        title=title,
        initial_comment=initial_comment,
    )


@mcp.tool()
@_with_workspace
def list_files(
    channel_id: ChannelIdArg,
    limit: LimitArg = 20,
    workspace_id: WorkspaceIdArg | None = None,
) -> dict[str, Any]:
    """List files shared in a channel, DM, or MPIM, newest first."""
    return files.list_files(channel_id=channel_id, limit=limit)


@mcp.tool()
@_with_workspace
@_snapshot_on_write
def open_dm(user_id: UserIdArg, workspace_id: WorkspaceIdArg | None = None) -> dict[str, Any]:
    """Open or reuse a direct message conversation with one user.

    If a DM with the user already exists, that channel is returned. Otherwise
    a new DM channel is created.
    """
    return dms.open_dm(user_id=user_id)


@mcp.tool()
@_with_workspace
@_snapshot_on_write
def open_mpim(user_ids: UserIdsArg, workspace_id: WorkspaceIdArg | None = None) -> dict[str, Any]:
    """Open or reuse a multi-party direct message conversation.

    The bot user is included automatically. At least two non-bot users are
    required. Duplicate user IDs are ignored. If an MPIM with the same member
    set already exists, it is returned.

    Args:
        user_ids: Users to include in the MPIM, excluding or including the bot.

    Returns:
        Slack-style response with the MPIM channel.
    """
    return dms.open_mpim(user_ids=user_ids)


@mcp.tool()
@_with_workspace
def list_dms(limit: LimitArg = 20, workspace_id: WorkspaceIdArg | None = None) -> dict[str, Any]:
    """List direct message and multi-party direct message conversations."""
    return dms.list_dms(limit=limit)


@mcp.tool()
@_with_workspace
@_snapshot_on_write
def send_dm(user_id: UserIdArg, text: MessageTextArg, workspace_id: WorkspaceIdArg | None = None) -> dict[str, Any]:
    """Send a direct message to a user.

    Opens or reuses the DM first, then posts the message.
    """
    return dms.send_dm(user_id=user_id, text=text)


@mcp.tool()
@_with_workspace
@_snapshot_on_write
def send_mpim(
    user_ids: UserIdsArg,
    text: MessageTextArg,
    workspace_id: WorkspaceIdArg | None = None,
) -> dict[str, Any]:
    """Send a message to a multi-party direct message conversation.

    Opens or reuses the MPIM for the provided member set, then posts the
    message. At least two non-bot users are required.
    """
    return dms.send_mpim(user_ids=user_ids, text=text)


@mcp.tool()
async def list_workspaces() -> dict[str, Any]:
    """List available Slack workspaces in the loaded mock state."""
    return state_tools.list_workspaces()


@mcp.tool()
async def export_state() -> SlackMockState:
    """Export the full Slack state as JSON."""
    return state_tools.export_state()


@mcp.tool()
@_snapshot_on_write
def import_state(state: dict[str, Any]) -> dict[str, bool]:
    """Replace the full Slack state with the provided JSON."""
    return state_tools.import_state(state)
