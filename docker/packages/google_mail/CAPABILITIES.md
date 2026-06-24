# Google Mail Capabilities

A mock email server with a realistic closed-world simulation. The agent has one or more mailboxes, each with contacts, folders, and emails. Only contacts in the address book can receive messages — sending to unknown addresses fails with a bounce. Supports multi-mailbox worlds where the agent can operate across multiple personas.

## What the agent can do

**Read and search email.** Browse by folder, read individual emails (which marks them as read), and search across all emails by keyword. Search supports Gmail-style operators: `from:`, `to:`, `cc:`, `bcc:`, `subject:`, `has:attachment`, `filename:`, `is:unread`, `is:read`, `is:important`, `in:`, `label:`, `before:`, `after:`, `newer_than:`, `older_than:`, `-term` (exclusion), and adjacent-term `OR`. Parenthesized boolean grouping is not supported. Check unread counts per folder and overall mailbox statistics.

**Send, reply, and forward.** Compose new emails to any contact, reply to existing threads (with proper Re: prefixes and quoted text), forward emails to other contacts. Replies maintain threading via message ID references. Sent mail automatically also gets an INBOX copy when the sender is also a recipient (self-send).

**Manage contacts.** List all contacts, search by name or email, add new contacts (individual or group), edit contact details, and delete contacts. Group contacts have member lists — sending to a group delivers to all members.

**Organize with folders.** View all folders with message counts, create custom folders, move emails between folders (including in bulk), and delete custom folders (moves their emails back to INBOX). System folders (INBOX, Sent, Drafts, Trash, Scheduled) cannot be deleted.

**Work with drafts.** Save draft emails, view all drafts, update draft content, and delete drafts.

**Schedule emails.** Schedule an email for future delivery with a specific date/time, view all scheduled emails, and cancel scheduled sends. Since this is a mock, scheduled emails are stored but not actually delivered at the scheduled time.

**Attachments and utilities.** Download attachments from emails, get technical email headers (message IDs, routing info).

**Multi-mailbox.** When a world defines multiple mailboxes, `list_mailboxes` returns all available mailbox IDs + email addresses, and every tool accepts an optional `mailbox_id` argument (defaults to `"default"`). Each mailbox's contacts, folders, and emails are isolated. State round-trips through a `{"mailboxes": {id: {...}}}` wrapper.

## Coverage gaps

- No email rules or filters (auto-sort, auto-reply)
- No rich text composition (HTML editor)
- No email templates
- Scheduled emails are stored but do not actually fire

## Toolsets

28 tools total. Toolsets map to `WORLDBENCH_TOOL_SETS` values (prefixed form — e.g., `google_mail_core`).

| Toolset | Tools | Description |
|---------|-------|-------------|
| `all` / `google_mail_all` | 28 | Everything |
| `read` / `google_mail_read` | 11 | All read-only tools |
| `write` / `google_mail_write` | 17 | All write tools |
| `google_mail_core` | 20 | Basic inbox plus legacy Toolathlon mail/folder/draft operations |
| `google_mail_contacts` | 5 | Contact management: get, search, add, edit, delete |
| `google_mail_folders` | 4 | Folder organization: get, create, delete, move emails |
| `google_mail_drafts` | 4 | Drafts: get, save, update, delete |
| `google_mail_scheduling` | 3 | Schedule, list scheduled, cancel scheduled |
| `google_mail_toolathlon_legacy` | 20 | Legacy Toolathlon tool subset (pre-integration) |
| `google_mail_state` | 2 | `export_state`, `import_state` for fixture seeding and grading |

**Multi-mailbox** worlds round-trip through `export_state` / `import_state` under a `{"mailboxes": {mailbox_id: {...}}}` wrapper; single-mailbox worlds use the flat `MailboxData` shape.
