"""Mock email MCP server."""

from __future__ import annotations

import functools

from fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import EmailStr

from google_mail.models import GoogleMailState
from google_mail.state import init_state as init_mail_state
from google_mail.state import write_snapshots
from google_mail.tools import contacts, drafts, folders, messages, scheduling
from google_mail.tools import state as state_tools
from google_mail.tools.common import (
    AttachmentFilename,
    AttachmentPaths,
    DraftId,
    EmailId,
    EmailIds,
    FolderName,
    GroupMembers,
    MailboxIdArg,
    PageNumber,
    PageSize,
    ScheduleTime,
)


def _snapshot_on_write(fn):
    """Decorator: dual-write the post-tool snapshot.

    Writes ``<BUNDLE_OUTPUT_DIR>/state.json`` (per-service bundle subdir,
    nested ``services/<name>/state.json`` layout) and the legacy
    ``final.json`` so consumers on either convention keep working.
    """

    @functools.wraps(fn)
    async def wrapper(*args, **kwargs):
        result = await fn(*args, **kwargs)
        write_snapshots()
        return result

    return wrapper


def init_state() -> None:
    """Eagerly initialize mailbox(es) and write the initial state snapshot."""
    init_mail_state()


mcp = FastMCP("google_mail")


@mcp.tool(
    name="list_mailboxes",
    annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True),
)
async def mail_list_mailboxes() -> str:
    """List all available mailboxes.

    Returns:
        JSON with mailbox IDs, email addresses, and names.
    """
    return await messages.list_mailboxes()


@mcp.tool(
    name="get_emails",
    annotations=ToolAnnotations(
        title="Get Emails",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    ),
)
async def mail_get_emails(
    folder: FolderName | None = None,
    page: PageNumber = 1,
    page_size: PageSize = 20,
    mailbox_id: MailboxIdArg = "default",
) -> str:
    """Get emails from the mailbox, optionally filtered by folder.

    Returns a paginated list of emails sorted by date (newest first).

    Args:
        folder: Folder to filter by (optional)
        page: Page number (1-indexed)
        page_size: Results per page (max 100)

    Returns:
        JSON with emails and pagination info.
    """
    return await messages.get_emails(folder=folder, page=page, page_size=page_size, mailbox_id=mailbox_id)


@mcp.tool(
    name="read_email",
    annotations=ToolAnnotations(
        title="Read Email",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    ),
)
@_snapshot_on_write
async def mail_read_email(email_id: EmailId, mailbox_id: MailboxIdArg = "default") -> str:
    """Read an email and mark it as read.

    Returns the full email content including body and attachments.

    Args:
        email_id: ID of the email to read

    Returns:
        JSON with full email details.
    """
    return await messages.read_email(email_id=email_id, mailbox_id=mailbox_id)


@mcp.tool(
    name="search_emails",
    annotations=ToolAnnotations(
        title="Search Emails",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    ),
)
async def mail_search_emails(
    query: str,
    folder: FolderName | None = None,
    page: PageNumber = 1,
    page_size: PageSize = 20,
    mailbox_id: MailboxIdArg = "default",
) -> str:
    """Search emails by query string. Supports Gmail-style operators.

    Bare words are ANDed together and matched across subject, body, from,
    and to. Double-quoted segments require exact adjacency. Combine freely,
    e.g. `from:alice after:2026/03/01 invoice`.

    Operators:
      from:alice              match sender substring (alice@... hits this)
      to:bob                  match to recipient substring (strict; use cc:/bcc: for other fields)
      cc:alice / bcc:alice    match cc/bcc recipient substring
      subject:meeting         match subject substring
      has:attachment          messages with one or more attachments
      filename:agenda.pdf     match attachment filename substring
      is:unread               state filters: unread, read, important
      in:sent                  exact folder match
      label:client             exact label match when labels exist; otherwise folder-style match
      "exact phrase"          match a contiguous phrase
      -term                   exclude messages containing term (works with operators, e.g. -from:bob)
      invoice OR billing      boolean OR (uppercase; lowercase 'or' is literal)
      before:2026/04/01       messages strictly before date (YYYY/MM/DD or YYYY-MM-DD)
      after:2026/04/01        messages on or after date
      newer_than:7d           messages within last N days (d), months (m = 30d), or years (y = 365d)
      older_than:1y           messages older than N d|m|y

    Parenthesized boolean grouping is not supported; `OR` joins adjacent terms only.
    Unsupported or malformed search syntax is reported in a `warnings` array
    while preserving best-effort search behavior.

    Args:
        query: Search query (supports the operators above).
        folder: Folder to limit search (optional).
        page: Page number (1-indexed).
        page_size: Results per page (max 100).

    Returns:
        JSON with matching emails and pagination info.
    """
    return await messages.search_emails(
        query=query, folder=folder, page=page, page_size=page_size, mailbox_id=mailbox_id
    )


