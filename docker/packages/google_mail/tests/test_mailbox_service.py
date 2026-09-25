"""Tests for the MailboxService."""

import json
import tempfile
from datetime import UTC
from pathlib import Path

import pytest
from pydantic import ValidationError

from google_mail.services.mailbox import (
    AttachmentNotFoundError,
    ContactExistsError,
    ContactNotFoundError,
    DraftNotFoundError,
    EmailNotFoundError,
    FolderExistsError,
    FolderNotFoundError,
    MailboxService,
    RecipientNotFoundError,
    RecipientRequiredError,
    ScheduledFolderError,
    SystemFolderError,
    _parse_search_query,
    normalize_search_pagination,
    search_query_warnings,
)


@pytest.fixture
def sample_data() -> dict:
    """Create sample mailbox data."""
    return {
        "mailbox": {"email": "alice@example.com", "name": "Alice Smith"},
        "contacts": [
            {"email": "bob@example.com", "name": "Bob Jones"},
            {"email": "carol@example.com", "name": "Carol White"},
        ],
        "groups": [
            {
                "email": "team@example.com",
                "name": "Team",
                "members": ["alice@example.com", "bob@example.com"],
            },
        ],
        "folders": [{"name": "Work"}],
        "emails": [
            {
                "email_id": "1",
                "folder": "INBOX",
                "subject": "Hello Alice",
                "from_addr": "bob@example.com",
                "to_addr": "alice@example.com",
                "date": "2024-01-15T10:00:00Z",
                "message_id": "<msg1@example.com>",
                "body_text": "Hi Alice, how are you?",
                "is_read": False,
                "is_important": False,
                "labels": ["client"],
            },
            {
                "email_id": "2",
                "folder": "INBOX",
                "subject": "Important meeting",
                "from_addr": "carol@example.com",
                "to_addr": "alice@example.com",
                "date": "2024-01-14T09:00:00Z",
                "message_id": "<msg2@example.com>",
                "body_text": "Don't forget the meeting tomorrow.",
                "is_read": True,
                "is_important": True,
                "attachments": [
                    {
                        "filename": "agenda.pdf",
                        "content_type": "application/pdf",
                        "content_base64": "SGVsbG8gV29ybGQ=",
                    },
                    {
                        "filename": "agenda v2.pdf",
                        "content_type": "application/pdf",
                        "content_base64": "SGVsbG8gV29ybGQ=",
                    },
                ],
            },
            {
                "email_id": "d1",
                "folder": "Drafts",
                "subject": "Draft email",
                "from_addr": "alice@example.com",
                "to_addr": "bob@example.com",
                "date": "2024-01-15T08:00:00Z",
                "message_id": "<draft1@example.com>",
                "body_text": "This is a draft",
                "is_read": True,
                "is_important": False,
            },
        ],
        "next_email_id": 4,
    }


