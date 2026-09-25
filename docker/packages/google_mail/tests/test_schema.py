"""Tests for schema validators on MailboxData / Email."""

import pytest
from pydantic import ValidationError

from google_mail.models import Email, MailboxData, MultiMailboxData


def _sample_email(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "email_id": "1",
        "folder": "INBOX",
        "subject": "s",
        "from_addr": "bob@example.com",
        "to_addr": "alice@example.com",
        "date": "2024-01-15T10:00:00Z",
        "message_id": "<m@x>",
        "body_text": "hi",
    }
    base.update(overrides)
    return base


def test_email_accepts_list_for_addr_fields():
    email = Email.model_validate(
        _sample_email(
            to_addr=["alice@example.com", "bob@example.com"],
            cc_addr=["carol@example.com"],
            bcc_addr=["dan@example.com", "eve@example.com"],
        )
    )
    assert email.to_addr == "alice@example.com, bob@example.com"
    assert email.cc_addr == "carol@example.com"
    assert email.bcc_addr == "dan@example.com, eve@example.com"


def test_email_list_validator_trims_and_drops_empties():
    email = Email.model_validate(_sample_email(to_addr=["  alice@example.com  ", "", "bob@example.com"]))
    assert email.to_addr == "alice@example.com, bob@example.com"


def test_email_addr_string_passthrough_unchanged():
    email = Email.model_validate(_sample_email(to_addr="alice@example.com, bob@example.com"))
    assert email.to_addr == "alice@example.com, bob@example.com"


@pytest.mark.parametrize("alias", ["labelIds", "label_ids"])
def test_email_accepts_label_aliases(alias):
    email = Email.model_validate(_sample_email(**{alias: ["INBOX", "Client"]}))
    assert email.labels == ["INBOX", "Client"]
    assert "labelIds" not in email.model_dump(mode="json")
    assert "label_ids" not in email.model_dump(mode="json")


def test_email_normalizes_labels():
    email = Email.model_validate(_sample_email(labels=[" client ", "", "client", "INBOX", "  "]))
    assert email.labels == ["client", "INBOX"]


@pytest.mark.parametrize("field", ["email_id", "folder", "message_id"])
def test_email_rejects_empty_identity_fields(field):
    with pytest.raises(ValidationError):
        Email.model_validate(_sample_email(**{field: ""}))


def test_email_allows_empty_subject_and_body_for_sparse_messages():
    email = Email.model_validate(_sample_email(subject="", body_text="", body_html=""))
    assert email.subject == ""
    assert email.body_text == ""
    assert email.body_html == ""


def test_email_accepts_empty_to_addr_for_drafts():
    email = Email.model_validate(_sample_email(folder="Drafts", to_addr=""))
    assert email.to_addr == ""


def test_email_accepts_empty_to_addr_for_trash():
    email = Email.model_validate(_sample_email(folder="Trash", to_addr=""))
    assert email.to_addr == ""


def test_email_rejects_empty_to_addr_outside_drafts():
    with pytest.raises(ValidationError):
        Email.model_validate(_sample_email(to_addr=""))


def test_email_allows_active_message_with_only_cc_recipient():
    email = Email.model_validate(_sample_email(to_addr="", cc_addr="bob@example.com"))
    assert email.to_addr == ""
    assert email.cc_addr == "bob@example.com"


def test_email_normalizes_empty_optional_recipient_fields_to_none():
    email = Email.model_validate(_sample_email(cc_addr="", bcc_addr=" "))
    assert email.cc_addr is None
    assert email.bcc_addr is None


@pytest.mark.parametrize(
    "field,value",
    [
        ("from_addr", "not-an-email"),
        ("to_addr", "not-an-email"),
        ("cc_addr", "alice@example.com, not-an-email"),
        ("bcc_addr", ["dan@example.com", "not-an-email"]),
    ],
)
def test_email_rejects_invalid_addr_fields(field, value):
    with pytest.raises(ValidationError):
        Email.model_validate(_sample_email(**{field: value}))


def test_mailbox_data_import_coerces_email_addr_lists():
    """import_state feeds JSON through MailboxData; list-form addrs should round-trip to strings."""
    data = MailboxData.model_validate(
        {
            "mailbox": {"email": "alice@example.com", "name": "Alice"},
            "contacts": [],
            "folders": [],
            "emails": [
                _sample_email(to_addr=["alice@example.com", "bob@example.com"]),
            ],
            "next_email_id": 2,
        }
    )
    assert data.emails[0].to_addr == "alice@example.com, bob@example.com"