@mcp.tool(
    name="send_email",
    annotations=ToolAnnotations(
        title="Send Email",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=False,
    ),
)
@_snapshot_on_write
async def mail_send_email(
    to: str,
    subject: str,
    body: str,
    html_body: str | None = None,
    cc: str | None = None,
    bcc: str | None = None,
    attachments: AttachmentPaths | None = None,
    mailbox_id: MailboxIdArg = "default",
) -> str:
    """Send an email.

    Recipients must be valid contacts in your address book.
    If sending to a group you're a member of, you'll receive a copy.

    Args:
        to: Recipient(s), comma-separated
        subject: Email subject
        body: Plain text email body
        html_body: HTML body (optional)
        cc: CC recipients, comma-separated (optional)
        bcc: BCC recipients, comma-separated (optional)
        attachments: Paths to files to attach (optional)

    Returns:
        JSON with sent status and email summary.
    """
    return await messages.send_email(
        to=to,
        subject=subject,
        body=body,
        html_body=html_body,
        cc=cc,
        bcc=bcc,
        attachments=attachments,
        mailbox_id=mailbox_id,
    )


@mcp.tool(
    name="reply_email",
    annotations=ToolAnnotations(
        title="Reply to Email",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=False,
    ),
)
@_snapshot_on_write
async def mail_reply_email(
    email_id: EmailId,
    body: str,
    html_body: str | None = None,
    reply_all: bool = False,
    mailbox_id: MailboxIdArg = "default",
) -> str:
    """Reply to an email.

    Creates a reply with proper subject prefix and recipients.

    Args:
        email_id: ID of the email to reply to
        body: Reply body text
        html_body: HTML body (optional)
        reply_all: Reply to all recipients

    Returns:
        JSON with sent status and reply email summary.
    """
    return await messages.reply_email(
        email_id=email_id, body=body, html_body=html_body, reply_all=reply_all, mailbox_id=mailbox_id
    )


@mcp.tool(
    name="forward_email",
    annotations=ToolAnnotations(
        title="Forward Email",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=False,
    ),
)
@_snapshot_on_write
async def mail_forward_email(
    email_id: EmailId,
    to: str,
    body: str | None = None,
    mailbox_id: MailboxIdArg = "default",
) -> str:
    """Forward an email to one or more recipients.

    Includes the original message content and any attachments.

    Args:
        email_id: ID of the email to forward
        to: Recipient(s) to forward to, comma-separated
        body: Additional message (optional)

    Returns:
        JSON with sent status and forwarded email summary.
    """
    return await messages.forward_email(email_id=email_id, to=to, body=body, mailbox_id=mailbox_id)


@mcp.tool(
    name="delete_emails",
    annotations=ToolAnnotations(
        title="Delete Emails",
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=True,
        openWorldHint=False,
    ),
)
@_snapshot_on_write
async def mail_delete_emails(
    email_ids: EmailIds,
    permanent: bool = False,
    mailbox_id: MailboxIdArg = "default",
) -> str:
    """Delete one or more emails.

    By default, moves them to Trash. Use permanent=True to skip Trash.
    Deleting from Trash permanently deletes. Partial success is allowed —
    emails that don't exist surface in the errors list but don't abort the batch.

    Args:
        email_ids: IDs of the emails to delete (pass a single-element list for one)
        permanent: Permanently delete (skip Trash)

    Returns:
        JSON with per-id status and a deleted/failed count.
    """
    return await messages.delete_emails(email_ids=email_ids, permanent=permanent, mailbox_id=mailbox_id)


@mcp.tool(
    name="move_emails",
    annotations=ToolAnnotations(
        title="Move Emails",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    ),
)
@_snapshot_on_write
async def mail_move_emails(
    email_ids: EmailIds,
    target_folder: FolderName,
    mailbox_id: MailboxIdArg = "default",
) -> str:
    """Move one or more emails to a different folder.

    Partial success is allowed — emails or folders that don't exist surface
    in the errors list but don't abort the batch.

    Args:
        email_ids: IDs of the emails to move (pass a single-element list for one)
        target_folder: Name of the target folder

    Returns:
        JSON with per-id status and a moved/failed count.
    """
    return await messages.move_emails(email_ids=email_ids, target_folder=target_folder, mailbox_id=mailbox_id)