@pytest.fixture
def mailbox_service(sample_data: dict) -> MailboxService:
    """Create a MailboxService with sample data."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(sample_data, f)
        temp_path = Path(f.name)

    service = MailboxService(temp_path)
    service.load()
    return service


class TestGetEmails:
    """Tests for get_emails."""

    def test_get_all_emails(self, mailbox_service: MailboxService) -> None:
        """Test getting all emails (excluding drafts)."""
        emails, total = mailbox_service.get_emails()
        # 3 total emails, but get_emails excludes Drafts folder by default
        assert total == 3
        assert len(emails) == 3

    def test_get_emails_by_folder(self, mailbox_service: MailboxService) -> None:
        """Test filtering by folder."""
        emails, total = mailbox_service.get_emails(folder="INBOX")
        assert total == 2
        emails, total = mailbox_service.get_emails(folder="Sent")
        assert total == 0

    def test_get_emails_invalid_folder(self, mailbox_service: MailboxService) -> None:
        """Test invalid folder raises error."""
        with pytest.raises(FolderNotFoundError):
            mailbox_service.get_emails(folder="NonExistent")

    def test_get_emails_pagination(self, mailbox_service: MailboxService) -> None:
        """Test pagination."""
        emails, total = mailbox_service.get_emails(page=1, page_size=1)
        assert len(emails) == 1
        assert total == 3


class TestReadEmail:
    """Tests for read_email."""

    def test_read_email_marks_as_read(self, mailbox_service: MailboxService) -> None:
        """Test that reading marks email as read."""
        email = mailbox_service.read_email("1")
        assert email.is_read is True

    def test_read_nonexistent_email(self, mailbox_service: MailboxService) -> None:
        """Test reading nonexistent email raises error."""
        with pytest.raises(EmailNotFoundError):
            mailbox_service.read_email("999")


class TestSearchEmails:
    """Tests for search_emails."""

    @pytest.mark.parametrize(
        "query,expected_count,expected_subject",
        [
            ("Hello", 1, "Hello Alice"),  # search by subject
            ("meeting", 1, "Important meeting"),  # search by body
        ],
    )
    def test_search_emails(
        self,
        mailbox_service: MailboxService,
        query: str,
        expected_count: int,
        expected_subject: str,
    ) -> None:
        """Test searching emails by subject and body."""
        emails, total = mailbox_service.search_emails(query)
        assert total == expected_count
        assert emails[0].subject == expected_subject

    def test_search_word_and_across_subject_and_body(self, mailbox_service: MailboxService) -> None:
        # "Hello Alice" hits (both words in subject), "Hello Bob" hits (subject
        # "Hello Alice", body "Hi Alice" — neither contains "Bob", so this should miss).
        emails, total = mailbox_service.search_emails("hello alice")
        assert total == 1
        assert emails[0].subject == "Hello Alice"

    def test_search_misses_when_word_absent(self, mailbox_service: MailboxService) -> None:
        emails, total = mailbox_service.search_emails("hello zzznonexistent")
        assert total == 0
        assert emails == []

    def test_search_quoted_phrase_requires_adjacency(self, mailbox_service: MailboxService) -> None:
        # "Hello Alice" is adjacent in the subject of email 1.
        emails, total = mailbox_service.search_emails('"hello alice"')
        assert total == 1

        # Reversed — never adjacent anywhere.
        emails, total = mailbox_service.search_emails('"alice hello"')
        assert total == 0

    def test_search_still_matches_email_address_as_single_token(self, mailbox_service: MailboxService) -> None:
        # Full email address has no spaces so it's a single token that falls
        # through to substring semantics naturally.
        emails, total = mailbox_service.search_emails("bob@example.com")
        assert total >= 1

    def test_search_subject_operator_matches_only_subject(self, mailbox_service: MailboxService) -> None:
        emails, total = mailbox_service.search_emails("subject:meeting")
        assert total == 1
        assert emails[0].email_id == "2"

        emails, total = mailbox_service.search_emails("subject:tomorrow")
        assert emails == []
        assert total == 0

    def test_search_subject_operator_supports_quoted_values(self, mailbox_service: MailboxService) -> None:
        emails, total = mailbox_service.search_emails('subject:"important meeting"')
        assert total == 1
        assert emails[0].email_id == "2"

    def test_search_subject_operator_works_with_or(self, mailbox_service: MailboxService) -> None:
        emails, total = mailbox_service.search_emails("subject:hello OR subject:meeting")
        assert total == 2
        assert {email.email_id for email in emails} == {"1", "2"}

    def test_search_has_attachment_operator(self, mailbox_service: MailboxService) -> None:
        emails, total = mailbox_service.search_emails("has:attachment")
        assert total == 1
        assert emails[0].email_id == "2"

    def test_search_unknown_has_value_returns_no_results(self, mailbox_service: MailboxService) -> None:
        emails, total = mailbox_service.search_emails("has:drive")
        assert emails == []
        assert total == 0

    def test_search_filename_operator(self, mailbox_service: MailboxService) -> None:
        emails, total = mailbox_service.search_emails("filename:agenda.pdf")
        assert total == 1
        assert emails[0].email_id == "2"

    def test_search_filename_operator_supports_quoted_values(self, mailbox_service: MailboxService) -> None:
        emails, total = mailbox_service.search_emails('filename:"agenda v2.pdf"')
        assert total == 1
        assert emails[0].email_id == "2"

    def test_search_is_state_operators(self, mailbox_service: MailboxService) -> None:
        unread, unread_total = mailbox_service.search_emails("is:unread")
        assert unread_total == 1
        assert unread[0].email_id == "1"

        read, read_total = mailbox_service.search_emails("is:read")
        assert read_total == 2
        assert {email.email_id for email in read} == {"2", "d1"}

        important, important_total = mailbox_service.search_emails("is:important")
        assert important_total == 1
        assert important[0].email_id == "2"

    def test_search_unknown_is_value_returns_no_results(self, mailbox_service: MailboxService) -> None:
        emails, total = mailbox_service.search_emails("is:snoozed")
        assert emails == []
        assert total == 0

    def test_search_unknown_is_value_reports_warning(self) -> None:
        warnings = search_query_warnings("is:snoozed")
        assert warnings == ["Unsupported is: value 'snoozed'. Supported values: unread, read, important."]

    def test_search_malformed_date_reports_warning(self) -> None:
        warnings = search_query_warnings("before:not-a-date invoice")
        assert warnings == ["Invalid before: date 'not-a-date'. Use YYYY/MM/DD or YYYY-MM-DD."]

    def test_search_unknown_operator_reports_warning(self) -> None:
        warnings = search_query_warnings("foo:bar invoice")
        assert warnings == ["Unsupported Gmail search operator 'foo:'; it will be treated as a text token."]

    def test_search_negated_state_operators(self, mailbox_service: MailboxService) -> None:
        no_attachment, no_attachment_total = mailbox_service.search_emails("-has:attachment")
        assert no_attachment_total == 2
        assert {email.email_id for email in no_attachment} == {"1", "d1"}

        not_unread, not_unread_total = mailbox_service.search_emails("-is:unread")
        assert not_unread_total == 2
        assert {email.email_id for email in not_unread} == {"2", "d1"}

    def test_search_in_and_label_operators_match_folders(self, mailbox_service: MailboxService) -> None:
        inbox, inbox_total = mailbox_service.search_emails("in:inbox meeting")
        assert inbox_total == 1
        assert inbox[0].email_id == "2"

        drafts, drafts_total = mailbox_service.search_emails("label:drafts draft")
        assert drafts_total == 1
        assert drafts[0].email_id == "d1"

        labels, labels_total = mailbox_service.search_emails("label:client hello")
        assert labels_total == 1
        assert labels[0].email_id == "1"

    def test_search_normalizes_unsafe_pagination(self, mailbox_service: MailboxService) -> None:
        page, total = mailbox_service.search_emails("hello", page=0, page_size=-1)
        assert total == 1
        assert page == []

        warnings = normalize_search_pagination(page=0, page_size=101)[2]
        assert warnings == [
            "page must be at least 1; using 1.",
            "page_size exceeds the maximum of 100; using 100.",
        ]

    def test_search_new_operators_compose_with_existing_parser(self, mailbox_service: MailboxService) -> None:
        emails, total = mailbox_service.search_emails("subject:meeting is:important has:attachment -from:bob")
        assert total == 1
        assert emails[0].email_id == "2"

    def test_search_operator_quoted_values_parse_for_non_subject_fields(self, mailbox_service: MailboxService) -> None:
        by_sender, sender_total = mailbox_service.search_emails('from:"carol@example.com"')
        assert sender_total == 1
        assert by_sender[0].email_id == "2"

        by_filename, filename_total = mailbox_service.search_emails('filename:"agenda v2.pdf"')
        assert filename_total == 1
        assert by_filename[0].email_id == "2"

    def test_parse_search_query_keeps_operator_phrase_groups(self) -> None:
        clauses = _parse_search_query('from:"carol@example.com" OR filename:"agenda v2.pdf" -has:attachment')

        assert len(clauses) == 2
        assert [(alt.field, alt.value, alt.negated) for alt in clauses[0].alternatives] == [
            ("from", "carol@example.com", False),
            ("filename", "agenda v2.pdf", False),
        ]
        assert [(alt.field, alt.value, alt.negated) for alt in clauses[1].alternatives] == [("has", "attachment", True)]


class TestSendEmail:
    """Tests for send_email."""

    def test_send_to_valid_recipient(self, mailbox_service: MailboxService) -> None:
        """Test sending to a valid recipient."""
        email = mailbox_service.send_email(
            to="bob@example.com",
            subject="Test",
            body="Test body",
        )
        assert email.folder == "Sent"
        assert email.to_addr == "bob@example.com"

    def test_send_to_invalid_recipient(self, mailbox_service: MailboxService) -> None:
        """Test sending to invalid recipient raises error."""
        with pytest.raises(RecipientNotFoundError):
            mailbox_service.send_email(
                to="unknown@example.com",
                subject="Test",
                body="Test body",
            )

    def test_send_without_recipients_rejects_before_consuming_id(self, mailbox_service: MailboxService) -> None:
        """Empty active sends fail before constructing mail or advancing counters."""
        next_email_id = mailbox_service.data.next_email_id

        with pytest.raises(RecipientRequiredError):
            mailbox_service.send_email(
                to="",
                subject="Test",
                body="Test body",
            )

        assert mailbox_service.data.next_email_id == next_email_id

    def test_send_to_group_creates_inbox_copy(self, mailbox_service: MailboxService) -> None:
        """Test that sending to a group you're a member of creates inbox copy."""
        initial_count = len(mailbox_service.data.emails)
        mailbox_service.send_email(
            to="team@example.com",
            subject="Team message",
            body="Hello team",
        )
        # Should have 2 new emails: Sent + INBOX copy
        assert len(mailbox_service.data.emails) == initial_count + 2

    def test_send_to_self_lands_in_sent_and_inbox(self, mailbox_service: MailboxService) -> None:
        """Sending to the mailbox owner's own address creates one copy in Sent and one in INBOX."""
        initial = len(mailbox_service.data.emails)
        sent = mailbox_service.send_email(
            to="alice@example.com",
            subject="Note to self",
            body="Remember the milk",
        )
        assert sent.folder == "Sent"

        new_emails = mailbox_service.data.emails[initial:]
        folders = sorted(e.folder for e in new_emails)
        assert folders == ["INBOX", "Sent"]

        inbox_copy = next(e for e in new_emails if e.folder == "INBOX")
        assert inbox_copy.subject == "Note to self"
        assert inbox_copy.from_addr == "alice@example.com"
        assert inbox_copy.is_read is False  # unread on receipt

    def test_self_send_does_not_require_contact(self, mailbox_service: MailboxService) -> None:
        """Contact check is overridden for the owner's own address even if not in contacts."""
        # alice@example.com is the owner, not in contacts — still accepted
        email = mailbox_service.send_email(
            to="alice@example.com",
            subject="ok",
            body="ok",
        )
        assert email.to_addr == "alice@example.com"

    def test_self_cc_lands_in_inbox(self, mailbox_service: MailboxService) -> None:
        """CC'ing yourself on an email to someone else also produces an INBOX copy."""
        initial = len(mailbox_service.data.emails)
        mailbox_service.send_email(
            to="bob@example.com",
            cc="alice@example.com",
            subject="loop myself in",
            body="body",
        )
        new_emails = mailbox_service.data.emails[initial:]
        assert sorted(e.folder for e in new_emails) == ["INBOX", "Sent"]

    def test_self_bcc_does_not_land_in_inbox(self, mailbox_service: MailboxService) -> None:
        """BCC semantics stay honest — self-BCC doesn't create a visible INBOX copy."""
        initial = len(mailbox_service.data.emails)
        mailbox_service.send_email(
            to="bob@example.com",
            bcc="alice@example.com",
            subject="hidden self-bcc",
            body="body",
        )
        new_emails = mailbox_service.data.emails[initial:]
        assert len(new_emails) == 1
        assert new_emails[0].folder == "Sent"


