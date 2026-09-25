"""Messages handlers for Google Mail tools."""

from __future__ import annotations

import base64
import mimetypes
from pathlib import Path

from google_mail.models import Attachment
from google_mail.services.mailbox import (
    AttachmentNotFoundError,
    EmailNotFoundError,
    FolderNotFoundError,
    RecipientNotFoundError,
    RecipientRequiredError,
    normalize_search_pagination,
    search_query_warnings,
)
from google_mail.state import get_mailbox, get_mailboxes
from google_mail.tools.common import (
    AttachmentFilename,
    AttachmentPaths,
    EmailId,
    EmailIds,
    FolderName,
    MailboxIdArg,
    PageNumber,
    PageSize,
    batch_response,
    error_response,
    format_email_full,
    format_email_summary,
    success_response,
)


async def list_mailboxes() -> str:
    result = []
    for mid, svc in get_mailboxes().items():
        result.append(
            {
                "mailbox_id": mid,
                "email": svc.data.mailbox.email,
                "name": svc.data.mailbox.name,
                "email_count": len(svc.data.emails),
                "contact_count": len(svc.data.contacts),
            }
        )
    return success_response({"mailboxes": result, "total": len(result)})


async def get_emails(
    folder: FolderName | None = None,
    page: PageNumber = 1,
    page_size: PageSize = 20,
    mailbox_id: MailboxIdArg = "default",
) -> str:
    mailbox = get_mailbox(mailbox_id)
    try:
        emails, total = mailbox.get_emails(
            folder=folder,
            page=page,
            page_size=page_size,
        )
        return success_response(
            {
                "emails": [format_email_summary(e) for e in emails],
                "total": total,
                "page": page,
                "page_size": page_size,
            }
        )
    except FolderNotFoundError as e:
        return error_response(str(e))


async def read_email(email_id: EmailId, mailbox_id: MailboxIdArg = "default") -> str:
    mailbox = get_mailbox(mailbox_id)
    try:
        email = mailbox.read_email(email_id)
        return success_response({"email": format_email_full(email)})
    except EmailNotFoundError as e:
        return error_response(str(e))


async def search_emails(
    query: str,
    folder: FolderName | None = None,
    page: PageNumber = 1,
    page_size: PageSize = 20,
    mailbox_id: MailboxIdArg = "default",
) -> str:
    mailbox = get_mailbox(mailbox_id)
    page, page_size, pagination_warnings = normalize_search_pagination(page, page_size)
    warnings = search_query_warnings(query) + pagination_warnings
    emails, total = mailbox.search_emails(
        query=query,
        folder=folder,
        page=page,
        page_size=page_size,
    )
    return success_response(
        {
            "emails": [format_email_summary(e) for e in emails],
            "total": total,
            "page": page,
            "page_size": page_size,
            "warnings": warnings,
        }
    )


async def send_email(
    to: str,
    subject: str,
    body: str,
    html_body: str | None = None,
    cc: str | None = None,
    bcc: str | None = None,
    attachments: AttachmentPaths | None = None,
    mailbox_id: MailboxIdArg = "default",
) -> str:
    mailbox = get_mailbox(mailbox_id)

    # Process attachments from file paths
    attachment_objs: list[Attachment] | None = None
    if attachments:
        attachment_objs = []
        for file_path_str in attachments:
            file_path = Path(file_path_str)
            if not file_path.exists():
                return error_response(f"Attachment file not found: {file_path_str}")
            content = file_path.read_bytes()
            content_base64 = base64.b64encode(content).decode("utf-8")
            content_type, _ = mimetypes.guess_type(file_path.name)
            if content_type is None:
                content_type = "application/octet-stream"
            attachment_objs.append(
                Attachment(
                    filename=file_path.name,
                    content_type=content_type,
                    content_base64=content_base64,
                )
            )

    try:
        email = mailbox.send_email(
            to=to,
            subject=subject,
            body=body,
            html_body=html_body,
            cc=cc,
            bcc=bcc,
            attachments=attachment_objs,
        )
        return success_response({"status": "sent", "email": format_email_summary(email)})
    except RecipientNotFoundError as e:
        return error_response(
            f"Invalid recipient: {e.recipient}",
            status="bounced",
            message="Recipient not found in contacts. Check available contacts with mail_get_contacts.",
        )
    except RecipientRequiredError as e:
        return error_response(str(e), status="bounced")