@mcp.tool(
    name="mark_emails",
    annotations=ToolAnnotations(
        title="Mark Emails",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    ),
)
@_snapshot_on_write
async def mail_mark_emails(
    email_ids: EmailIds,
    is_read: bool | None = None,
    is_important: bool | None = None,
    mailbox_id: MailboxIdArg = "default",
) -> str:
    """Mark emails as read/unread or important/not important.

    Args:
        email_ids: List of email IDs to mark
        is_read: Set read status (optional)
        is_important: Set important status (optional)

    Returns:
        JSON with number of emails updated.
    """
    return await messages.mark_emails(
        email_ids=email_ids, is_read=is_read, is_important=is_important, mailbox_id=mailbox_id
    )


@mcp.tool(
    name="get_folders",
    annotations=ToolAnnotations(
        title="Get Folders",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    ),
)
async def mail_get_folders(mailbox_id: MailboxIdArg = "default") -> str:
    """Get all folders with message counts.

    Returns system folders (INBOX, Sent, Drafts, Trash) and custom folders.

    Returns:
        JSON with folder list including name, total, unread, is_system.
    """
    return await folders.get_folders(mailbox_id=mailbox_id)


@mcp.tool(
    name="create_folder",
    annotations=ToolAnnotations(
        title="Create Folder",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=False,
    ),
)
@_snapshot_on_write
async def mail_create_folder(folder_name: FolderName, mailbox_id: MailboxIdArg = "default") -> str:
    """Create a new custom folder.

    Args:
        folder_name: Name of the folder to create

    Returns:
        JSON with creation status.
    """
    return await folders.create_folder(folder_name=folder_name, mailbox_id=mailbox_id)


@mcp.tool(
    name="delete_folder",
    annotations=ToolAnnotations(
        title="Delete Folder",
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=True,
        openWorldHint=False,
    ),
)
@_snapshot_on_write
async def mail_delete_folder(folder_name: FolderName, mailbox_id: MailboxIdArg = "default") -> str:
    """Delete a custom folder.

    System folders (INBOX, Sent, Drafts, Trash) cannot be deleted.
    Emails in the deleted folder are moved to INBOX.

    Args:
        folder_name: Name of the folder to delete

    Returns:
        JSON with deletion status.
    """
    return await folders.delete_folder(folder_name=folder_name, mailbox_id=mailbox_id)


@mcp.tool(
    name="get_unread_count",
    annotations=ToolAnnotations(
        title="Get Unread Count",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    ),
)
async def mail_get_unread_count(folder: FolderName | None = None, mailbox_id: MailboxIdArg = "default") -> str:
    """Get unread email count for folders.

    Args:
        folder: Specific folder (optional)

    Returns:
        JSON with unread counts per folder.
    """
    return await messages.get_unread_count(folder=folder, mailbox_id=mailbox_id)


@mcp.tool(
    name="get_mailbox_stats",
    annotations=ToolAnnotations(
        title="Get Mailbox Stats",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    ),
)
async def mail_get_mailbox_stats(mailbox_id: MailboxIdArg = "default") -> str:
    """Get overall mailbox statistics.

    Returns owner info, total counts, and per-folder breakdown.

    Returns:
        JSON with comprehensive mailbox statistics.
    """
    return await messages.get_mailbox_stats(mailbox_id=mailbox_id)


@mcp.tool(
    name="save_draft",
    annotations=ToolAnnotations(
        title="Save Draft",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=False,
    ),
)
@_snapshot_on_write
async def mail_save_draft(
    subject: str = "",
    body: str = "",
    html_body: str | None = None,
    to: str | None = None,
    cc: str | None = None,
    bcc: str | None = None,
    mailbox_id: MailboxIdArg = "default",
) -> str:
    """Save a new email draft.

    Args:
        subject: Draft subject
        body: Draft body
        html_body: HTML body (optional)
        to: Recipient(s), comma-separated (optional)
        cc: CC recipient(s), comma-separated (optional)
        bcc: BCC recipient(s), comma-separated (optional)

    Returns:
        JSON with saved draft details.
    """
    return await drafts.save_draft(
        subject=subject, body=body, html_body=html_body, to=to, cc=cc, bcc=bcc, mailbox_id=mailbox_id
    )


@mcp.tool(
    name="get_drafts",
    annotations=ToolAnnotations(
        title="Get Drafts",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    ),
)
async def mail_get_drafts(
    page: PageNumber = 1,
    page_size: PageSize = 20,
    mailbox_id: MailboxIdArg = "default",
) -> str:
    """Get all drafts with pagination.

    Args:
        page: Page number (1-indexed)
        page_size: Results per page (max 100)

    Returns:
        JSON with drafts and pagination info.
    """
    return await drafts.get_drafts(page=page, page_size=page_size, mailbox_id=mailbox_id)