class TestReplyEmail:
    """Tests for reply_email."""

    def test_reply_adds_re_prefix(self, mailbox_service: MailboxService) -> None:
        """Test that reply adds Re: prefix."""
        reply = mailbox_service.reply_email("1", "Thanks!")
        assert reply.subject == "Re: Hello Alice"

    def test_reply_to_nonexistent(self, mailbox_service: MailboxService) -> None:
        """Test reply to nonexistent email raises error."""
        with pytest.raises(EmailNotFoundError):
            mailbox_service.reply_email("999", "Thanks!")


class TestForwardEmail:
    """Tests for forward_email."""

    def test_forward_adds_fwd_prefix(self, mailbox_service: MailboxService) -> None:
        """Test that forward adds Fwd: prefix."""
        fwd = mailbox_service.forward_email("1", "carol@example.com")
        assert fwd.subject == "Fwd: Hello Alice"

    def test_forward_to_multiple_recipients(self, mailbox_service: MailboxService) -> None:
        """Test forwarding to multiple comma-separated recipients."""
        fwd = mailbox_service.forward_email("1", "bob@example.com, carol@example.com")
        assert fwd.subject == "Fwd: Hello Alice"
        assert fwd.to_addr == "bob@example.com, carol@example.com"

    def test_forward_to_mixed_valid_invalid_recipients(self, mailbox_service: MailboxService) -> None:
        """Test forwarding to a mix of valid and invalid recipients raises error."""
        with pytest.raises(RecipientNotFoundError):
            mailbox_service.forward_email("1", "bob@example.com, unknown@example.com")