@pytest.mark.parametrize(
    "payload",
    [
        {"mailbox": {"email": "not-an-email", "name": "Alice"}},
        {
            "mailbox": {"email": "alice@example.com", "name": "Alice"},
            "contacts": [{"email": "not-an-email", "name": "Bob"}],
        },
        {
            "mailbox": {"email": "alice@example.com", "name": "Alice"},
            "groups": [{"email": "not-an-email", "name": "Team", "members": ["alice@example.com"]}],
        },
        {
            "mailbox": {"email": "alice@example.com", "name": "Alice"},
            "groups": [{"email": "team@example.com", "name": "Team", "members": ["not-an-email"]}],
        },
    ],
)
def test_mailbox_data_rejects_invalid_email_addresses(payload):
    with pytest.raises(ValidationError):
        MailboxData.model_validate(payload)


@pytest.mark.parametrize(
    "payload",
    [
        {
            "mailbox": {"email": "alice@example.com", "name": "Alice"},
            "unexpected": True,
        },
        {
            "mailbox": {"email": "alice@example.com", "name": "Alice", "unexpected": True},
        },
        {
            "mailbox": {"email": "alice@example.com", "name": "Alice"},
            "contacts": [{"email": "bob@example.com", "name": "Bob", "unexpected": True}],
        },
        {
            "mailbox": {"email": "alice@example.com", "name": "Alice"},
            "folders": [{"name": "Projects", "unexpected": True}],
        },
        {
            "mailbox": {"email": "alice@example.com", "name": "Alice"},
            "emails": [_sample_email(unexpected=True)],
        },
        {
            "mailbox": {"email": "alice@example.com", "name": "Alice"},
            "emails": [
                _sample_email(
                    attachments=[
                        {
                            "filename": "notes.txt",
                            "content_type": "text/plain",
                            "content_base64": "aGk=",
                            "unexpected": True,
                        }
                    ]
                )
            ],
        },
    ],
)
def test_mailbox_data_rejects_unknown_state_fields(payload):
    with pytest.raises(ValidationError):
        MailboxData.model_validate(payload)


def test_mailbox_data_still_parses_json_datetime_strings():
    data = MailboxData.model_validate(
        {
            "mailbox": {"email": "alice@example.com", "name": "Alice"},
            "emails": [_sample_email(date="2024-01-15T10:00:00Z")],
            "next_email_id": 2,
        }
    )
    assert data.emails[0].date.isoformat() == "2024-01-15T10:00:00+00:00"


def test_mailbox_data_rejects_non_positive_next_email_id():
    with pytest.raises(ValidationError):
        MailboxData.model_validate(
            {
                "mailbox": {"email": "alice@example.com", "name": "Alice"},
                "next_email_id": 0,
            }
        )


def test_mailbox_data_rejects_duplicate_folder_names():
    with pytest.raises(ValidationError):
        MailboxData.model_validate(
            {
                "mailbox": {"email": "alice@example.com", "name": "Alice"},
                "folders": [{"name": "Projects"}, {"name": "Projects"}],
            }
        )


def test_mailbox_data_rejects_empty_folder_name():
    with pytest.raises(ValidationError):
        MailboxData.model_validate(
            {
                "mailbox": {"email": "alice@example.com", "name": "Alice"},
                "folders": [{"name": ""}],
            }
        )


@pytest.mark.parametrize("folder_name", ["INBOX", "Sent", "Drafts", "Trash", "Scheduled"])
def test_mailbox_data_rejects_system_folders_as_custom_folders(folder_name):
    with pytest.raises(ValidationError):
        MailboxData.model_validate(
            {
                "mailbox": {"email": "alice@example.com", "name": "Alice"},
                "folders": [{"name": folder_name}],
            }
        )


def test_mailbox_data_rejects_case_insensitive_duplicate_contact_emails():
    with pytest.raises(ValidationError):
        MailboxData.model_validate(
            {
                "mailbox": {"email": "alice@example.com", "name": "Alice"},
                "contacts": [
                    {"email": "bob@example.com", "name": "Bob"},
                    {"email": "BOB@example.com", "name": "Other Bob"},
                ],
            }
        )


def test_mailbox_data_rejects_duplicate_email_ids():
    with pytest.raises(ValidationError):
        MailboxData.model_validate(
            {
                "mailbox": {"email": "alice@example.com", "name": "Alice"},
                "emails": [
                    _sample_email(email_id="1", subject="First"),
                    _sample_email(email_id="1", subject="Second"),
                ],
            }
        )


