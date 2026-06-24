"""Folders handlers for Google Mail tools."""

from __future__ import annotations

from google_mail.services.mailbox import (
    FolderExistsError,
    FolderNotFoundError,
    SystemFolderError,
)
from google_mail.state import get_mailbox
from google_mail.tools.common import (
    FolderName,
    MailboxIdArg,
    error_response,
    success_response,
)


async def get_folders(mailbox_id: MailboxIdArg = "default") -> str:
    mailbox = get_mailbox(mailbox_id)
    folders = mailbox.get_folders()
    return success_response({"folders": folders})


async def create_folder(folder_name: FolderName, mailbox_id: MailboxIdArg = "default") -> str:
    mailbox = get_mailbox(mailbox_id)
    try:
        mailbox.create_folder(folder_name)
        return success_response({"status": "created", "folder_name": folder_name})
    except FolderExistsError as e:
        return error_response(str(e))


async def delete_folder(folder_name: FolderName, mailbox_id: MailboxIdArg = "default") -> str:
    mailbox = get_mailbox(mailbox_id)
    try:
        mailbox.delete_folder(folder_name)
        return success_response({"status": "deleted", "folder_name": folder_name})
    except (SystemFolderError, FolderNotFoundError) as e:
        return error_response(str(e))