@mcp.tool(
    name="update_draft",
    annotations=ToolAnnotations(
        title="Update Draft",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    ),
)
@_snapshot_on_write
async def mail_update_draft(
    draft_id: DraftId,
    subject: str | None = None,
    body: str | None = None,
    html_body: str | None = None,
    to: str | None = None,
    cc: str | None = None,
    bcc: str | None = None,
    mailbox_id: MailboxIdArg = "default",
) -> str:
    """Update an existing draft.

    Only provided fields are updated; others remain unchanged.

    Args:
        draft_id: ID of the draft to update
        subject: New subject (optional)
        body: New body (optional)
        html_body: New HTML body (optional)
        to: New recipient(s), comma-separated (optional)
        cc: New CC recipient(s), comma-separated (optional)
        bcc: New BCC recipient(s), comma-separated (optional)

    Returns:
        JSON with updated draft details.
    """
    return await drafts.update_draft(
        draft_id=draft_id, subject=subject, body=body, html_body=html_body, to=to, cc=cc, bcc=bcc, mailbox_id=mailbox_id
    )


@mcp.tool(
    name="delete_draft",
    annotations=ToolAnnotations(
        title="Delete Draft",
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=True,
        openWorldHint=False,
    ),
)
@_snapshot_on_write
async def mail_delete_draft(draft_id: DraftId, mailbox_id: MailboxIdArg = "default") -> str:
    """Delete a draft.

    Args:
        draft_id: ID of the draft to delete

    Returns:
        JSON with deletion status.
    """
    return await drafts.delete_draft(draft_id=draft_id, mailbox_id=mailbox_id)


@mcp.tool(
    name="get_contacts",
    annotations=ToolAnnotations(
        title="Get Contacts",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    ),
)
async def mail_get_contacts(mailbox_id: MailboxIdArg = "default") -> str:
    """Get all contacts.

    Returns the person contacts in your address book. Groups are available
    through get_groups.

    Returns:
        JSON with contact list.
    """
    return await contacts.get_contacts(mailbox_id=mailbox_id)


@mcp.tool(
    name="download_attachment",
    annotations=ToolAnnotations(
        title="Download Attachment",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    ),
)
async def mail_download_attachment(
    email_id: EmailId,
    filename: AttachmentFilename,
    mailbox_id: MailboxIdArg = "default",
) -> str:
    """Download an attachment from an email.

    Returns the attachment content as base64-encoded data.

    Args:
        email_id: ID of the email
        filename: Name of the attachment file

    Returns:
        JSON with attachment data.
    """
    return await messages.download_attachment(email_id=email_id, filename=filename, mailbox_id=mailbox_id)


@mcp.tool(
    name="search_contacts",
    annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True),
)
async def mail_search_contacts(query: str, mailbox_id: MailboxIdArg = "default") -> str:
    """Search contacts by name or email address (case-insensitive).

    Args:
        query: Search string to match against contact name or email
    """
    return await contacts.search_contacts(query=query, mailbox_id=mailbox_id)


@mcp.tool(
    name="add_contact",
    annotations=ToolAnnotations(readOnlyHint=False, idempotentHint=False),
)
@_snapshot_on_write
async def mail_add_contact(
    email: EmailStr,
    name: str,
    mailbox_id: MailboxIdArg = "default",
) -> str:
    """Add a new contact to the address book.

    Args:
        email: Email address of the contact
        name: Display name of the contact
    """
    return await contacts.add_contact(email=email, name=name, mailbox_id=mailbox_id)


@mcp.tool(
    name="edit_contact",
    annotations=ToolAnnotations(readOnlyHint=False, idempotentHint=True),
)
@_snapshot_on_write
async def mail_edit_contact(
    email: EmailStr,
    name: str | None = None,
    mailbox_id: MailboxIdArg = "default",
) -> str:
    """Update an existing contact's name.

    Args:
        email: Email address of the contact to update (lookup key)
        name: New display name (optional, omit to keep current)
    """
    return await contacts.edit_contact(email=email, name=name, mailbox_id=mailbox_id)


@mcp.tool(
    name="delete_contact",
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=True),
)
@_snapshot_on_write
async def mail_delete_contact(email: EmailStr, mailbox_id: MailboxIdArg = "default") -> str:
    """Remove a contact from the address book.

    Args:
        email: Email address of the contact to delete
    """
    return await contacts.delete_contact(email=email, mailbox_id=mailbox_id)


@mcp.tool(
    name="get_groups",
    annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True),
)
async def mail_get_groups(mailbox_id: MailboxIdArg = "default") -> str:
    """Get all addressable contact groups."""
    return await contacts.get_groups(mailbox_id=mailbox_id)


