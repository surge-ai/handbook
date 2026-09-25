"""Scheduling handlers for Google Mail tools."""

from __future__ import annotations

from google_mail.services.mailbox import (
    EmailNotFoundError,
    RecipientNotFoundError,
    RecipientRequiredError,
)
from google_mail.state import get_mailbox
from google_mail.tools.common import (
    EmailId,
    MailboxIdArg,
    PageNumber,
    PageSize,
    ScheduleTime,
    error_response,
    format_email_summary,
    normalize_scheduled_time,
    success_response,
)


async def schedule_email(
    to: str,
    subject: str,
    body: str,
    scheduled_time: ScheduleTime,
    html_body: str | None = None,
    cc: str | None = None,
    bcc: str | None = None,
    mailbox_id: MailboxIdArg = "default",
) -> str:
    mailbox = get_mailbox(mailbox_id)
    try:
        send_at = normalize_scheduled_time(scheduled_time)
    except ValueError:
        return error_response(f"Invalid scheduled_time format: {scheduled_time}")

    try:
        email = mailbox.schedule_email(
            to=to,
            subject=subject,
            body=body,
            scheduled_time=send_at,
            html_body=html_body,
            cc=cc,
            bcc=bcc,
        )
        result = format_email_summary(email)
        result["scheduled_time"] = email.scheduled_time.isoformat() if email.scheduled_time else None
        return success_response({"status": "scheduled", "email": result})
    except RecipientNotFoundError as e:
        return error_response(str(e), status="failed")
    except RecipientRequiredError as e:
        return error_response(str(e), status="failed")


async def get_scheduled_emails(
    page: PageNumber = 1,
    page_size: PageSize = 20,
    mailbox_id: MailboxIdArg = "default",
) -> str:
    mailbox = get_mailbox(mailbox_id)
    emails, total = mailbox.get_scheduled_emails(page=page, page_size=page_size)
    results = []
    for e in emails:
        r = format_email_summary(e)
        r["scheduled_time"] = e.scheduled_time.isoformat() if e.scheduled_time else None
        results.append(r)
    return success_response(
        {
            "scheduled_emails": results,
            "total": total,
            "page": page,
            "page_size": page_size,
        }
    )


async def cancel_scheduled_email(email_id: EmailId, mailbox_id: MailboxIdArg = "default") -> str:
    mailbox = get_mailbox(mailbox_id)
    try:
        mailbox.cancel_scheduled_email(email_id)
        return success_response({"status": "cancelled", "email_id": email_id})
    except EmailNotFoundError as e:
        return error_response(str(e))