def test_mailbox_data_rejects_next_email_id_that_collides_with_numeric_email_id():
    with pytest.raises(ValidationError):
        MailboxData.model_validate(
            {
                "mailbox": {"email": "alice@example.com", "name": "Alice"},
                "emails": [_sample_email(email_id="5")],
                "next_email_id": 5,
            }
        )


def test_mailbox_data_rejects_next_email_id_below_existing_numeric_email_id():
    with pytest.raises(ValidationError):
        MailboxData.model_validate(
            {
                "mailbox": {"email": "alice@example.com", "name": "Alice"},
                "emails": [_sample_email(email_id="7")],
                "next_email_id": 6,
            }
        )


def test_mailbox_data_allows_next_email_id_with_non_numeric_email_ids():
    data = MailboxData.model_validate(
        {
            "mailbox": {"email": "alice@example.com", "name": "Alice"},
            "emails": [_sample_email(email_id="draft-1")],
            "next_email_id": 1,
        }
    )
    assert data.next_email_id == 1


def test_mailbox_data_rejects_email_with_unknown_folder():
    with pytest.raises(ValidationError):
        MailboxData.model_validate(
            {
                "mailbox": {"email": "alice@example.com", "name": "Alice"},
                "emails": [_sample_email(folder="Projects")],
            }
        )


def test_mailbox_data_accepts_empty_attachment_content():
    data = MailboxData.model_validate(
        {
            "mailbox": {"email": "alice@example.com", "name": "Alice"},
            "emails": [
                _sample_email(
                    attachments=[
                        {
                            "filename": "empty.txt",
                            "content_type": "text/plain",
                            "content_base64": "",
                        }
                    ]
                )
            ],
            "next_email_id": 2,
        }
    )
    assert data.emails[0].attachments[0].size == 0


def test_mailbox_data_rejects_invalid_attachment_base64():
    with pytest.raises(ValidationError):
        MailboxData.model_validate(
            {
                "mailbox": {"email": "alice@example.com", "name": "Alice"},
                "emails": [
                    _sample_email(
                        attachments=[
                            {
                                "filename": "broken.txt",
                                "content_type": "text/plain",
                                "content_base64": "not base64!",
                            }
                        ]
                    )
                ],
            }
        )


def test_mailbox_data_rejects_empty_attachment_filename():
    with pytest.raises(ValidationError):
        MailboxData.model_validate(
            {
                "mailbox": {"email": "alice@example.com", "name": "Alice"},
                "emails": [
                    _sample_email(
                        attachments=[
                            {
                                "filename": "",
                                "content_type": "text/plain",
                                "content_base64": "aGk=",
                            }
                        ]
                    )
                ],
            }
        )


def test_mailbox_data_rejects_duplicate_attachment_filenames_per_email():
    with pytest.raises(ValidationError):
        MailboxData.model_validate(
            {
                "mailbox": {"email": "alice@example.com", "name": "Alice"},
                "emails": [
                    _sample_email(
                        attachments=[
                            {
                                "filename": "notes.txt",
                                "content_type": "text/plain",
                                "content_base64": "aGk=",
                            },
                            {
                                "filename": "notes.txt",
                                "content_type": "text/plain",
                                "content_base64": "Ynll",
                            },
                        ]
                    )
                ],
            }
        )


def test_mailbox_data_accepts_scheduled_email_with_scheduled_time():
    data = MailboxData.model_validate(
        {
            "mailbox": {"email": "alice@example.com", "name": "Alice"},
            "emails": [
                _sample_email(
                    folder="Scheduled",
                    scheduled_time="2024-01-16T10:00:00Z",
                )
            ],
            "next_email_id": 2,
        }
    )
    scheduled_time = data.emails[0].scheduled_time
    assert scheduled_time is not None
    assert scheduled_time.isoformat() == "2024-01-16T10:00:00+00:00"


def test_mailbox_data_rejects_scheduled_folder_without_scheduled_time():
    with pytest.raises(ValidationError):
        MailboxData.model_validate(
            {
                "mailbox": {"email": "alice@example.com", "name": "Alice"},
                "emails": [_sample_email(folder="Scheduled")],
            }
        )


def test_mailbox_data_rejects_scheduled_time_outside_scheduled_folder():
    with pytest.raises(ValidationError):
        MailboxData.model_validate(
            {
                "mailbox": {"email": "alice@example.com", "name": "Alice"},
                "emails": [_sample_email(folder="INBOX", scheduled_time="2024-01-16T10:00:00Z")],
            }
        )


