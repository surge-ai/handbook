"""Pydantic models for Slack state."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator


class StrictStateModel(BaseModel):
    """Base model for persisted Slack state."""

    model_config = ConfigDict(extra="forbid")


NonEmptyString = Annotated[str, StringConstraints(min_length=1)]
SlackChannelId = Annotated[str, StringConstraints(pattern=r"^[CDG][A-Za-z0-9_]+$")]
SlackFileId = Annotated[str, StringConstraints(pattern=r"^F[A-Za-z0-9_]+$")]
SlackTeamId = Annotated[str, StringConstraints(pattern=r"^T[A-Za-z0-9_]+$")]
SlackUserId = Annotated[str, StringConstraints(pattern=r"^[UW][A-Za-z0-9_]+$")]
SlackTs = Annotated[str, StringConstraints(pattern=r"^\d{10}\.\d{3,6}$")]
SlackStatusEmoji = Annotated[str, StringConstraints(pattern=r"^$|^:[a-z0-9_+-]+:$")]
SlackWorkspaceId = NonEmptyString


def _non_empty(value: str, field_name: str) -> str:
    if not value.strip():
        raise ValueError(f"{field_name} must be non-empty")
    return value


class SlackMessageType(StrEnum):
    MESSAGE = "message"


class SlackMessageSubtype(StrEnum):
    BOT_MESSAGE = "bot_message"
    CHANNEL_ARCHIVE = "channel_archive"
    CHANNEL_JOIN = "channel_join"
    CHANNEL_LEAVE = "channel_leave"
    CHANNEL_NAME = "channel_name"
    CHANNEL_PURPOSE = "channel_purpose"
    CHANNEL_TOPIC = "channel_topic"
    CHANNEL_UNARCHIVE = "channel_unarchive"
    FILE_SHARE = "file_share"
    HUDDLE_THREAD = "huddle_thread"
    ME_MESSAGE = "me_message"
    MESSAGE_CHANGED = "message_changed"
    MESSAGE_DELETED = "message_deleted"
    THREAD_BROADCAST = "thread_broadcast"


class SlackTwoFactorType(StrEnum):
    APP = "app"
    SMS = "sms"


class SlackFileMode(StrEnum):
    HOSTED = "hosted"
    EXTERNAL = "external"
    SNIPPET = "snippet"
    POST = "post"


class SlackBlockType(StrEnum):
    ACTIONS = "actions"
    CONTEXT = "context"
    DIVIDER = "divider"
    HEADER = "header"
    IMAGE = "image"
    INPUT = "input"
    SECTION = "section"
    VIDEO = "video"


class SlackTextObjectType(StrEnum):
    MARKDOWN = "mrkdwn"
    PLAIN_TEXT = "plain_text"


class SlackStatusEmojiDisplayInfo(StrictStateModel):
    emoji_name: str
    display_url: str


class SlackProfileField(StrictStateModel):
    value: str
    alt: str | None = None


class SlackUserProfile(StrictStateModel):
    avatar_hash: str | None = None
    status_text: str | None = None
    status_emoji: SlackStatusEmoji | None = None
    status_emoji_display_info: list[SlackStatusEmojiDisplayInfo] | None = None
    status_expiration: int | None = None
    status_text_canonical: str | None = None
    real_name: str | None = None
    display_name: str | None = None
    real_name_normalized: str | None = None
    display_name_normalized: str | None = None
    email: str | None = None
    pronouns: str | None = None
    huddle_state: str | None = None
    huddle_state_expiration_ts: int | None = None
    first_name: str | None = None
    last_name: str | None = None
    title: str | None = None
    phone: str | None = None
    skype: str | None = None
    start_date: str | None = None
    team: str | None = None
    fields: dict[str, SlackProfileField] | None = None
    image_original: str | None = None
    is_custom_image: bool | None = None
    image_24: str | None = None
    image_32: str | None = None
    image_48: str | None = None
    image_72: str | None = None
    image_192: str | None = None
    image_512: str | None = None
    image_1024: str | None = None


class SlackEnterpriseUser(StrictStateModel):
    id: NonEmptyString
    enterprise_id: NonEmptyString
    enterprise_name: NonEmptyString
    is_admin: bool
    is_owner: bool
    is_primary_owner: bool | None = None
    teams: list[str]


class SlackUser(StrictStateModel):
    id: SlackUserId
    team_id: SlackTeamId = "T_MOCK"
    name: NonEmptyString
    deleted: bool = False
    color: str | None = None
    real_name: str | None = None
    tz: str | None = None
    tz_label: str | None = None
    tz_offset: int | None = None
    profile: SlackUserProfile = Field(default_factory=SlackUserProfile)
    is_admin: bool | None = None
    is_owner: bool | None = None
    is_primary_owner: bool | None = None
    is_restricted: bool | None = None
    is_ultra_restricted: bool | None = None
    is_bot: bool | None = None
    is_app_user: bool | None = None
    is_email_confirmed: bool | None = None
    is_invited_user: bool | None = None
    is_stranger: bool | None = None
    updated: int | None = None
    has_2fa: bool | None = None
    two_factor_type: SlackTwoFactorType | None = None
    locale: str | None = None
    who_can_share_contact_card: str | None = None
    always_active: bool | None = None
    enterprise_user: SlackEnterpriseUser | None = None

    @field_validator("id", "name")
    @classmethod
    def _required_strings(cls, value: str) -> str:
        return _non_empty(value, "user field")


class SlackChannelTopic(StrictStateModel):
    value: str = ""
    creator: SlackUserId | Literal[""] = ""
    last_set: int = 0


class SlackChannelPostingRestrictions(StrictStateModel):
    type: list[str] | None = None
    subteam: list[str] | None = None
    user: list[str] | None = None


class SlackChannelTab(StrictStateModel):
    id: NonEmptyString
    label: NonEmptyString
    type: NonEmptyString
    data: Any | None = None
    is_disabled: bool | None = None


class SlackChannelProperties(StrictStateModel):
    posting_restricted_to: SlackChannelPostingRestrictions | None = None
    threads_restricted_to: SlackChannelPostingRestrictions | None = None
    at_channel_restricted: bool | None = None
    at_here_restricted: bool | None = None
    huddles_restricted: bool | None = None
    sharing_disabled: bool | None = None
    tabs: list[SlackChannelTab] | None = None
    default_tab_id: str | None = None
    auto_open_tab_id: str | None = None
    membership_limit: int | None = None
    canvas: Any | None = None


class SlackChannel(StrictStateModel):
    id: SlackChannelId
    name: NonEmptyString
    is_channel: bool = True
    is_group: bool = False
    is_im: bool = False
    is_mpim: bool = False
    is_private: bool = False
    created: int = 0
    is_archived: bool = False
    is_general: bool = False
    is_frozen: bool | None = None
    is_read_only: bool | None = None
    is_thread_only: bool | None = None
    unlinked: int = 0
    name_normalized: str | None = None
    is_shared: bool = False
    is_org_shared: bool = False
    is_ext_shared: bool = False
    is_pending_ext_shared: bool = False
    pending_shared: list[str] = Field(default_factory=list)
    pending_connected_team_ids: list[str] = Field(default_factory=list)
    context_team_id: SlackTeamId = "T_MOCK"
    updated: int = 0
    parent_conversation: SlackChannelId | None = None
    creator: SlackUserId | Literal[""] = ""
    shared_team_ids: list[SlackTeamId] = Field(default_factory=lambda: ["T_MOCK"])
    is_member: bool = True
    conversation_host_id: str | None = None
    topic: SlackChannelTopic | None = None
    purpose: SlackChannelTopic | None = None
    properties: SlackChannelProperties | None = None
    previous_names: list[str] | None = None
    num_members: int | None = None
    last_read: SlackTs | None = None
    unread_count: int | None = None
    unread_count_display: int | None = None
    latest: dict[str, Any] | None = None
    user: SlackUserId | None = None
    members: list[SlackUserId] | None = None

    @field_validator("id", "name")
    @classmethod
    def _required_strings(cls, value: str) -> str:
        return _non_empty(value, "channel field")

    @field_validator("members")
    @classmethod
    def _members_are_unique(cls, value: list[str] | None) -> list[str] | None:
        if value is not None and len(set(value)) != len(value):
            raise ValueError("channel members must be unique")
        return value

    @model_validator(mode="after")
    def _normalize_name(self) -> Self:
        if self.name_normalized is None:
            self.name_normalized = self.name
        kinds = [self.is_channel, self.is_group, self.is_im, self.is_mpim]
        if sum(1 for kind in kinds if kind) != 1:
            raise ValueError("exactly one Slack conversation kind flag must be true")
        if self.is_im and not self.user:
            raise ValueError("DM channels require user")
        if self.is_channel and self.is_private:
            raise ValueError("public channel flag is inconsistent with is_private")
        if (self.is_group or self.is_im or self.is_mpim) and not self.is_private:
            raise ValueError("private conversation flags require is_private")
        return self


class SlackReaction(StrictStateModel):
    name: NonEmptyString
    users: list[SlackUserId] = Field(default_factory=list)
    count: int

    @field_validator("name")
    @classmethod
    def _name_required(cls, value: str) -> str:
        return _non_empty(value, "reaction.name").strip(":")

    @model_validator(mode="after")
    def _count_matches_users(self) -> Self:
        if self.count != len(self.users):
            raise ValueError("reaction.count must match number of reaction users")
        if len(set(self.users)) != len(self.users):
            raise ValueError("reaction.users must be unique")
        return self


class SlackAttachmentField(StrictStateModel):
    title: NonEmptyString
    value: str
    short: bool


class SlackAttachmentActionConfirm(StrictStateModel):
    title: str | None = None
    text: NonEmptyString
    ok_text: str | None = None
    dismiss_text: str | None = None


class SlackAttachmentActionOption(StrictStateModel):
    text: NonEmptyString
    value: NonEmptyString


class SlackAttachmentActionOptionGroup(StrictStateModel):
    text: NonEmptyString
    options: list[SlackAttachmentActionOption]


class SlackAttachmentAction(StrictStateModel):
    id: str | None = None
    name: NonEmptyString
    text: NonEmptyString
    type: NonEmptyString
    value: str | None = None
    style: str | None = None
    url: str | None = None
    confirm: SlackAttachmentActionConfirm | None = None
    options: list[SlackAttachmentActionOption] | None = None
    option_groups: list[SlackAttachmentActionOptionGroup] | None = None
    data_source: str | None = None
    selected_options: list[SlackAttachmentActionOption] | None = None
    min_query_length: int | None = None


class SlackAttachment(StrictStateModel):
    fallback: str | None = None
    color: str | None = None
    pretext: str | None = None
    author_name: str | None = None
    author_link: str | None = None
    author_icon: str | None = None
    author_subname: str | None = None
    title: str | None = None
    title_link: str | None = None
    text: str | None = None
    fields: list[SlackAttachmentField] | None = None
    image_url: str | None = None
    thumb_url: str | None = None
    thumb_width: int | None = None
    thumb_height: int | None = None
    footer: str | None = None
    footer_icon: str | None = None
    ts: str | None = None
    mrkdwn_in: list[str] | None = None
    actions: list[SlackAttachmentAction] | None = None
    callback_id: str | None = None
    id: int | None = None


class SlackTextObject(StrictStateModel):
    type: SlackTextObjectType
    text: str
    emoji: bool | None = None
    verbatim: bool | None = None


class SlackBlockElement(StrictStateModel):
    type: NonEmptyString
    text: str | SlackTextObject | None = None
    elements: list[SlackBlockElement] | None = None


class SlackBlock(StrictStateModel):
    type: SlackBlockType
    block_id: str | None = None
    elements: list[SlackBlockElement] | None = None
    text: SlackTextObject | None = None
    accessory: SlackBlockElement | None = None
    fields: list[SlackTextObject] | None = None
    image_url: str | None = None
    alt_text: str | None = None
    title: SlackTextObject | None = None


class SlackFileShares(StrictStateModel):
    public: dict[str, list[Any]] | None = None
    private: dict[str, list[Any]] | None = None


class SlackFile(StrictStateModel):
    id: SlackFileId
    created: int | None = None
    timestamp: int | None = None
    name: str | None = None
    title: str | None = None
    mimetype: str | None = None
    filetype: str | None = None
    pretty_type: str | None = None
    user: SlackUserId | None = None
    user_team: SlackTeamId | None = None
    editable: bool | None = None
    size: int | None = None
    mode: SlackFileMode | None = None
    is_external: bool | None = None
    external_type: str | None = None
    is_public: bool | None = None
    public_url_shared: bool | None = None
    display_as_bot: bool | None = None
    username: str | None = None
    url_private: str | None = None
    url_private_download: str | None = None
    thumb_64: str | None = None
    thumb_80: str | None = None
    thumb_360: str | None = None
    thumb_360_w: int | None = None
    thumb_360_h: int | None = None
    thumb_480: str | None = None
    thumb_480_w: int | None = None
    thumb_480_h: int | None = None
    thumb_160: str | None = None
    thumb_720: str | None = None
    thumb_720_w: int | None = None
    thumb_720_h: int | None = None
    thumb_800: str | None = None
    thumb_800_w: int | None = None
    thumb_800_h: int | None = None
    thumb_960: str | None = None
    thumb_960_w: int | None = None
    thumb_960_h: int | None = None
    thumb_1024: str | None = None
    thumb_1024_w: int | None = None
    thumb_1024_h: int | None = None
    original_w: int | None = None
    original_h: int | None = None
    thumb_tiny: str | None = None
    permalink: str | None = None
    permalink_public: str | None = None
    edit_link: str | None = None
    preview: str | None = None
    preview_highlight: str | None = None
    lines: int | None = None
    lines_more: int | None = None
    preview_is_truncated: bool | None = None
    comments_count: int | None = None
    is_starred: bool | None = None
    shares: SlackFileShares | None = None
    channels: list[SlackChannelId] | None = None
    groups: list[SlackChannelId] | None = None
    ims: list[SlackChannelId] | None = None
    has_rich_preview: bool | None = None

    @field_validator("id")
    @classmethod
    def _id_required(cls, value: str) -> str:
        return _non_empty(value, "file.id")


class SlackMessageEdited(StrictStateModel):
    user: SlackUserId
    ts: SlackTs


class SlackHuddleRoom(StrictStateModel):
    id: NonEmptyString
    name: str | None = None
    media_server: str | None = None
    created_by: SlackUserId
    date_start: int
    date_end: int
    participants: list[SlackUserId]
    participant_history: list[SlackUserId]
    participants_camera_on: list[SlackUserId]
    participants_camera_off: list[SlackUserId]
    participants_screenshare_on: list[SlackUserId]
    participants_screenshare_off: list[SlackUserId]
    canvas_thread_ts: SlackTs | None = None
    thread_root_ts: SlackTs | None = None
    channels: list[SlackChannelId]
    is_dm_call: bool
    was_rejected: bool
    was_missed: bool
    was_accepted: bool
    has_ended: bool
    background_id: str | None = None
    canvas_background: str | None = None
    is_prewarmed: bool
    is_scheduled: bool
    attached_file_ids: list[SlackFileId]
    media_backend_type: str | None = None
    display_id: str | None = None
    external_unique_id: str | None = None
    app_id: str | None = None
    call_family: str | None = None
    pending_invitees: dict[str, Any] | None = None
    last_invite_status_by_user: dict[str, Any] | None = None


class SlackBotProfile(StrictStateModel):
    id: NonEmptyString
    deleted: bool | None = None
    name: str
    updated: int | None = None
    app_id: str
    icons: dict[str, str] | None = None
    team_id: SlackTeamId


class SlackMessageMetadata(StrictStateModel):
    event_type: str
    event_payload: dict[str, Any]


class SlackMessage(StrictStateModel):
    type: SlackMessageType = SlackMessageType.MESSAGE
    subtype: SlackMessageSubtype | None = None
    user: SlackUserId | None = None
    bot_id: str | None = None
    bot_profile: SlackBotProfile | None = None
    text: str
    ts: SlackTs
    thread_ts: SlackTs | None = None
    parent_user_id: SlackUserId | None = None
    reply_count: int | None = None
    reply_users_count: int | None = None
    latest_reply: SlackTs | None = None
    reply_users: list[SlackUserId] | None = None
    is_locked: bool | None = None
    subscribed: bool | None = None
    team: SlackTeamId | None = None
    channel: SlackChannelId | None = None
    is_starred: bool | None = None
    pinned_to: list[SlackChannelId] | None = None
    pinned_info: dict[str, dict[str, Any]] | None = None
    reactions: list[SlackReaction] | None = None
    attachments: list[SlackAttachment] | None = None
    blocks: list[SlackBlock] | None = None
    files: list[SlackFile] | None = None
    upload: bool | None = None
    display_as_bot: bool | None = None
    edited: SlackMessageEdited | None = None
    client_msg_id: str | None = None
    permalink: str | None = None
    no_notifications: bool | None = None
    room: SlackHuddleRoom | None = None
    metadata: SlackMessageMetadata | None = None

    @model_validator(mode="after")
    def _reply_counts_are_consistent(self) -> Self:
        if (
            self.reply_users is not None
            and self.reply_users_count is not None
            and self.reply_users_count != len(self.reply_users)
        ):
            raise ValueError("reply_users_count must match reply_users length")
        return self


class SlackCounters(StrictStateModel):
    channelId: int = 1_000
    fileId: int = 1_000


class SlackState(StrictStateModel):
    users: dict[SlackUserId, SlackUser] = Field(default_factory=dict)
    channels: dict[SlackChannelId, SlackChannel] = Field(default_factory=dict)
    messages: dict[SlackChannelId, list[SlackMessage]] = Field(default_factory=dict)
    bot_user_id: SlackUserId | None = None
    is_admin: bool = False
    counters: SlackCounters = Field(default_factory=SlackCounters)

    @model_validator(mode="after")
    def _keys_and_references_are_consistent(self) -> Self:
        if self.bot_user_id is not None and self.bot_user_id not in self.users:
            raise ValueError(f"bot_user_id {self.bot_user_id!r} does not reference an existing user")

        for key, user in self.users.items():
            if key != user.id:
                raise ValueError(f"users key {key!r} does not match user.id {user.id!r}")
        user_names: dict[str, str] = {}
        for user in self.users.values():
            normalized_name = user.name.casefold()
            if normalized_name in user_names:
                raise ValueError(
                    f"user name {user.name!r} is shared by {user_names[normalized_name]!r} and {user.id!r}"
                )
            user_names[normalized_name] = user.id

        for key, channel in self.channels.items():
            if key != channel.id:
                raise ValueError(f"channels key {key!r} does not match channel.id {channel.id!r}")
            if channel.creator and channel.creator not in self.users:
                raise ValueError(f"channel {key!r} creator {channel.creator!r} does not reference an existing user")
            for field_name in ("topic", "purpose"):
                topic = getattr(channel, field_name)
                if topic is not None and topic.creator and topic.creator not in self.users:
                    raise ValueError(
                        f"channel {key!r} {field_name}.creator {topic.creator!r} does not reference an existing user"
                    )
            if channel.user is not None and channel.user not in self.users:
                raise ValueError(f"channel {key!r} user {channel.user!r} does not reference an existing user")
            for member in channel.members or []:
                if member not in self.users:
                    raise ValueError(f"channel {key!r} member {member!r} does not reference an existing user")

        for channel_id, messages in self.messages.items():
            if channel_id not in self.channels:
                raise ValueError(f"messages key {channel_id!r} does not reference an existing channel")
            seen: set[str] = set()
            message_by_ts = {message.ts: message for message in messages}
            for message in messages:
                if message.ts in seen:
                    raise ValueError(f"duplicate message timestamp {message.ts!r} in channel {channel_id!r}")
                seen.add(message.ts)
                if message.thread_ts and message.thread_ts != message.ts and message.thread_ts not in message_by_ts:
                    raise ValueError(f"thread reply {message.ts!r} references missing parent {message.thread_ts!r}")
                if message.channel is not None and message.channel != channel_id:
                    raise ValueError(f"message.channel {message.channel!r} does not match messages key {channel_id!r}")
                self._validate_message_user_references(channel_id, message)
                self._validate_file_channel_references(channel_id, message)
                if message.pinned_to:
                    for pinned_channel in message.pinned_to:
                        if pinned_channel not in self.channels:
                            raise ValueError(f"message {message.ts!r} pinned to unknown channel {pinned_channel!r}")
                self._validate_pin_info(message)
            self._validate_thread_metadata(channel_id, messages, message_by_ts)
        return self

    def _validate_message_user_references(self, channel_id: str, message: SlackMessage) -> None:
        if message.user is not None and message.user not in self.users:
            raise ValueError(
                f"message {message.ts!r} in channel {channel_id!r} user {message.user!r} does not reference an existing user"
            )
        if message.parent_user_id is not None and message.parent_user_id not in self.users:
            raise ValueError(
                f"message {message.ts!r} in channel {channel_id!r} parent_user_id {message.parent_user_id!r} does not reference an existing user"
            )
        if message.edited is not None and message.edited.user not in self.users:
            raise ValueError(
                f"message {message.ts!r} edited.user {message.edited.user!r} does not reference an existing user"
            )
        for reply_user in message.reply_users or []:
            if reply_user not in self.users:
                raise ValueError(
                    f"message {message.ts!r} reply user {reply_user!r} does not reference an existing user"
                )
        for reaction in message.reactions or []:
            for reaction_user in reaction.users:
                if reaction_user not in self.users:
                    raise ValueError(
                        f"message {message.ts!r} reaction user {reaction_user!r} does not reference an existing user"
                    )
        for file in message.files or []:
            if file.user is not None and file.user not in self.users:
                raise ValueError(f"message {message.ts!r} file user {file.user!r} does not reference an existing user")
        if message.room is not None:
            room_users = [
                message.room.created_by,
                *message.room.participants,
                *message.room.participant_history,
                *message.room.participants_camera_on,
                *message.room.participants_camera_off,
                *message.room.participants_screenshare_on,
                *message.room.participants_screenshare_off,
            ]
            for room_user in room_users:
                if room_user not in self.users:
                    raise ValueError(
                        f"message {message.ts!r} huddle user {room_user!r} does not reference an existing user"
                    )

    def _validate_file_channel_references(self, channel_id: str, message: SlackMessage) -> None:
        for file in message.files or []:
            for file_channel in file.channels or []:
                if file_channel not in self.channels:
                    raise ValueError(
                        f"message {message.ts!r} file channel {file_channel!r} does not reference an existing channel"
                    )
            for file_group in file.groups or []:
                if file_group not in self.channels:
                    raise ValueError(
                        f"message {message.ts!r} file group {file_group!r} does not reference an existing channel"
                    )
            for file_im in file.ims or []:
                if file_im not in self.channels:
                    raise ValueError(
                        f"message {message.ts!r} file im {file_im!r} does not reference an existing channel"
                    )
        if message.room is not None:
            for room_channel in message.room.channels:
                if room_channel not in self.channels:
                    raise ValueError(
                        f"message {message.ts!r} huddle channel {room_channel!r} does not reference an existing channel"
                    )
        if message.channel is not None and message.channel != channel_id:
            raise ValueError(f"message.channel {message.channel!r} does not match messages key {channel_id!r}")

    def _validate_pin_info(self, message: SlackMessage) -> None:
        pinned_to = set(message.pinned_to or [])
        pinned_info = message.pinned_info or {}
        if pinned_info and not pinned_to:
            raise ValueError(f"message {message.ts!r} has pinned_info without pinned_to")
        extra_info_channels = set(pinned_info) - pinned_to
        if extra_info_channels:
            raise ValueError(f"message {message.ts!r} pinned_info contains channels not in pinned_to")
        for pinned_channel in pinned_to:
            info = pinned_info.get(pinned_channel)
            if info is None:
                continue
            pinned_by = info.get("pinned_by")
            if pinned_by is not None and pinned_by not in self.users:
                raise ValueError(f"message {message.ts!r} pinned_by {pinned_by!r} does not reference an existing user")
            pinned_ts = info.get("pinned_ts")
            if pinned_ts is not None and not isinstance(pinned_ts, int):
                raise ValueError(f"message {message.ts!r} pinned_ts for channel {pinned_channel!r} must be an integer")

    def _validate_thread_metadata(
        self,
        channel_id: str,
        messages: list[SlackMessage],
        message_by_ts: dict[str, SlackMessage],
    ) -> None:
        replies_by_parent: dict[str, list[SlackMessage]] = {}
        for message in messages:
            if message.thread_ts and message.thread_ts != message.ts:
                replies_by_parent.setdefault(message.thread_ts, []).append(message)

        for parent_ts, replies in replies_by_parent.items():
            parent = message_by_ts[parent_ts]
            replies.sort(key=lambda reply: float(reply.ts))
            reply_users = sorted({reply.user for reply in replies if reply.user is not None})
            if parent.reply_count is not None and parent.reply_count != len(replies):
                raise ValueError(
                    f"message {parent.ts!r} reply_count does not match actual replies in channel {channel_id!r}"
                )
            if parent.reply_users_count is not None and parent.reply_users_count != len(reply_users):
                raise ValueError(
                    f"message {parent.ts!r} reply_users_count does not match actual reply users in channel {channel_id!r}"
                )
            if parent.reply_users is not None and set(parent.reply_users) != set(reply_users):
                raise ValueError(
                    f"message {parent.ts!r} reply_users does not match actual reply users in channel {channel_id!r}"
                )
            if parent.latest_reply is not None and parent.latest_reply != replies[-1].ts:
                raise ValueError(
                    f"message {parent.ts!r} latest_reply does not match latest actual reply in channel {channel_id!r}"
                )

        for message in messages:
            if (
                message.reply_count or message.reply_users_count or message.reply_users or message.latest_reply
            ) and message.ts not in replies_by_parent:
                raise ValueError(
                    f"message {message.ts!r} has reply metadata but no actual replies in channel {channel_id!r}"
                )


class SlackWorkspacesState(StrictStateModel):
    workspaces: dict[SlackWorkspaceId, SlackState] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _has_workspaces(self) -> Self:
        if not self.workspaces:
            raise ValueError("workspaces must contain at least one workspace")
        return self


SlackMockState = SlackState | SlackWorkspacesState