async def reply_email(
    email_id: EmailId,
    body: str,
    html_body: str | None = None,
    reply_all: bool = False,
    mailbox_id: MailboxIdArg = "default",
) -> str:
    mailbox = get_mailbox(mailbox_id)
    try:
        email = mailbox.reply_email(
            email_id=email_id,
            body=body,
            html_body=html_body,
            reply_all=reply_all,
        )
        return success_response({"status": "sent", "email": format_email_summary(email)})
    except EmailNotFoundError as e:
        return error_response(str(e))
    except RecipientNotFoundError as e:
        return error_response(f"Invalid recipient: {e.recipient}", status="bounced")


async def forward_email(
    email_id: EmailId,
    to: str,
    body: str | None = None,
    mailbox_id: MailboxIdArg = "default",
) -> str:
    mailbox = get_mailbox(mailbox_id)
    try:
        email = mailbox.forward_email(
            email_id=email_id,
            to=to,
            body=body,
        )
        return success_response({"status": "sent", "email": format_email_summary(email)})
    except EmailNotFoundError as e:
        return error_response(str(e))
    except RecipientNotFoundError as e:
        return error_response(f"Invalid recipient: {e.recipient}", status="bounced")


async def delete_emails(
    email_ids: EmailIds,
    permanent: bool = False,
    mailbox_id: MailboxIdArg = "default",
) -> str:
    mailbox = get_mailbox(mailbox_id)
    action = "permanently deleted" if permanent else "moved to Trash"
    result = mailbox.delete_emails(email_ids, permanent=permanent)
    deleted = result.succeeded_ids
    return batch_response(
        action=action,
        requested_count=len(email_ids),
        succeeded_ids=deleted,
        errors=[error.to_dict() for error in result.errors],
        extra={
            "deletedCount": len(deleted),
            "deletedIds": deleted,
        },
    )


async def move_emails(
    email_ids: EmailIds,
    target_folder: FolderName,
    mailbox_id: MailboxIdArg = "default",
) -> str:
    mailbox = get_mailbox(mailbox_id)
    result = mailbox.move_emails(email_ids, target_folder)
    moved = result.succeeded_ids
    return batch_response(
        action="moved",
        requested_count=len(email_ids),
        succeeded_ids=moved,
        errors=[error.to_dict() for error in result.errors],
        extra={
            "target_folder": target_folder,
            "movedCount": len(moved),
            "movedIds": moved,
        },
    )


async def mark_emails(
    email_ids: EmailIds,
    is_read: bool | None = None,
    is_important: bool | None = None,
    mailbox_id: MailboxIdArg = "default",
) -> str:
    mailbox = get_mailbox(mailbox_id)
    if is_read is None and is_important is None:
        return error_response("At least one of is_read or is_important must be provided", status="failed")

    result = mailbox.mark_emails(
        email_ids=email_ids,
        is_read=is_read,
        is_important=is_important,
    )
    marked = result.succeeded_ids
    return batch_response(
        action="marked",
        requested_count=len(email_ids),
        succeeded_ids=marked,
        errors=[error.to_dict() for error in result.errors],
        extra={
            "markedCount": len(marked),
            "markedIds": marked,
        },
    )


async def get_unread_count(folder: FolderName | None = None, mailbox_id: MailboxIdArg = "default") -> str:
    mailbox = get_mailbox(mailbox_id)
    try:
        counts = mailbox.get_unread_count(folder=folder)
        return success_response({"unread_counts": counts})
    except FolderNotFoundError as e:
        return error_response(str(e))


async def get_mailbox_stats(mailbox_id: MailboxIdArg = "default") -> str:
    mailbox = get_mailbox(mailbox_id)
    stats = mailbox.get_mailbox_stats()
    return success_response(stats)


async def download_attachment(
    email_id: EmailId,
    filename: AttachmentFilename,
    mailbox_id: MailboxIdArg = "default",
) -> str:
    mailbox = get_mailbox(mailbox_id)
    try:
        attachment = mailbox.get_attachment(email_id, filename)
        return success_response(
            {
                "filename": attachment.filename,
                "content_type": attachment.content_type,
                "size": attachment.size,
                "content_base64": attachment.content_base64,
            }
        )
    except EmailNotFoundError as e:
        return error_response(str(e))
    except AttachmentNotFoundError as e:
        return error_response(str(e))