def test_mailbox_data_accepts_group_members_from_contacts_and_mailbox_owner():
    data = MailboxData.model_validate(
        {
            "mailbox": {"email": "alice@example.com", "name": "Alice"},
            "contacts": [
                {"email": "bob@example.com", "name": "Bob"},
            ],
            "groups": [
                {"email": "team@example.com", "name": "Team", "members": ["alice@example.com", "bob@example.com"]},
            ],
        }
    )
    group = data.get_group_by_email("team@example.com")
    assert group is not None
    assert group.members == ["alice@example.com", "bob@example.com"]


@pytest.mark.parametrize(
    "members",
    [
        [],
        [""],
        ["bob@example.com", "bob@example.com"],
        ["team@example.com"],
        ["unknown@example.com"],
    ],
)
def test_mailbox_data_rejects_invalid_group_members(members):
    with pytest.raises(ValidationError):
        MailboxData.model_validate(
            {
                "mailbox": {"email": "alice@example.com", "name": "Alice"},
                "contacts": [
                    {"email": "bob@example.com", "name": "Bob"},
                ],
                "groups": [
                    {"email": "team@example.com", "name": "Team", "members": members},
                ],
            }
        )


def test_multi_mailbox_data_validates_nested_mailboxes():
    state = MultiMailboxData.model_validate(
        {
            "mailboxes": {
                "work": {
                    "mailbox": {"email": "alice@example.com", "name": "Alice"},
                    "emails": [_sample_email()],
                    "next_email_id": 2,
                }
            }
        }
    )
    assert state.mailboxes["work"].mailbox.email == "alice@example.com"


def test_multi_mailbox_data_rejects_unknown_wrapper_fields():
    with pytest.raises(ValidationError):
        MultiMailboxData.model_validate(
            {
                "mailboxes": {
                    "work": {
                        "mailbox": {"email": "alice@example.com", "name": "Alice"},
                    }
                },
                "unexpected": True,
            }
        )


def test_multi_mailbox_data_rejects_empty_mailbox_map():
    with pytest.raises(ValidationError):
        MultiMailboxData.model_validate({"mailboxes": {}})


def test_multi_mailbox_data_rejects_empty_mailbox_id():
    with pytest.raises(ValidationError):
        MultiMailboxData.model_validate(
            {
                "mailboxes": {
                    "": {
                        "mailbox": {"email": "alice@example.com", "name": "Alice"},
                    }
                }
            }
        )


def test_addr_fields_advertise_array_variant_in_json_schema():
    """MCP clients validate payloads against inputSchema; the list form must be advertised."""
    email_schema = MailboxData.model_json_schema()["$defs"]["Email"]["properties"]
    array_variant = {"type": "array", "items": {"type": "string", "format": "email"}}

    to_variants = email_schema["to_addr"]["anyOf"]
    assert {"type": "string"} in to_variants
    assert array_variant in to_variants

    cc_variants = email_schema["cc_addr"]["anyOf"]
    assert {"type": "string"} in cc_variants
    assert {"type": "null"} in cc_variants
    assert array_variant in cc_variants


def test_address_book_fields_advertise_email_format_in_json_schema():
    schema = MailboxData.model_json_schema()

    assert schema["$defs"]["Mailbox"]["properties"]["email"]["format"] == "email"
    assert schema["$defs"]["Contact"]["properties"]["email"]["format"] == "email"
    assert schema["$defs"]["ContactGroup"]["properties"]["email"]["format"] == "email"
    assert schema["$defs"]["ContactGroup"]["properties"]["members"]["items"]["format"] == "email"
    assert schema["$defs"]["Email"]["properties"]["from_addr"]["format"] == "email"


def test_attachment_content_advertises_base64_encoding_in_json_schema():
    attachment_schema = MailboxData.model_json_schema()["$defs"]["Attachment"]["properties"]
    assert attachment_schema["content_base64"]["contentEncoding"] == "base64"


def test_non_empty_state_fields_advertise_min_length_in_json_schema():
    schema = MailboxData.model_json_schema()["$defs"]
    assert schema["Folder"]["properties"]["name"]["minLength"] == 1
    assert schema["Attachment"]["properties"]["filename"]["minLength"] == 1
    assert schema["Email"]["properties"]["email_id"]["minLength"] == 1
    assert schema["Email"]["properties"]["folder"]["minLength"] == 1
    assert schema["Email"]["properties"]["message_id"]["minLength"] == 1


def test_multi_mailbox_data_advertises_non_empty_mailboxes_in_json_schema():
    mailboxes_schema = MultiMailboxData.model_json_schema()["properties"]["mailboxes"]
    assert mailboxes_schema["minProperties"] == 1
    assert mailboxes_schema["propertyNames"]["minLength"] == 1
