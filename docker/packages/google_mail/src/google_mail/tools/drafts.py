"""Drafts handlers for Google Mail tools."""

from __future__ import annotations

from pydantic import ValidationError

from google_mail.services.mailbox import (
    DraftNotFoundError,
)
from google_mail.state import get_mailbox
from google_mail.tools.common import (
    DraftId,
    MailboxIdArg,
    PageNumber,
    PageSize,
    error_response,
    format_draft,
    success_response,
)


async def save_draft(
    subject: str = "",
    body: str = "",
    html_body: str | None = None,
    to: str | None = None,
    cc: str | None = None,
    bcc: str | None = None,
    mailbox_id: MailboxIdArg = "default",
) -> str:
    mailbox = get_mailbox(mailbox_id)
    try:
        draft = mailbox.save_draft(
            subject=subject,
            body=body,
            html_body=html_body,
            to=to,
            cc=cc,
            bcc=bcc,
        )
        return success_response({"status": "saved", "draft": format_draft(draft)})
    except ValidationError as e:
        return error_response(str(e), status="failed")


async def get_drafts(
    page: PageNumber = 1,
    page_size: PageSize = 20,
    mailbox_id: MailboxIdArg = "default",
) -> str:
    mailbox = get_mailbox(mailbox_id)
    drafts, total = mailbox.get_drafts(page=page, page_size=page_size)
    return success_response(
        {
            "drafts": [format_draft(d) for d in drafts],
            "total": total,
            "page": page,
            "page_size": page_size,
        }
    )


async def update_draft(
    draft_id: DraftId,
    subject: str | None = None,
    body: str | None = None,
    html_body: str | None = None,
    to: str | None = None,
    cc: str | None = None,
    bcc: str | None = None,
    mailbox_id: MailboxIdArg = "default",
) -> str:
    mailbox = get_mailbox(mailbox_id)
    try:
        draft = mailbox.update_draft(
            draft_id=draft_id,
            subject=subject,
            body=body,
            html_body=html_body,
            to=to,
            cc=cc,
            bcc=bcc,
        )
        return success_response({"status": "updated", "draft": format_draft(draft)})
    except DraftNotFoundError as e:
        return error_response(str(e))
    except ValidationError as e:
        return error_response(str(e), status="failed")


async def delete_draft(draft_id: DraftId, mailbox_id: MailboxIdArg = "default") -> str:
    mailbox = get_mailbox(mailbox_id)
    try:
        mailbox.delete_draft(draft_id)
        return success_response({"status": "deleted", "draft_id": draft_id})
    except DraftNotFoundError as e:
        return error_response(str(e))
