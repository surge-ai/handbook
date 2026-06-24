# Slack Capabilities

A mock Slack workspace with channels, messages, threads, reactions, pins, user profiles, file sharing, direct messages, and presence/status. Supports an admin mode (via `is_admin: true` in state) where the agent can edit/delete any user's messages and manage channels.

## What the agent can do

**Send and manage messages.** Post to channels, reply to threads, edit existing messages, delete messages. Cross-channel search by text + author with pagination (`search_messages`). Search supports `in:`, `from:`, `after:`, `before:`, `during:`, and `has:link|reaction|star|pin`; `from:me` is not supported because the mock has no caller identity.

**Read conversations.** Get channel message history, view full thread replies, browse messages. `search_messages` finds across all channels simultaneously.

**Manage channels.** List all channels, create public or private channels, archive, rename (with history tracking), set channel topics and purposes.

**Reactions and pins.** Add emoji reactions to messages, pin important messages for easy reference, unpin, list all pinned messages in a channel.

**User profiles and presence.** Look up profiles (name, title, email), check if a user is online or away, and set custom status text and emoji.

**File sharing.** Upload files to channels (with automatic MIME type detection) and list files shared in a channel. Uploaded files create a message in the channel with the file attached.

**Direct messages.** Open a DM with another user, list existing DM conversations, and send DMs. DMs are separate from channel messages.

**Admin mode.** When `is_admin: true` in state, the mock user is treated as a workspace admin: `edit_message` and `delete_message` can act on any user's messages (not just the bot's own), and `archive_channel` / `rename_channel` / `set_channel_topic` work without membership restrictions.

## Coverage gaps

- No channel membership management (join/leave/invite)
- No message scheduling or reminders
- No Slack apps, bots, or integrations
- No message formatting (Block Kit) — only plain text
- No custom emoji management
- No channel notification settings
- `search_messages` returns `channel_scope_conflict` when `channel_id` and query `in:#channel` point to different channels

## Toolsets

27 tools total: 25 model-facing tools plus 2 state tools. Toolsets map to `WORLDBENCH_TOOL_SETS` values (prefixed form — e.g., `slack_messages`).

| Toolset | Tools | Description |
|---------|-------|-------------|
| `all` / `slack_all` | 25 | All model-facing tools |
| `read` / `slack_read` | 10 | Model-facing read-only tools |
| `write` / `slack_write` | 15 | Model-facing write tools |
| `slack_messages` | 7 | Core messaging: post, reply, edit, delete, history, threads, search |
| `slack_channels` | 5 | Channel management: list, create, archive, rename, set topic |
| `slack_reactions_pins` | 4 | Reactions + pins: add reaction, pin/unpin, list pins |
| `slack_users` | 4 | User info: get users, get profile, get presence, set status |
| `slack_files` | 2 | File sharing: upload, list |
| `slack_dms` | 3 | Direct messages: open DM, list DMs, send DM |
| `slack_admin` | 3 | Admin-only operations: archive channel, rename channel, set topic (paired with `is_admin: true`) |
| `slack_core` | 11 | Baseline chat plus legacy Toolathlon reactions/profile tools |
| `slack_toolathlon_legacy` | 8 | Legacy Toolathlon tool subset (pre-integration) |
| `slack_state` | 2 | `export_state`, `import_state` for fixture seeding and grading |

**Admin mode.** Set `is_admin: true` in the world's Slack state JSON to let the agent edit/delete any user's messages and perform channel-admin operations. Default `false` restricts writes to the bot user's own messages.

**Bot user ID.** Default `U_MOCK_BOT`. Override per world by setting `bot_user_id` in the state JSON — the value round-trips through `export_state` / `import_state`.