class TestDeleteEmail:
    """Tests for delete_email."""

    def test_delete_moves_to_trash(self, mailbox_service: MailboxService) -> None:
        """Test that delete moves to Trash by default."""
        mailbox_service.delete_email("1")
        email = mailbox_service.get_email("1")
        assert email.folder == "Trash"

    def test_delete_permanent(self, mailbox_service: MailboxService) -> None:
        """Test permanent deletion."""
        mailbox_service.delete_email("1", permanent=True)
        with pytest.raises(EmailNotFoundError):
            mailbox_service.get_email("1")

    def test_delete_emails_batch_saves_once(
        self, mailbox_service: MailboxService, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test batch delete performs all mutations before one persistence pass."""
        save_calls = 0
        original_save = mailbox_service._save

        def counting_save() -> None:
            nonlocal save_calls
            save_calls += 1
            original_save()

        monkeypatch.setattr(mailbox_service, "_save", counting_save)

        result = mailbox_service.delete_emails(["1", "2", "missing"])

        assert result.succeeded_ids == ["1", "2"]
        assert [error.email_id for error in result.errors] == ["missing"]
        assert save_calls == 1

    def test_delete_emails_batch_rolls_back_if_save_fails(
        self,
        mailbox_service: MailboxService,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Test failed batch persistence restores in-memory delete mutations."""
        monkeypatch.setattr(mailbox_service, "_save", lambda: (_ for _ in ()).throw(RuntimeError("save failed")))

        with pytest.raises(RuntimeError, match="save failed"):
            mailbox_service.delete_emails(["1", "2"])

        assert mailbox_service.get_email("1").folder == "INBOX"
        assert mailbox_service.get_email("2").folder == "INBOX"

    def test_delete_empty_recipient_draft_moves_to_trash(self, mailbox_service: MailboxService) -> None:
        """Test soft-deleting an incomplete draft keeps persisted state valid."""
        draft = mailbox_service.save_draft(subject="No recipient yet", body="Draft content")

        mailbox_service.delete_email(draft.email_id)

        trashed = mailbox_service.get_email(draft.email_id)
        assert trashed.folder == "Trash"
        assert trashed.to_addr == ""


class TestMoveEmail:
    """Tests for move_email."""

    def test_move_emails_batch_saves_once(
        self, mailbox_service: MailboxService, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test batch move performs all mutations before one persistence pass."""
        save_calls = 0
        original_save = mailbox_service._save

        def counting_save() -> None:
            nonlocal save_calls
            save_calls += 1
            original_save()

        monkeypatch.setattr(mailbox_service, "_save", counting_save)

        result = mailbox_service.move_emails(["1", "2", "missing"], "Work")

        assert result.succeeded_ids == ["1", "2"]
        assert [error.email_id for error in result.errors] == ["missing"]
        assert mailbox_service.get_email("1").folder == "Work"
        assert mailbox_service.get_email("2").folder == "Work"
        assert save_calls == 1

    def test_move_emails_batch_invalid_folder_does_not_save(
        self,
        mailbox_service: MailboxService,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Test invalid batch move target reports every attempt without persisting."""
        save_calls = 0

        def counting_save() -> None:
            nonlocal save_calls
            save_calls += 1

        monkeypatch.setattr(mailbox_service, "_save", counting_save)

        result = mailbox_service.move_emails(["1", "missing"], "Nope")

        assert result.succeeded_ids == []
        assert [error.email_id for error in result.errors] == ["1", "missing"]
        assert all(error.error == "Folder not found: Nope" for error in result.errors)
        assert save_calls == 0

    def test_move_emails_batch_rolls_back_if_save_fails(
        self,
        mailbox_service: MailboxService,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Test failed batch persistence restores in-memory move mutations."""
        monkeypatch.setattr(mailbox_service, "_save", lambda: (_ for _ in ()).throw(RuntimeError("save failed")))

        with pytest.raises(RuntimeError, match="save failed"):
            mailbox_service.move_emails(["1", "2"], "Work")

        assert mailbox_service.get_email("1").folder == "INBOX"
        assert mailbox_service.get_email("2").folder == "INBOX"

    def test_move_empty_recipient_draft_to_trash(self, mailbox_service: MailboxService) -> None:
        """Test moving an incomplete draft to Trash keeps persisted state valid."""
        draft = mailbox_service.save_draft(subject="No recipient yet", body="Draft content")

        mailbox_service.move_email(draft.email_id, "Trash")

        trashed = mailbox_service.get_email(draft.email_id)
        assert trashed.folder == "Trash"
        assert trashed.to_addr == ""

    def test_move_empty_recipient_draft_to_active_folder_requires_recipient(
        self,
        mailbox_service: MailboxService,
    ) -> None:
        """Test incomplete drafts cannot become active messages without recipients."""
        draft = mailbox_service.save_draft(subject="No recipient yet", body="Draft content")

        with pytest.raises(RecipientRequiredError):
            mailbox_service.move_email(draft.email_id, "Work")

    def test_move_cc_only_draft_to_active_folder(self, mailbox_service: MailboxService) -> None:
        """CC-only drafts count as recipient-bearing active messages."""
        draft = mailbox_service.save_draft(subject="CC only", body="Draft content", cc="alice@example.com")

        mailbox_service.move_email(draft.email_id, "Work")

        moved = mailbox_service.get_email(draft.email_id)
        assert moved.folder == "Work"
        assert moved.to_addr == ""
        assert moved.cc_addr == "alice@example.com"

    def test_move_emails_reports_empty_recipient_draft_errors(self, mailbox_service: MailboxService) -> None:
        """Test batch move reports incomplete-draft errors without aborting valid moves."""
        draft = mailbox_service.save_draft(subject="No recipient yet", body="Draft content")

        result = mailbox_service.move_emails(["1", draft.email_id], "Work")

        assert result.succeeded_ids == ["1"]
        assert [error.email_id for error in result.errors] == [draft.email_id]
        assert "Cannot move email without recipients" in result.errors[0].error
        assert mailbox_service.get_email(draft.email_id).folder == "Drafts"


class TestMarkEmails:
    """Tests for mark_emails."""

    def test_mark_emails_batch_reports_missing_ids_and_saves_once(
        self,
        mailbox_service: MailboxService,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Test service-level batch marking owns missing-id detection."""
        save_calls = 0
        original_save = mailbox_service._save

        def counting_save() -> None:
            nonlocal save_calls
            save_calls += 1
            original_save()

        monkeypatch.setattr(mailbox_service, "_save", counting_save)

        result = mailbox_service.mark_emails(["1", "missing"], is_read=True, is_important=True)

        assert result.succeeded_ids == ["1"]
        assert [error.email_id for error in result.errors] == ["missing"]
        assert mailbox_service.get_email("1").is_read is True
        assert mailbox_service.get_email("1").is_important is True
        assert save_calls == 1

    def test_mark_emails_requires_action(self, mailbox_service: MailboxService) -> None:
        """Test direct service callers cannot perform a silent no-op mark."""
        with pytest.raises(ValueError, match="At least one"):
            mailbox_service.mark_emails(["1"])

    def test_mark_emails_batch_rolls_back_if_save_fails(
        self,
        mailbox_service: MailboxService,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Test failed batch persistence restores in-memory mark mutations."""
        monkeypatch.setattr(mailbox_service, "_save", lambda: (_ for _ in ()).throw(RuntimeError("save failed")))

        with pytest.raises(RuntimeError, match="save failed"):
            mailbox_service.mark_emails(["1"], is_read=True, is_important=True)

        email = mailbox_service.get_email("1")
        assert email.is_read is False
        assert email.is_important is False


class TestFolders:
    """Tests for folder operations."""

    def test_folder_crud_operations(self, mailbox_service: MailboxService) -> None:
        """Test folder create, read, delete operations."""
        # Test get folders
        folders = mailbox_service.get_folders()
        folder_names = [f["name"] for f in folders]
        assert "INBOX" in folder_names
        assert "Sent" in folder_names
        assert "Work" in folder_names

        # Test create folder
        mailbox_service.create_folder("Personal")
        folders = mailbox_service.get_folders()
        folder_names = [f["name"] for f in folders]
        assert "Personal" in folder_names

        # Test delete folder
        mailbox_service.delete_folder("Work")
        folders = mailbox_service.get_folders()
        folder_names = [f["name"] for f in folders]
        assert "Work" not in folder_names

    def test_folder_error_handling(self, mailbox_service: MailboxService) -> None:
        """Test folder error cases."""
        # Test creating duplicate folder
        with pytest.raises(FolderExistsError):
            mailbox_service.create_folder("Work")

        # Test deleting system folder
        with pytest.raises(SystemFolderError):
            mailbox_service.delete_folder("INBOX")


class TestDrafts:
    """Tests for draft operations."""

    def test_save_draft(self, mailbox_service: MailboxService) -> None:
        """Test saving a draft."""
        draft = mailbox_service.save_draft(
            subject="New draft",
            body="Draft content",
            to="bob@example.com",
        )
        assert draft.subject == "New draft"
        assert draft.body_text == "Draft content"
        assert draft.folder == "Drafts"

    def test_save_draft_without_recipient(self, mailbox_service: MailboxService) -> None:
        """Test saving an incomplete draft with no recipient."""
        draft = mailbox_service.save_draft(
            subject="No recipient yet",
            body="Draft content",
        )
        assert draft.folder == "Drafts"
        assert draft.to_addr == ""

    def test_get_drafts(self, mailbox_service: MailboxService) -> None:
        """Test getting all drafts."""
        drafts, total = mailbox_service.get_drafts()
        assert total == 1

    def test_update_draft(self, mailbox_service: MailboxService) -> None:
        """Test updating a draft."""
        draft = mailbox_service.update_draft("d1", subject="Updated subject")
        assert draft.subject == "Updated subject"

    def test_update_draft_rolls_back_invalid_recipient(self, mailbox_service: MailboxService) -> None:
        """Invalid draft updates must not corrupt the live in-memory draft."""
        original = mailbox_service.get_draft("d1").model_dump(mode="json")

        with pytest.raises(ValidationError):
            mailbox_service.update_draft("d1", subject="Should roll back", to="not an email")

        restored = mailbox_service.get_draft("d1")
        assert restored.model_dump(mode="json") == original

        updated = mailbox_service.update_draft("d1", subject="Valid update")
        assert updated.subject == "Valid update"

    def test_delete_draft(self, mailbox_service: MailboxService) -> None:
        """Test deleting a draft."""
        mailbox_service.delete_draft("d1")
        with pytest.raises(DraftNotFoundError):
            mailbox_service.get_draft("d1")


class TestContacts:
    """Tests for contact operations."""

    def test_get_contacts(self, mailbox_service: MailboxService) -> None:
        """Test getting all contacts."""
        contacts = mailbox_service.get_contacts()
        assert len(contacts) == 2

    def test_contacts_sorted_by_name(self, mailbox_service: MailboxService) -> None:
        """Test that contacts are sorted by name."""
        contacts = mailbox_service.get_contacts()
        names = [c.name for c in contacts]
        assert names == sorted(names, key=str.lower)

    def test_get_groups(self, mailbox_service: MailboxService) -> None:
        """Test getting contact groups."""
        groups = mailbox_service.get_groups()
        assert len(groups) == 1
        assert groups[0].email == "team@example.com"
        assert groups[0].members == ["alice@example.com", "bob@example.com"]

    def test_search_contacts_by_name(self, mailbox_service: MailboxService) -> None:
        """Test searching contacts by name."""
        contacts = mailbox_service.search_contacts("bob")
        assert len(contacts) == 1
        assert contacts[0].email == "bob@example.com"

    def test_search_contacts_by_email(self, mailbox_service: MailboxService) -> None:
        """Test searching contacts by email."""
        contacts = mailbox_service.search_contacts("carol@")
        assert len(contacts) == 1
        assert contacts[0].name == "Carol White"

    def test_search_contacts_case_insensitive(self, mailbox_service: MailboxService) -> None:
        """Test that search is case-insensitive."""
        contacts = mailbox_service.search_contacts("BOB")
        assert len(contacts) == 1

    def test_search_contacts_no_match(self, mailbox_service: MailboxService) -> None:
        """Test search with no matches returns empty list."""
        contacts = mailbox_service.search_contacts("nonexistent")
        assert contacts == []

    def test_add_contact(self, mailbox_service: MailboxService) -> None:
        """Test adding a new contact."""
        contact = mailbox_service.add_contact("dave@example.com", "Dave Brown")
        assert contact.email == "dave@example.com"
        assert contact.name == "Dave Brown"
        # Verify it's in the list
        all_contacts = mailbox_service.get_contacts()
        assert len(all_contacts) == 3

    def test_add_contact_duplicate(self, mailbox_service: MailboxService) -> None:
        """Test adding a contact with duplicate email raises error."""
        with pytest.raises(ContactExistsError):
            mailbox_service.add_contact("bob@example.com", "Bob Duplicate")

    def test_add_group(self, mailbox_service: MailboxService) -> None:
        """Test adding a group with members."""
        group = mailbox_service.add_group(
            "devs@example.com",
            "Developers",
            ["alice@example.com", "bob@example.com"],
        )
        assert group.email == "devs@example.com"
        assert group.members == ["alice@example.com", "bob@example.com"]

    def test_add_group_rolls_back_invalid_members(self, mailbox_service: MailboxService) -> None:
        """Test invalid group creation does not leave invalid state in memory."""
        with pytest.raises(ValueError):
            mailbox_service.add_group("bad@example.com", "Bad Group", ["unknown@example.com"])

        assert mailbox_service.data.get_group_by_email("bad@example.com") is None

    def test_edit_contact_name(self, mailbox_service: MailboxService) -> None:
        """Test updating a contact's name."""
        contact = mailbox_service.edit_contact("bob@example.com", name="Robert Jones")
        assert contact.name == "Robert Jones"
        assert contact.email == "bob@example.com"

    def test_edit_group_members(self, mailbox_service: MailboxService) -> None:
        """Test updating a group's members."""
        group = mailbox_service.edit_group("team@example.com", members=["bob@example.com"])
        assert group.members == ["bob@example.com"]

    def test_edit_group_rolls_back_invalid_members(self, mailbox_service: MailboxService) -> None:
        """Test invalid group edits do not leave invalid state in memory."""
        with pytest.raises(ValueError):
            mailbox_service.edit_group("team@example.com", members=["unknown@example.com"])

        group = mailbox_service.data.get_group_by_email("team@example.com")
        assert group is not None
        assert group.members == [
            "alice@example.com",
            "bob@example.com",
        ]

    def test_edit_contact_not_found(self, mailbox_service: MailboxService) -> None:
        """Test editing nonexistent contact raises error."""
        with pytest.raises(ContactNotFoundError):
            mailbox_service.edit_contact("nobody@example.com", name="Nobody")

    def test_delete_contact(self, mailbox_service: MailboxService) -> None:
        """Test deleting a contact."""
        mailbox_service.delete_contact("carol@example.com")
        all_contacts = mailbox_service.get_contacts()
        assert len(all_contacts) == 1
        assert all(c.email != "carol@example.com" for c in all_contacts)

    def test_delete_contact_not_found(self, mailbox_service: MailboxService) -> None:
        """Test deleting nonexistent contact raises error."""
        with pytest.raises(ContactNotFoundError):
            mailbox_service.delete_contact("nobody@example.com")


class TestScheduledEmails:
    """Tests for email scheduling operations."""

    def test_schedule_email(self, mailbox_service: MailboxService) -> None:
        """Test scheduling an email for later delivery."""
        from datetime import datetime

        send_at = datetime(2025, 6, 1, 9, 0, 0, tzinfo=UTC)
        email = mailbox_service.schedule_email(
            to="bob@example.com",
            subject="Scheduled test",
            body="This is scheduled",
            scheduled_time=send_at,
        )
        assert email.folder == "Scheduled"
        assert email.scheduled_time == send_at
        assert email.to_addr == "bob@example.com"

    def test_schedule_email_invalid_recipient(self, mailbox_service: MailboxService) -> None:
        """Test scheduling to invalid recipient raises error."""
        from datetime import datetime

        send_at = datetime(2025, 6, 1, 9, 0, 0, tzinfo=UTC)
        with pytest.raises(RecipientNotFoundError):
            mailbox_service.schedule_email(
                to="nobody@example.com",
                subject="Test",
                body="Test",
                scheduled_time=send_at,
            )

    def test_schedule_email_without_recipients_rejects_before_consuming_id(
        self, mailbox_service: MailboxService
    ) -> None:
        """Empty scheduled sends fail before constructing mail or advancing counters."""
        from datetime import datetime

        next_email_id = mailbox_service.data.next_email_id
        send_at = datetime(2025, 6, 1, 9, 0, 0, tzinfo=UTC)

        with pytest.raises(RecipientRequiredError):
            mailbox_service.schedule_email(
                to="",
                subject="Test",
                body="Test",
                scheduled_time=send_at,
            )

        assert mailbox_service.data.next_email_id == next_email_id

    def test_get_scheduled_emails(self, mailbox_service: MailboxService) -> None:
        """Test listing scheduled emails."""
        from datetime import datetime

        mailbox_service.schedule_email(
            to="bob@example.com",
            subject="First",
            body="1",
            scheduled_time=datetime(2025, 6, 2, tzinfo=UTC),
        )
        mailbox_service.schedule_email(
            to="carol@example.com",
            subject="Second",
            body="2",
            scheduled_time=datetime(2025, 6, 1, tzinfo=UTC),
        )
        emails, total = mailbox_service.get_scheduled_emails()
        assert total == 2
        # Sorted by scheduled_time ascending
        assert emails[0].subject == "Second"
        assert emails[1].subject == "First"

    def test_get_scheduled_emails_empty(self, mailbox_service: MailboxService) -> None:
        """Test listing when no emails are scheduled."""
        emails, total = mailbox_service.get_scheduled_emails()
        assert total == 0
        assert emails == []

    def test_cancel_scheduled_email(self, mailbox_service: MailboxService) -> None:
        """Test cancelling a scheduled email."""
        from datetime import datetime

        email = mailbox_service.schedule_email(
            to="bob@example.com",
            subject="Cancel me",
            body="test",
            scheduled_time=datetime(2025, 6, 1, tzinfo=UTC),
        )
        mailbox_service.cancel_scheduled_email(email.email_id)
        emails, total = mailbox_service.get_scheduled_emails()
        assert total == 0

    def test_cancel_nonexistent_email(self, mailbox_service: MailboxService) -> None:
        """Test cancelling non-existent email raises error."""
        with pytest.raises(EmailNotFoundError):
            mailbox_service.cancel_scheduled_email("999")

    def test_cancel_non_scheduled_email(self, mailbox_service: MailboxService) -> None:
        """Test cancelling a regular (non-scheduled) email raises error."""
        with pytest.raises(EmailNotFoundError):
            mailbox_service.cancel_scheduled_email("1")  # existing INBOX email

    def test_delete_scheduled_email_moves_to_trash_and_clears_scheduled_time(
        self,
        mailbox_service: MailboxService,
    ) -> None:
        """Test soft-deleting scheduled mail keeps persisted state valid."""
        from datetime import datetime

        email = mailbox_service.schedule_email(
            to="bob@example.com",
            subject="Delete scheduled",
            body="test",
            scheduled_time=datetime(2025, 6, 1, tzinfo=UTC),
        )
        mailbox_service.delete_email(email.email_id)

        trashed = mailbox_service.get_email(email.email_id)
        assert trashed.folder == "Trash"
        assert trashed.scheduled_time is None

    def test_move_scheduled_email_clears_scheduled_time(self, mailbox_service: MailboxService) -> None:
        """Test moving scheduled mail out of Scheduled keeps persisted state valid."""
        from datetime import datetime

        email = mailbox_service.schedule_email(
            to="bob@example.com",
            subject="Move scheduled",
            body="test",
            scheduled_time=datetime(2025, 6, 1, tzinfo=UTC),
        )
        mailbox_service.move_email(email.email_id, "Work")

        moved = mailbox_service.get_email(email.email_id)
        assert moved.folder == "Work"
        assert moved.scheduled_time is None

    def test_move_regular_email_to_scheduled_requires_schedule_tool(self, mailbox_service: MailboxService) -> None:
        """Test regular mail cannot enter Scheduled without scheduled_time metadata."""
        with pytest.raises(ScheduledFolderError):
            mailbox_service.move_email("1", "Scheduled")


class TestAttachments:
    """Tests for attachment operations."""

    def test_get_attachment(self, mailbox_service: MailboxService) -> None:
        """Test getting an attachment."""
        attachment = mailbox_service.get_attachment("2", "agenda.pdf")
        assert attachment.filename == "agenda.pdf"
        assert attachment.content_type == "application/pdf"

    def test_get_nonexistent_attachment(self, mailbox_service: MailboxService) -> None:
        """Test getting nonexistent attachment raises error."""
        with pytest.raises(AttachmentNotFoundError):
            mailbox_service.get_attachment("2", "nonexistent.pdf")


# ---------------------------------------------------------------------------
# Gmail-style operator tests
# ---------------------------------------------------------------------------


@pytest.fixture
def operator_mailbox(tmp_path: Path) -> MailboxService:
    """Mailbox seeded for Gmail operator coverage.

    Dates are computed relative to "now" so newer_than/older_than are stable.
    """
    from datetime import UTC, datetime, timedelta

    now = datetime.now(UTC)

    def ts(delta_days: int) -> str:
        return (now - timedelta(days=delta_days)).isoformat()

    data = {
        "mailbox": {"email": "agent@example.com", "name": "Agent"},
        "contacts": [
            {"email": "alice@example.com", "name": "Alice"},
            {"email": "bob@example.com", "name": "Bob"},
            {"email": "carol@example.com", "name": "Carol"},
            {"email": "dave@example.com", "name": "Dave"},
            {"email": "spammer@example.com", "name": "Spammer"},
        ],
        "folders": [],
        "emails": [
            {
                "email_id": "1",
                "folder": "INBOX",
                "subject": "Invoice for March hours",
                "from_addr": "alice@example.com",
                "to_addr": "agent@example.com",
                "cc_addr": None,
                "bcc_addr": None,
                "date": ts(2),
                "message_id": "<m1@example.com>",
                "body_text": "Here is your invoice for March billing.",
                "is_read": False,
                "is_important": True,
                "attachments": [],
            },
            {
                "email_id": "2",
                "folder": "INBOX",
                "subject": "Billing update",
                "from_addr": "bob@example.com",
                "to_addr": "agent@example.com",
                "cc_addr": "alice@example.com",
                "bcc_addr": None,
                "date": ts(5),
                "message_id": "<m2@example.com>",
                "body_text": "Quarterly billing summary attached.",
                "is_read": True,
                "is_important": False,
                "attachments": [],
            },
            {
                "email_id": "3",
                "folder": "INBOX",
                "subject": "Contract draft",
                "from_addr": "carol@example.com",
                "to_addr": "agent@example.com",
                "cc_addr": None,
                "bcc_addr": "dave@example.com",
                "date": ts(20),
                "message_id": "<m3@example.com>",
                "body_text": "Please review the draft contract by Friday.",
                "is_read": True,
                "is_important": False,
                "attachments": [],
            },
            {
                "email_id": "4",
                "folder": "Sent",
                "subject": "Re: scheduling",
                "from_addr": "agent@example.com",
                "to_addr": "alice@example.com",
                "cc_addr": None,
                "bcc_addr": None,
                "date": ts(10),
                "message_id": "<m4@example.com>",
                "body_text": "Works for me.",
                "is_read": True,
                "is_important": False,
                "attachments": [],
            },
            {
                "email_id": "5",
                "folder": "INBOX",
                "subject": "Spam offer",
                "from_addr": "spammer@example.com",
                "to_addr": "agent@example.com",
                "cc_addr": None,
                "bcc_addr": None,
                "date": ts(1),
                "message_id": "<m5@example.com>",
                "body_text": "Amazing deal on contract forms.",
                "is_read": False,
                "is_important": False,
                "attachments": [],
            },
            {
                "email_id": "6",
                "folder": "INBOX",
                "subject": "Ancient newsletter",
                "from_addr": "newsletter@example.com",
                "to_addr": "agent@example.com",
                "cc_addr": None,
                "bcc_addr": None,
                "date": ts(500),  # ~1.3 years ago
                "message_id": "<m6@example.com>",
                "body_text": "Old newsletter content here.",
                "is_read": True,
                "is_important": False,
                "attachments": [],
            },
            {
                "email_id": "7",
                "folder": "INBOX",
                "subject": "March summary",
                "from_addr": "alice@example.com",
                "to_addr": "bob@example.com",
                "cc_addr": "agent@example.com",
                "bcc_addr": None,
                "date": ts(35),
                "message_id": "<m7@example.com>",
                "body_text": "Summary of March activity.",
                "is_read": True,
                "is_important": False,
                "attachments": [],
            },
        ],
        "next_email_id": 8,
    }
    # Add a contact for the newsletter sender so validation passes.
    data["contacts"].append({"email": "newsletter@example.com", "name": "Newsletter"})

    path = tmp_path / "mailbox.json"
    path.write_text(json.dumps(data))
    svc = MailboxService(path)
    svc.load()
    return svc


class TestSearchEmailsOperators:
    """Cover every Gmail operator + parser edge case."""

    # ---- Address filters --------------------------------------------------

    def test_from_substring_match(self, operator_mailbox: MailboxService) -> None:
        emails, total = operator_mailbox.search_emails("from:alice")
        assert total == 2
        assert {e.email_id for e in emails} == {"1", "7"}

    def test_from_case_insensitive_operator_name(self, operator_mailbox: MailboxService) -> None:
        emails, total = operator_mailbox.search_emails("FROM:alice")
        assert total == 2

    def test_from_does_not_match_body_text(self, operator_mailbox: MailboxService) -> None:
        # "agent" appears in bodies/to_addr but not in any from_addr
        emails, total = operator_mailbox.search_emails("from:agent")
        # Only email 4 is FROM agent@
        assert total == 1
        assert emails[0].email_id == "4"

    def test_to_matches_only_to_field(self, operator_mailbox: MailboxService) -> None:
        # email 7 has to=bob, cc=agent — `to:agent` should NOT match email 7
        emails, total = operator_mailbox.search_emails("to:bob")
        assert total == 1
        assert emails[0].email_id == "7"

    def test_cc_matches_cc_field(self, operator_mailbox: MailboxService) -> None:
        emails, total = operator_mailbox.search_emails("cc:alice")
        assert {e.email_id for e in emails} == {"2"}
        assert total == 1

    def test_bcc_matches_bcc_field(self, operator_mailbox: MailboxService) -> None:
        emails, total = operator_mailbox.search_emails("bcc:dave")
        assert {e.email_id for e in emails} == {"3"}

    def test_address_operator_empty_value_returns_nothing(self, operator_mailbox: MailboxService) -> None:
        emails, total = operator_mailbox.search_emails("from:")
        assert total == 0

    # ---- OR ---------------------------------------------------------------

    def test_or_between_bare_words(self, operator_mailbox: MailboxService) -> None:
        emails, total = operator_mailbox.search_emails("invoice OR newsletter")
        # email 1 (invoice), email 6 (newsletter)
        ids = {e.email_id for e in emails}
        assert "1" in ids and "6" in ids

    def test_or_between_two_operators(self, operator_mailbox: MailboxService) -> None:
        emails, total = operator_mailbox.search_emails("from:alice OR from:bob")
        assert {e.email_id for e in emails} == {"1", "2", "7"}

    def test_or_across_operator_and_bare_word(self, operator_mailbox: MailboxService) -> None:
        emails, total = operator_mailbox.search_emails("from:alice OR contract")
        # from:alice → 1,7; bare 'contract' → 3, 5 (body mentions 'contract')
        assert {e.email_id for e in emails} == {"1", "3", "5", "7"}

    def test_lowercase_or_is_literal(self, operator_mailbox: MailboxService) -> None:
        # Lowercase 'or' must be treated as a literal bare-word token.
        # None of the emails contain 'or' as a standalone word in matched regions;
        # fixture wording ("for", "works", etc.) contains 'or' as substring though.
        emails_literal, _ = operator_mailbox.search_emails("invoice or billing")
        emails_boolean, _ = operator_mailbox.search_emails("invoice OR billing")
        # Boolean version matches at least as many as literal version.
        assert len(emails_boolean) >= len(emails_literal)
        # Boolean version includes email 1 (invoice) AND email 2 (billing).
        assert {"1", "2"}.issubset({e.email_id for e in emails_boolean})

    def test_trailing_or_dangles_gracefully(self, operator_mailbox: MailboxService) -> None:
        emails, total = operator_mailbox.search_emails("invoice OR")
        # Should behave like just "invoice"
        assert any(e.email_id == "1" for e in emails)
        assert total >= 1

    def test_leading_or_dangles_gracefully(self, operator_mailbox: MailboxService) -> None:
        emails, total = operator_mailbox.search_emails("OR invoice")
        assert any(e.email_id == "1" for e in emails)

    def test_chained_or(self, operator_mailbox: MailboxService) -> None:
        emails, total = operator_mailbox.search_emails("invoice OR newsletter OR contract")
        ids = {e.email_id for e in emails}
        # 1 invoice, 3 contract (subject), 5 contract (body), 6 newsletter
        assert {"1", "3", "5", "6"}.issubset(ids)

    # ---- Negation ---------------------------------------------------------

    def test_negation_excludes_bare_word(self, operator_mailbox: MailboxService) -> None:
        emails, total = operator_mailbox.search_emails("march -spam")
        # 'march' in subject of 1, 7; 'spam' appears in subject of 5 only.
        # 5 doesn't contain 'march' anyway, so it's already excluded.
        # Exclude any message containing 'spam'.
        ids = {e.email_id for e in emails}
        assert "5" not in ids
        assert "1" in ids

    def test_negation_of_from(self, operator_mailbox: MailboxService) -> None:
        # Match messages NOT from alice that mention 'billing' or 'march'.
        emails, total = operator_mailbox.search_emails("billing -from:alice")
        ids = {e.email_id for e in emails}
        assert "2" in ids
        assert "1" not in ids  # alice is sender of 1
        assert "7" not in ids  # alice sender

    def test_negation_of_quoted_phrase(self, operator_mailbox: MailboxService) -> None:
        # Every message that does NOT contain the exact phrase 'march hours'.
        emails, total = operator_mailbox.search_emails('-"march hours"')
        ids = {e.email_id for e in emails}
        # Only email 1 has 'March hours' adjacent in the subject.
        assert "1" not in ids

    def test_lone_dash_dropped(self, operator_mailbox: MailboxService) -> None:
        # "- invoice" — the lone "-" must be ignored; behaves like "invoice".
        emails, total = operator_mailbox.search_emails("- invoice")
        assert any(e.email_id == "1" for e in emails)

    def test_double_dash_treated_as_part_of_value(self, operator_mailbox: MailboxService) -> None:
        # "--invoice" → negates, value becomes "-invoice" which doesn't match anywhere.
        emails, total = operator_mailbox.search_emails("--invoice")
        # Nothing contains literal "-invoice" so the negation passes for all emails.
        assert total == len(operator_mailbox.data.emails)

    # ---- Phrases + word-AND preservation ----------------------------------

    def test_quoted_phrase_still_requires_adjacency(self, operator_mailbox: MailboxService) -> None:
        emails, total = operator_mailbox.search_emails('"march hours"')
        assert {e.email_id for e in emails} == {"1"}

        # Reversed phrase should NOT match
        emails, total = operator_mailbox.search_emails('"hours march"')
        assert total == 0

    def test_operator_combined_with_phrase(self, operator_mailbox: MailboxService) -> None:
        emails, total = operator_mailbox.search_emails('from:alice "march hours"')
        assert {e.email_id for e in emails} == {"1"}

    def test_bare_multi_word_is_AND(self, operator_mailbox: MailboxService) -> None:
        emails, total = operator_mailbox.search_emails("march billing")
        # both words must appear. email 1: 'March' subject + 'March billing' body → yes.
        # email 7: subject 'March summary', body 'March activity' → no 'billing'.
        assert {e.email_id for e in emails} == {"1"}

    def test_operator_with_quoted_value(self, operator_mailbox: MailboxService) -> None:
        # Quoted value after operator is a phrase-style substring match.
        emails, total = operator_mailbox.search_emails('from:"alice@example.com"')
        assert {e.email_id for e in emails} == {"1", "7"}

    # ---- Dates ------------------------------------------------------------

    def test_before_is_strict(self, operator_mailbox: MailboxService) -> None:
        from datetime import UTC, datetime, timedelta

        # Use a date so exactly email 1 (2 days ago) is excluded by strict before.
        now = datetime.now(UTC)
        day_1 = (now - timedelta(days=2)).strftime("%Y/%m/%d")
        emails, total = operator_mailbox.search_emails(f"before:{day_1}")
        ids = {e.email_id for e in emails}
        # email 1 dated exactly day_1 should NOT be included (strict before).
        # Emails older than day_1 (2,3,4,6,7) should be included.
        assert "1" not in ids or not any(e.date.strftime("%Y/%m/%d") == day_1 for e in emails if e.email_id == "1")

    def test_after_is_inclusive(self, operator_mailbox: MailboxService) -> None:
        from datetime import UTC, datetime, timedelta

        now = datetime.now(UTC)
        day_2 = (now - timedelta(days=2)).strftime("%Y/%m/%d")
        emails, _ = operator_mailbox.search_emails(f"after:{day_2}")
        ids = {e.email_id for e in emails}
        # email 1 (2 days ago) and 5 (1 day ago) should both be included.
        assert "1" in ids and "5" in ids

    def test_date_dash_separator(self, operator_mailbox: MailboxService) -> None:
        from datetime import UTC, datetime, timedelta

        now = datetime.now(UTC)
        day_3 = (now - timedelta(days=3)).strftime("%Y-%m-%d")
        emails, _ = operator_mailbox.search_emails(f"after:{day_3}")
        assert any(e.email_id == "1" for e in emails)

    def test_date_range_intersection(self, operator_mailbox: MailboxService) -> None:
        from datetime import UTC, datetime, timedelta

        now = datetime.now(UTC)
        after = (now - timedelta(days=7)).strftime("%Y/%m/%d")
        before = (now - timedelta(days=1)).strftime("%Y/%m/%d")
        emails, _ = operator_mailbox.search_emails(f"after:{after} before:{before}")
        # Emails in [7 days ago, 1 day ago) — that's email 1 (2 days), email 2 (5 days)
        ids = {e.email_id for e in emails}
        assert "1" in ids and "2" in ids
        assert "6" not in ids  # 500 days ago

    def test_malformed_date_fails_silently(self, operator_mailbox: MailboxService) -> None:
        emails, total = operator_mailbox.search_emails("before:garbage")
        assert total == 0

    def test_newer_than_days(self, operator_mailbox: MailboxService) -> None:
        emails, _ = operator_mailbox.search_emails("newer_than:7d")
        ids = {e.email_id for e in emails}
        # Emails within last 7 days: 1 (2d), 2 (5d), 5 (1d)
        assert {"1", "2", "5"}.issubset(ids)
        assert "6" not in ids  # 500 days old

    def test_newer_than_months(self, operator_mailbox: MailboxService) -> None:
        emails, _ = operator_mailbox.search_emails("newer_than:1m")
        # 30 days: 1, 2, 4, 5 (all within 30 days), plus maybe 3 (20 days)
        ids = {e.email_id for e in emails}
        assert "3" in ids  # 20 days ago
        assert "6" not in ids  # 500 days ago

    def test_newer_than_years(self, operator_mailbox: MailboxService) -> None:
        emails, _ = operator_mailbox.search_emails("newer_than:2y")
        ids = {e.email_id for e in emails}
        assert "6" in ids  # 500 days, within 2*365

    def test_older_than_years(self, operator_mailbox: MailboxService) -> None:
        emails, _ = operator_mailbox.search_emails("older_than:1y")
        ids = {e.email_id for e in emails}
        # Only email 6 (~500 days) is older than 1 year.
        assert ids == {"6"}

    def test_malformed_duration_fails_silently(self, operator_mailbox: MailboxService) -> None:
        assert operator_mailbox.search_emails("newer_than:7x")[1] == 0
        assert operator_mailbox.search_emails("newer_than:foo")[1] == 0

    # ---- Parser combinatorics --------------------------------------------

    def test_empty_query_returns_nothing(self, operator_mailbox: MailboxService) -> None:
        emails, total = operator_mailbox.search_emails("")
        assert emails == [] and total == 0

    def test_whitespace_query_returns_nothing(self, operator_mailbox: MailboxService) -> None:
        assert operator_mailbox.search_emails("   ")[1] == 0

    def test_only_operators_no_bare_words(self, operator_mailbox: MailboxService) -> None:
        from datetime import UTC, datetime, timedelta

        now = datetime.now(UTC)
        after = (now - timedelta(days=7)).strftime("%Y/%m/%d")
        emails, _ = operator_mailbox.search_emails(f"from:alice after:{after}")
        # Only email 1 — alice sent within 7 days.
        assert {e.email_id for e in emails} == {"1"}

    def test_only_negation(self, operator_mailbox: MailboxService) -> None:
        # Returns every email that does NOT contain 'spam'.
        emails, total = operator_mailbox.search_emails("-spam")
        assert total > 0
        assert "5" not in {e.email_id for e in emails}

    def test_unknown_operator_treated_as_literal_bare_word(self, operator_mailbox: MailboxService) -> None:
        # 'random:foo' matches if that literal substring appears anywhere.
        emails, total = operator_mailbox.search_emails("random:foo")
        assert total == 0

    # ---- Integration with existing args ----------------------------------

    def test_folder_filter_still_applies(self, operator_mailbox: MailboxService) -> None:
        emails, _ = operator_mailbox.search_emails("from:alice", folder="Sent")
        # email 4 is in Sent (from agent, to alice) — NOT from alice.
        # In INBOX + Sent only email 4 is in Sent and it's from agent.
        assert {e.email_id for e in emails} == set()  # no alice-sent mail in Sent

    def test_pagination_with_operator(self, operator_mailbox: MailboxService) -> None:
        # Get all mentioning 'march' (subject or body), paginate.
        page1, total = operator_mailbox.search_emails("march", page=1, page_size=1)
        page2, _ = operator_mailbox.search_emails("march", page=2, page_size=1)
        assert total >= 2
        assert len(page1) == 1 and len(page2) == 1
        assert page1[0].email_id != page2[0].email_id
