# Google Mail MCP

A mock Gmail MCP (Model Context Protocol) server for testing and RL environment training. It simulates a complete email system without requiring any network connectivity - all state is loaded from and persisted to a JSON file.

## Overview

This MCP server provides 20 email tools that operate on a "closed-world" simulation:

- **Closed-world contacts**: Only predefined email addresses can receive messages
- **Bounce simulation**: Emails to invalid addresses generate realistic bounce notifications
- **Group support**: Sending to a group delivers copies to members (including yourself if you're a member)
- **State persistence**: All changes are saved back to the JSON file
- **No network required**: Everything runs locally from a JSON file

## Project Structure

```
packages/google_mail/
├── google_mail.py      # Entry point for container environment
├── utils.py            # Utilities for data path computation
├── __init__.py         # Package exports
├── src/mail_mcp/       # Core MCP server implementation
│   ├── server.py       # FastMCP server with tool definitions
│   ├── models/         # Pydantic schemas
│   └── services/       # Mailbox business logic
├── examples/
│   └── sample_mailbox.json
└── tests/
```

## Installation

### Local Development

```bash
cd packages/google_mail
uv sync
```

### Container Environment

The MCP server is pre-installed in the DAT container. Data is stored in `external_services/mailbox.json` next to the agent workspace (outside the agent's filesystem access).

## Usage

### Running Locally

```bash
# Using environment variable
MAIL_MCP_DATA_PATH=/path/to/mailbox.json uv run mail-mcp

# Using CLI argument
uv run mail-mcp --data-path /path/to/mailbox.json

# With debug logging
uv run mail-mcp --data-path /path/to/mailbox.json --debug
```

### Container Usage (via MCP Config)

The server is configured in `configs/mcp_servers/google_mail.yaml`:

```yaml
type: stdio
name: google_mail
params:
  command: uv
  args:
    - "run"
    - "--project"
    - "/workspace/packages/google_mail"
    - "python"
    - "/workspace/packages/google_mail/google_mail.py"
    - "--agent-workspace"
    - "${agent_workspace}"
```

### Task Preprocessing

To set up mailbox data for a task, use the utility functions:

```python
from google_mail import create_mail_data
from pathlib import Path

# Copy mailbox.json to external_services location
create_mail_data(
    agent_workspace="/workspace/dumps/workspace",
    source_mailbox_path=Path("initial_workspace/mailbox.json"),
)
```

### MCP Client Configuration

For direct MCP client usage:

```json
{
  "mcpServers": {
    "mail": {
      "command": "uv",
      "args": ["run", "--project", "/path/to/google_mail", "mail-mcp"],
      "env": {
        "MAIL_MCP_DATA_PATH": "/path/to/mailbox.json"
      }
    }
  }
}
```

## Tools

### Email Operations

| Tool | Description |
|------|-------------|
| `mail_get_emails` | Get paginated emails from a folder |
| `mail_read_email` | Read a single email (marks as read) |
| `mail_search_emails` | Search emails by query string, including Gmail-style operators such as `from:`, `subject:`, `has:attachment`, `filename:`, `is:unread`, and `in:` |
| `mail_send_email` | Send an email |
| `mail_reply_email` | Reply to an email |
| `mail_forward_email` | Forward an email with attachments |
| `mail_delete_emails` | Delete one or more emails (moves to Trash or permanent) |
| `mail_move_emails` | Move one or more emails to a different folder |
| `mail_mark_emails` | Mark emails as read/unread/important |

### Folder Operations

| Tool | Description |
|------|-------------|
| `mail_get_folders` | List all folders with message counts |
| `mail_create_folder` | Create a new folder |
| `mail_delete_folder` | Delete a folder (system folders protected) |
| `mail_get_unread_count` | Get unread counts per folder |
| `mail_get_mailbox_stats` | Get statistics for all folders |

### Draft Operations

| Tool | Description |
|------|-------------|
| `mail_save_draft` | Save a new draft |
| `mail_get_drafts` | Get paginated list of drafts |
| `mail_update_draft` | Update an existing draft |
| `mail_delete_draft` | Delete a draft |

### Other

| Tool | Description |
|------|-------------|
| `mail_get_contacts` | List all valid contacts (closed-world) |
| `mail_download_attachment` | Download an attachment as base64 |

## Threading

Emails are linked into threads via the `in_reply_to` field, which references the parent email's `message_id`.

When using `mail_reply_email`:
1. The reply's `in_reply_to` is automatically set to the original's `message_id`
2. The reply body includes a quoted copy of the original message:

```
Your reply here...

--- Original Message ---
From: alice@example.com
Date: 2024-01-15 10:30
Subject: Meeting Tomorrow

Original message content...
```

This provides both programmatic thread linking (via `in_reply_to`) and human-readable context (via quoted original).

## JSON Schema

The mailbox data file follows this schema:

### Root Structure

```json
{
  "mailbox": { ... },
  "contacts": [ ... ],
  "folders": [ ... ],
  "emails": [ ... ],
  "drafts": [ ... ],
  "next_email_id": 1
}
```

### Fields

#### mailbox (required)
The identity of the mailbox owner. This is the "From" address when sending emails.

```json
{
  "email": "user@example.com",
  "name": "Display Name"
}
```

#### contacts (required)
List of valid email recipients. Only these addresses (plus the mailbox owner) can receive emails. Sending to any other address will generate a bounce notification.

```json
[
  {"email": "alice@example.com", "name": "Alice Smith"},
  {"email": "team@example.com", "name": "Engineering Team", "members": [
    "user@example.com",
    "alice@example.com"
  ]}
]
```

Groups have a `members` array. When you send to a group and you're a member, you receive a copy in your INBOX.

#### folders (optional)
Custom folders beyond the system defaults. System folders (INBOX, Sent, Drafts, Trash) always exist implicitly.

```json
[
  {"name": "Work"},
  {"name": "Archive/2024"}
]
```

#### emails (optional)
List of emails in the mailbox.

```json
[
  {
    "email_id": "1",
    "folder": "INBOX",
    "subject": "Meeting Tomorrow",
    "from_addr": "alice@example.com",
    "to_addr": "user@example.com",
    "cc_addr": null,
    "bcc_addr": null,
    "date": "2024-01-15T10:30:00Z",
    "message_id": "<msg001@example.com>",
    "in_reply_to": null,
    "body_text": "Plain text content here",
    "body_html": "<p>Optional HTML content</p>",
    "is_read": false,
    "is_important": false,
    "attachments": [
      {
        "filename": "document.pdf",
        "content_type": "application/pdf",
        "content_base64": "JVBERi0xLjQK..."
      }
    ]
  }
]
```

The `in_reply_to` field links replies to their parent email via `message_id`. This enables thread tracking.

#### drafts (optional)
List of draft emails.

```json
[
  {
    "draft_id": "draft_1",
    "subject": "Draft Subject",
    "body": "Draft content",
    "html_body": null,
    "to": "alice@example.com",
    "cc": null,
    "bcc": null,
    "created_at": "2024-01-15T09:00:00Z",
    "updated_at": "2024-01-15T09:30:00Z"
  }
]
```

#### next_email_id (optional)
Counter for generating new email IDs. Defaults to 1 if not specified.

## Examples

### Minimal Empty Mailbox

```json
{
  "mailbox": {
    "email": "user@example.com",
    "name": "Test User"
  },
  "contacts": [],
  "folders": [],
  "emails": [],
  "drafts": [],
  "next_email_id": 1
}
```

### Populated Mailbox

See `examples/sample_mailbox.json` for a complete example with contacts, emails, groups, drafts, and attachments.

## Development

### Running Tests

```bash
cd packages/google_mail
uv run pytest
```

### Code Quality

```bash
uv run ruff check .
uv run mypy .
```
