"""Shared helpers and public argument aliases for Google Mail tools."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Annotated, Any

from pydantic import EmailStr, Field

from google_mail.models import Email

PageNumber = Annotated[int, Field(ge=1, description="Page number, starting at 1.")]
PageSize = Annotated[int, Field(ge=1, le=100, description="Number of results per page.")]
GroupMembers = Annotated[list[EmailStr], Field(min_length=1, description="Contact emails included in the group.")]
EmailId = Annotated[str, Field(min_length=1, description="Email ID.")]
EmailIds = Annotated[list[EmailId], Field(min_length=1, description="One or more email IDs.")]
DraftId = Annotated[str, Field(min_length=1, description="Draft ID.")]
FolderName = Annotated[str, Field(min_length=1, description="Folder name.")]
MailboxIdArg = Annotated[str, Field(min_length=1, description="Mailbox identifier.")]
AttachmentFilename = Annotated[str, Field(min_length=1, description="Attachment filename.")]
AttachmentPath = Annotated[str, Field(min_length=1, description="Path to a file to attach.")]
AttachmentPaths = Annotated[list[AttachmentPath], Field(min_length=1, description="Paths to files to attach.")]
ScheduleTime = Annotated[datetime, Field(description="ISO 8601 scheduled send time.")]


def error_response(error: str, **extra: Any) -> str:
    """Format a JSON error response."""
    return json.dumps({"error": error, **extra})


def success_response(data: dict[str, Any]) -> str:
    """Format a JSON success response."""
    return json.dumps(data, indent=2)


def batch_response(
    *,
    action: str,
    requested_count: int,
    succeeded_ids: list[str],
    errors: list[dict[str, str]],
    extra: dict[str, Any] | None = None,
) -> str:
    """Format a batch-action response with unambiguous aggregate status."""
    succeeded_count = len(succeeded_ids)
    failed_count = len(errors)
    if succeeded_count == requested_count:
        status = "all_succeeded"
        summary = f"All {requested_count} attempts succeeded"
    elif succeeded_count == 0:
        status = "all_failed"
        summary = f"All {requested_count} attempts failed"
    else:
        status = "partial_success"
        summary = f"{succeeded_count} of {requested_count} attempts succeeded"

    payload: dict[str, Any] = {
        "status": status,
        "summary": summary,
        "action": action,
        "requestedCount": requested_count,
        "succeededCount": succeeded_count,
        "failedCount": failed_count,
        "succeededIds": succeeded_ids,
        "errors": errors,
    }
    if extra:
        payload.update(extra)
    return success_response(payload)


def normalize_scheduled_time(value: datetime | str) -> datetime:
    """Normalize parsed or direct-call scheduled_time values."""
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value


def format_email_summary(email: Email) -> dict[str, Any]:
    """Format an email for summary output."""
    return {
        "email_id": email.email_id,
        "folder": email.folder,
        "subject": email.subject,
        "from": email.from_addr,
        "to": email.to_addr,
        "date": email.date.isoformat(),
        "is_read": email.is_read,
        "is_important": email.is_important,
        "has_attachments": len(email.attachments) > 0,
    }


def format_email_full(email: Email) -> dict[str, Any]:
    """Format an email for full output."""
    result = format_email_summary(email)
    result["cc"] = email.cc_addr
    result["bcc"] = email.bcc_addr
    result["message_id"] = email.message_id
    result["in_reply_to"] = email.in_reply_to
    result["body_text"] = email.body_text
    result["body_html"] = email.body_html
    result["attachments"] = [
        {
            "filename": a.filename,
            "content_type": a.content_type,
            "size": a.size,
        }
        for a in email.attachments
    ]
    return result


def format_draft(draft: Email) -> dict[str, Any]:
    """Format a draft (email in Drafts folder) for output."""
    return {
        "draft_id": draft.email_id,
        "subject": draft.subject,
        "to": draft.to_addr,
        "cc": draft.cc_addr,
        "bcc": draft.bcc_addr,
        "body": draft.body_text,
        "html_body": draft.body_html,
        "date": draft.date.isoformat(),
    }


def format_contact(contact: Any) -> dict[str, Any]:
    """Format a contact for output."""
    return {
        "email": contact.email,
        "name": contact.name,
    }


def format_group(group: Any) -> dict[str, Any]:
    """Format a group for output."""
    return {
        "email": group.email,
        "name": group.name,
        "members": group.members,
    }