@mcp.tool(
    name="search_groups",
    annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True),
)
async def mail_search_groups(query: str, mailbox_id: MailboxIdArg = "default") -> str:
    """Search groups by name or email address (case-insensitive)."""
    return await contacts.search_groups(query=query, mailbox_id=mailbox_id)


@mcp.tool(
    name="add_group",
    annotations=ToolAnnotations(readOnlyHint=False, idempotentHint=False),
)
@_snapshot_on_write
async def mail_add_group(
    email: EmailStr,
    name: str,
    members: GroupMembers,
    mailbox_id: MailboxIdArg = "default",
) -> str:
    """Add a new addressable contact group.

    Args:
        email: Email address of the group
        name: Display name of the group
        members: Contact emails included in the group
    """
    return await contacts.add_group(email=email, name=name, members=members, mailbox_id=mailbox_id)


@mcp.tool(
    name="edit_group",
    annotations=ToolAnnotations(readOnlyHint=False, idempotentHint=True),
)
@_snapshot_on_write
async def mail_edit_group(
    email: EmailStr,
    name: str | None = None,
    members: GroupMembers | None = None,
    mailbox_id: MailboxIdArg = "default",
) -> str:
    """Update an existing contact group."""
    return await contacts.edit_group(email=email, name=name, members=members, mailbox_id=mailbox_id)


@mcp.tool(
    name="delete_group",
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=True),
)
@_snapshot_on_write
async def mail_delete_group(email: EmailStr, mailbox_id: MailboxIdArg = "default") -> str:
    """Remove a contact group from the address book."""
    return await contacts.delete_group(email=email, mailbox_id=mailbox_id)


@mcp.tool(
    name="schedule_email",
    annotations=ToolAnnotations(readOnlyHint=False, idempotentHint=False),
)
@_snapshot_on_write
async def mail_schedule_email(
    to: str,
    subject: str,
    body: str,
    scheduled_time: ScheduleTime,
    html_body: str | None = None,
    cc: str | None = None,
    bcc: str | None = None,
    mailbox_id: MailboxIdArg = "default",
) -> str:
    """Schedule an email for later delivery.

    Args:
        to: Recipient(s), comma-separated
        subject: Email subject
        body: Plain text email body
        scheduled_time: ISO 8601 datetime for when to send (e.g., '2024-12-25T09:00:00Z')
        html_body: HTML body (optional)
        cc: CC recipient(s), comma-separated (optional)
        bcc: BCC recipient(s), comma-separated (optional)
    """
    return await scheduling.schedule_email(
        to=to,
        subject=subject,
        body=body,
        scheduled_time=scheduled_time,
        html_body=html_body,
        cc=cc,
        bcc=bcc,
        mailbox_id=mailbox_id,
    )


@mcp.tool(
    name="get_scheduled_emails",
    annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True),
)
async def mail_get_scheduled_emails(
    page: PageNumber = 1,
    page_size: PageSize = 20,
    mailbox_id: MailboxIdArg = "default",
) -> str:
    """Get list of emails scheduled for later delivery."""
    return await scheduling.get_scheduled_emails(page=page, page_size=page_size, mailbox_id=mailbox_id)


@mcp.tool(
    name="cancel_scheduled_email",
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=True),
)
@_snapshot_on_write
async def mail_cancel_scheduled_email(email_id: EmailId, mailbox_id: MailboxIdArg = "default") -> str:
    """Cancel a scheduled email. The email is permanently removed.

    Args:
        email_id: ID of the scheduled email to cancel
    """
    return await scheduling.cancel_scheduled_email(email_id=email_id, mailbox_id=mailbox_id)


@mcp.tool(
    name="export_state",
    annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True),
)
async def export_state() -> GoogleMailState:
    """Export the full mailbox state as JSON.

    Single-mailbox worlds emit ``MailboxData``; multi-mailbox worlds emit
    ``MultiMailboxData``. Round-trips with import_state.
    """
    return await state_tools.export_state()


@mcp.tool(
    name="import_state",
    annotations=ToolAnnotations(readOnlyHint=False, idempotentHint=True),
)
@_snapshot_on_write
async def import_state(state: GoogleMailState) -> dict:
    """Replace the full mailbox state with the provided JSON.

    Accepts either the flat MailboxData shape (loaded into the ``default``
    mailbox) or the multi-mailbox wrapper (``{"mailboxes": {mailbox_id: ...}}``).
    For synthetic-data injection and test setup.
    """
    return await state_tools.import_state(state=state)
