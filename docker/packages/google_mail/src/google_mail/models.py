"""Schema models for mock mailbox data."""

from __future__ import annotations

import base64
import binascii
from datetime import datetime
from typing import Annotated, Self

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    TypeAdapter,
    ValidationInfo,
    field_validator,
    model_validator,
)

_EMAIL_ADAPTER: TypeAdapter[EmailStr] = TypeAdapter(EmailStr)


def _allow_list_of_strings(schema: dict) -> None:
    """Widen a string-typed JSON schema to also advertise ``array[string]``.

    Pydantic emits the declared type only; our ``_coerce_addr_list`` validator
    accepts lists at runtime, so the advertised schema must match or strict MCP
    clients will reject list payloads before they reach the validator.
    """
    array_variant = {"type": "array", "items": {"type": "string", "format": "email"}}
    if "anyOf" in schema:
        schema["anyOf"].append(array_variant)
    else:
        schema.pop("type", None)
        schema["anyOf"] = [{"type": "string"}, array_variant]


def _validate_email_address(value: str) -> str:
    """Validate and normalize a single email address."""
    return str(_EMAIL_ADAPTER.validate_python(value))


def _validate_address_list(value: str) -> str:
    """Validate and normalize a comma-separated email address list."""
    addresses = [_validate_email_address(address.strip()) for address in value.split(",") if address.strip()]
    if not addresses:
        raise ValueError("At least one email address is required")
    return ", ".join(addresses)


class StrictBaseStateModel(BaseModel):
    """Base model for canonical persisted Google Mail state.

    Keep Pydantic's normal JSON coercions, such as parsing datetime strings,
    but reject fields that are not part of the declared state contract.
    """

    model_config = ConfigDict(extra="forbid")


NonEmptyStateString = Annotated[str, Field(min_length=1)]
INCOMPLETE_RECIPIENT_FOLDERS: frozenset[str] = frozenset({"Drafts", "Trash"})


class Mailbox(StrictBaseStateModel):
    """Identity of the mailbox owner."""

    email: EmailStr = Field(..., description="Email address of the mailbox owner")
    name: str = Field(..., description="Display name of the mailbox owner")


class Contact(StrictBaseStateModel):
    """A valid email contact in the closed-world simulation."""

    email: EmailStr = Field(..., description="Email address of the contact")
    name: str = Field(..., description="Display name of the contact")


class ContactGroup(StrictBaseStateModel):
    """An addressable email group in the closed-world simulation."""

    email: EmailStr = Field(..., description="Email address of the group")
    name: str = Field(..., description="Display name of the group")
    members: list[EmailStr] = Field(..., min_length=1, description="Contact emails included in the group")


class Folder(StrictBaseStateModel):
    """A custom email folder."""

    name: NonEmptyStateString = Field(..., description="Folder name")


