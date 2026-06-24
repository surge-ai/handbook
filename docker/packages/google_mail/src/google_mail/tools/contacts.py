"""Contacts handlers for Google Mail tools."""

from __future__ import annotations

from pydantic import EmailStr

from google_mail.services.mailbox import (
    ContactExistsError,
    ContactInUseError,
    ContactNotFoundError,
    GroupExistsError,
    GroupNotFoundError,
)
from google_mail.state import get_mailbox
from google_mail.tools.common import (
    GroupMembers,
    MailboxIdArg,
    error_response,
    format_contact,
    format_group,
    success_response,
)


async def get_contacts(mailbox_id: MailboxIdArg = "default") -> str:
    mailbox = get_mailbox(mailbox_id)
    contacts = mailbox.get_contacts()
    return success_response(
        {
            "contacts": [
                {
                    "email": c.email,
                    "name": c.name,
                }
                for c in contacts
            ]
        }
    )


async def search_contacts(query: str, mailbox_id: MailboxIdArg = "default") -> str:
    mailbox = get_mailbox(mailbox_id)
    contacts = mailbox.search_contacts(query)
    return success_response(
        {
            "contacts": [format_contact(c) for c in contacts],
            "total": len(contacts),
        }
    )


async def add_contact(
    email: EmailStr,
    name: str,
    mailbox_id: MailboxIdArg = "default",
) -> str:
    mailbox = get_mailbox(mailbox_id)
    try:
        contact = mailbox.add_contact(email=email, name=name)
        return success_response({"status": "created", "contact": format_contact(contact)})
    except ContactExistsError as e:
        return error_response(str(e))


async def edit_contact(
    email: EmailStr,
    name: str | None = None,
    mailbox_id: MailboxIdArg = "default",
) -> str:
    mailbox = get_mailbox(mailbox_id)
    try:
        contact = mailbox.edit_contact(email=email, name=name)
        return success_response({"status": "updated", "contact": format_contact(contact)})
    except ContactNotFoundError as e:
        return error_response(str(e))


async def delete_contact(email: EmailStr, mailbox_id: MailboxIdArg = "default") -> str:
    mailbox = get_mailbox(mailbox_id)
    try:
        mailbox.delete_contact(email)
        return success_response({"status": "deleted", "email": email})
    except ContactNotFoundError as e:
        return error_response(str(e))
    except ContactInUseError as e:
        return error_response(str(e))


async def get_groups(mailbox_id: MailboxIdArg = "default") -> str:
    mailbox = get_mailbox(mailbox_id)
    groups = mailbox.get_groups()
    return success_response({"groups": [format_group(group) for group in groups]})


async def search_groups(query: str, mailbox_id: MailboxIdArg = "default") -> str:
    mailbox = get_mailbox(mailbox_id)
    groups = mailbox.search_groups(query)
    return success_response(
        {
            "groups": [format_group(group) for group in groups],
            "total": len(groups),
        }
    )


async def add_group(
    email: EmailStr,
    name: str,
    members: GroupMembers,
    mailbox_id: MailboxIdArg = "default",
) -> str:
    mailbox = get_mailbox(mailbox_id)
    try:
        group = mailbox.add_group(email=email, name=name, members=members)
        return success_response({"status": "created", "group": format_group(group)})
    except GroupExistsError as e:
        return error_response(str(e))
    except ValueError as e:
        return error_response(str(e))


async def edit_group(
    email: EmailStr,
    name: str | None = None,
    members: GroupMembers | None = None,
    mailbox_id: MailboxIdArg = "default",
) -> str:
    mailbox = get_mailbox(mailbox_id)
    try:
        group = mailbox.edit_group(email=email, name=name, members=members)
        return success_response({"status": "updated", "group": format_group(group)})
    except GroupNotFoundError as e:
        return error_response(str(e))
    except ValueError as e:
        return error_response(str(e))


async def delete_group(email: EmailStr, mailbox_id: MailboxIdArg = "default") -> str:
    mailbox = get_mailbox(mailbox_id)
    try:
        mailbox.delete_group(email)
        return success_response({"status": "deleted", "email": email})
    except GroupNotFoundError as e:
        return error_response(str(e))