class Attachment(StrictBaseStateModel):
    """An email attachment with base64-encoded content."""

    filename: NonEmptyStateString = Field(..., description="Filename of the attachment")
    content_type: str = Field(..., description="MIME type")
    content_base64: str = Field(
        ...,
        description="Base64-encoded file content",
        json_schema_extra={"contentEncoding": "base64"},
    )

    @field_validator("content_base64")
    @classmethod
    def validate_content_base64(cls, value: str) -> str:
        try:
            base64.b64decode(value, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError("Attachment content_base64 must be valid base64") from exc
        return value

    @property
    def size(self) -> int:
        """Return the decoded size of the attachment in bytes."""
        return len(base64.b64decode(self.content_base64))


class Email(StrictBaseStateModel):
    """An email message in the mailbox."""

    email_id: NonEmptyStateString = Field(..., description="Unique email identifier")
    folder: NonEmptyStateString = Field(..., description="Folder containing the email")
    subject: str = Field(..., description="Email subject line")
    from_addr: EmailStr = Field(..., description="Sender email address")
    to_addr: str = Field(
        ...,
        description="Recipient(s), comma-separated string or list of strings",
        json_schema_extra=_allow_list_of_strings,
    )
    cc_addr: str | None = Field(
        default=None,
        description="CC recipients, comma-separated string or list of strings",
        json_schema_extra=_allow_list_of_strings,
    )
    bcc_addr: str | None = Field(
        default=None,
        description="BCC recipients, comma-separated string or list of strings",
        json_schema_extra=_allow_list_of_strings,
    )
    date: datetime = Field(..., description="Date/time the email was sent")
    message_id: NonEmptyStateString = Field(..., description="RFC 2822 Message-ID")
    in_reply_to: str | None = Field(default=None, description="Message-ID of the email this is replying to")
    body_text: str = Field(..., description="Plain text body")
    body_html: str | None = Field(default=None, description="HTML body")
    is_read: bool = Field(default=False, description="Whether the email has been read")
    is_important: bool = Field(default=False, description="Whether the email is marked important")
    labels: list[str] = Field(
        default_factory=list,
        description="Search labels attached to the email",
        validation_alias=AliasChoices("labels", "labelIds", "label_ids"),
    )
    attachments: list[Attachment] = Field(default_factory=list, description="Email attachments")
    scheduled_time: datetime | None = Field(default=None, description="Scheduled send time (None if not scheduled)")

    # Synthetic-data generators often emit ["a@x", "b@y"] for recipient fields
    # even though the canonical form is "a@x, b@y"; accept both on import.
    @field_validator("to_addr", "cc_addr", "bcc_addr", mode="before")
    @classmethod
    def _coerce_addr_list(cls, value: object) -> object:
        if isinstance(value, list):
            return ", ".join(str(a).strip() for a in value if a and str(a).strip())
        return value

    @field_validator("to_addr", "cc_addr", "bcc_addr")
    @classmethod
    def validate_addr_list(cls, value: str | None, info: ValidationInfo) -> str | None:
        if value is None:
            return None
        if not value.strip():
            if info.field_name == "to_addr":
                return ""
            return None
        return _validate_address_list(value)

    @field_validator("labels")
    @classmethod
    def normalize_labels(cls, value: list[str]) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()
        for label in value:
            stripped = str(label).strip()
            if not stripped or stripped in seen:
                continue
            normalized.append(stripped)
            seen.add(stripped)
        return normalized

    @model_validator(mode="after")
    def validate_attachment_filenames_are_unique(self) -> Self:
        """Ensure attachment filename lookups are unambiguous within an email."""
        filenames = [attachment.filename for attachment in self.attachments]
        if len(filenames) != len(set(filenames)):
            raise ValueError("Duplicate attachment filenames")
        return self

    @model_validator(mode="after")
    def validate_scheduled_time_matches_folder(self) -> Self:
        """Keep scheduled_time consistent with the Scheduled folder."""
        if self.folder == "Scheduled" and self.scheduled_time is None:
            raise ValueError("Scheduled emails must have scheduled_time")
        if self.folder != "Scheduled" and self.scheduled_time is not None:
            raise ValueError("Only Scheduled emails may have scheduled_time")
        return self

    @model_validator(mode="after")
    def validate_recipient_presence_matches_folder(self) -> Self:
        """Allow incomplete drafts/trash, but require recipients for active messages."""
        if self.folder not in INCOMPLETE_RECIPIENT_FOLDERS and not any(
            [self.to_addr.strip(), self.cc_addr, self.bcc_addr]
        ):
            raise ValueError("Active emails require at least one recipient")
        return self


SYSTEM_FOLDERS: frozenset[str] = frozenset({"INBOX", "Sent", "Drafts", "Trash", "Scheduled"})
MailboxId = Annotated[str, Field(min_length=1, description="Mailbox identifier")]


class MailboxData(StrictBaseStateModel):
    """Root schema for mock mailbox data."""

    mailbox: Mailbox = Field(..., description="Identity of the mailbox owner")
    contacts: list[Contact] = Field(default_factory=list, description="Valid contacts")
    groups: list[ContactGroup] = Field(default_factory=list, description="Addressable contact groups")
    folders: list[Folder] = Field(default_factory=list, description="Custom folders")
    emails: list[Email] = Field(default_factory=list, description="All emails (including drafts in Drafts folder)")
    next_email_id: int = Field(default=1, ge=1, description="Next email ID counter")

    @model_validator(mode="after")
    def validate_contacts_and_groups_have_unique_emails(self) -> Self:
        """Ensure no duplicate address book entries across contacts and groups."""
        emails = [c.email.lower() for c in self.contacts]
        emails.extend(group.email.lower() for group in self.groups)
        if len(emails) != len(set(emails)):
            raise ValueError("Duplicate email addresses in contacts or groups")
        return self

    @model_validator(mode="after")
    def validate_folders_have_unique_names(self) -> Self:
        """Ensure custom folder names are unique."""
        folder_names = [f.name for f in self.folders]
        if len(folder_names) != len(set(folder_names)):
            raise ValueError("Duplicate folder names")
        return self

    @model_validator(mode="after")
    def validate_folders_are_custom_only(self) -> Self:
        """Ensure fixed system folders are not persisted as custom folders."""
        system_folders = sorted({folder.name for folder in self.folders if folder.name in SYSTEM_FOLDERS})
        if system_folders:
            raise ValueError(f"System folders must not be listed as custom folders: {', '.join(system_folders)}")
        return self

    @model_validator(mode="after")
    def validate_emails_have_unique_ids(self) -> Self:
        """Ensure every email has a unique ID."""
        email_ids = [e.email_id for e in self.emails]
        if len(email_ids) != len(set(email_ids)):
            raise ValueError("Duplicate email IDs")
        return self

    @model_validator(mode="after")
    def validate_next_email_id_is_unused(self) -> Self:
        """Ensure the numeric ID counter will not collide with imported emails."""
        numeric_email_ids = [int(email.email_id) for email in self.emails if email.email_id.isdecimal()]
        if numeric_email_ids and self.next_email_id <= max(numeric_email_ids):
            raise ValueError("next_email_id must be greater than all numeric email IDs")
        return self

    @model_validator(mode="after")
    def validate_email_folders_exist(self) -> Self:
        """Ensure every email references a known system or custom folder."""
        folder_names = self.get_all_folder_names()
        unknown_folders = sorted({email.folder for email in self.emails if email.folder not in folder_names})
        if unknown_folders:
            raise ValueError(f"Email references unknown folders: {', '.join(unknown_folders)}")
        return self

    @model_validator(mode="after")
    def validate_group_members(self) -> Self:
        """Ensure groups reference known person contacts or the mailbox owner."""
        valid_member_emails = {self.mailbox.email.lower()} | {contact.email.lower() for contact in self.contacts}
        for group in self.groups:
            normalized_members = [member.strip().lower() for member in group.members]
            if any(not member for member in normalized_members):
                raise ValueError(f"Group has empty members: {group.email}")
            if len(normalized_members) != len(set(normalized_members)):
                raise ValueError(f"Group has duplicate members: {group.email}")
            if group.email.lower() in normalized_members:
                raise ValueError(f"Group cannot include itself as a member: {group.email}")
            unknown_members = sorted(set(normalized_members) - valid_member_emails)
            if unknown_members:
                raise ValueError(f"Group references unknown members for {group.email}: {', '.join(unknown_members)}")
        return self

    def get_all_folder_names(self) -> set[str]:
        """Return all folder names (system + custom)."""
        custom = {f.name for f in self.folders}
        return set(SYSTEM_FOLDERS) | custom

    def get_contact_by_email(self, email: str) -> Contact | None:
        """Find a contact by email address (case-insensitive)."""
        for contact in self.contacts:
            if contact.email.lower() == email.lower():
                return contact
        return None

    def get_group_by_email(self, email: str) -> ContactGroup | None:
        """Find a group by email address (case-insensitive)."""
        for group in self.groups:
            if group.email.lower() == email.lower():
                return group
        return None

    def is_valid_recipient(self, email: str) -> bool:
        """Check if an email address is a valid recipient (closed-world)."""
        return (
            email.lower() == self.mailbox.email.lower()
            or self.get_contact_by_email(email) is not None
            or self.get_group_by_email(email) is not None
        )

    def is_mailbox_member_of_group(self, group: ContactGroup) -> bool:
        """Check if the mailbox owner is a member of a group contact."""
        mailbox_email = self.mailbox.email.lower()
        return any(member.lower() == mailbox_email for member in group.members)


class MultiMailboxData(StrictBaseStateModel):
    """Root schema for multi-mailbox Google Mail state."""

    mailboxes: dict[MailboxId, MailboxData] = Field(
        ...,
        min_length=1,
        description="Mailbox state keyed by mailbox identifier.",
    )


type GoogleMailState = MailboxData | MultiMailboxData
