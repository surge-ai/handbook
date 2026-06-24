"""Tests for multi-mailbox support."""

import asyncio
import json

import pytest

from google_mail.models import MultiMailboxData
from google_mail.services.mailbox import MailboxService


@pytest.fixture
def multi_mailbox_services(tmp_path):
    """Create two mailbox services with different data."""
    work_path = tmp_path / "work.json"
    work_path.write_text(
        json.dumps(
            {
                "mailbox": {"email": "alex@acmecorp.com", "name": "Alex Morgan"},
                "contacts": [{"email": "pam@acmecorp.com", "name": "Pam Chen"}],
                "folders": [],
                "emails": [
                    {
                        "email_id": "1",
                        "folder": "INBOX",
                        "subject": "Work email",
                        "from_addr": "pam@acmecorp.com",
                        "to_addr": "alex@acmecorp.com",
                        "date": "2026-04-14T09:00:00Z",
                        "message_id": "<work1@acmecorp.com>",
                        "body_text": "This is a work email.",
                        "is_read": False,
                        "is_important": False,
                    }
                ],
                "next_email_id": 10,
            }
        )
    )
    personal_path = tmp_path / "personal.json"
    personal_path.write_text(
        json.dumps(
            {
                "mailbox": {"email": "alex.m@gmail.com", "name": "Alex"},
                "contacts": [{"email": "friend@gmail.com", "name": "Best Friend"}],
                "folders": [],
                "emails": [
                    {
                        "email_id": "1",
                        "folder": "INBOX",
                        "subject": "Personal email",
                        "from_addr": "friend@gmail.com",
                        "to_addr": "alex.m@gmail.com",
                        "date": "2026-04-14T10:00:00Z",
                        "message_id": "<personal1@gmail.com>",
                        "body_text": "Hey Alex! Want to grab lunch?",
                        "is_read": False,
                        "is_important": False,
                    }
                ],
                "next_email_id": 10,
            }
        )
    )

    work_svc = MailboxService(work_path)
    work_svc.load()
    personal_svc = MailboxService(personal_path)
    personal_svc.load()

    return {"work": work_svc, "personal": personal_svc}


@pytest.fixture
def outputdir(tmp_path):
    out = tmp_path / "output" / "google_mail"
    out.mkdir(parents=True)
    return out


@pytest.fixture(autouse=True)
def _patch_server_globals(multi_mailbox_services, outputdir):
    import google_mail.state as state

    state.set_mailboxes(multi_mailbox_services)
    state.set_snapshot_paths(final_path=outputdir / "final.json")
    yield
    state.set_mailboxes({})
    state.set_snapshot_paths(final_path=None, bundle_state_path=None)


class TestListMailboxes:
    def test_lists_all_mailboxes(self):
        from google_mail.server import mail_list_mailboxes

        result = json.loads(asyncio.run(mail_list_mailboxes()))
        assert result["total"] == 2
        ids = [m["mailbox_id"] for m in result["mailboxes"]]
        assert "work" in ids
        assert "personal" in ids

    def test_mailbox_info(self):
        from google_mail.server import mail_list_mailboxes

        result = json.loads(asyncio.run(mail_list_mailboxes()))
        work = next(m for m in result["mailboxes"] if m["mailbox_id"] == "work")
        assert work["email"] == "alex@acmecorp.com"
        assert work["email_count"] == 1
        assert work["contact_count"] == 1


class TestMailboxIsolation:
    def test_get_emails_from_work(self):
        from google_mail.server import mail_get_emails

        result = json.loads(asyncio.run(mail_get_emails(mailbox_id="work")))
        assert result["total"] == 1
        assert result["emails"][0]["subject"] == "Work email"

    def test_get_emails_from_personal(self):
        from google_mail.server import mail_get_emails

        result = json.loads(asyncio.run(mail_get_emails(mailbox_id="personal")))
        assert result["total"] == 1
        assert result["emails"][0]["subject"] == "Personal email"

    def test_contacts_are_isolated(self):
        from google_mail.server import mail_get_contacts

        work_result = json.loads(asyncio.run(mail_get_contacts(mailbox_id="work")))
        assert len(work_result["contacts"]) == 1
        assert work_result["contacts"][0]["name"] == "Pam Chen"

        personal_result = json.loads(asyncio.run(mail_get_contacts(mailbox_id="personal")))
        assert len(personal_result["contacts"]) == 1
        assert personal_result["contacts"][0]["name"] == "Best Friend"

    def test_send_email_in_correct_mailbox(self):
        from google_mail.server import mail_send_email

        result = json.loads(
            asyncio.run(
                mail_send_email(
                    to="pam@acmecorp.com",
                    subject="Test from work",
                    body="Sent from work mailbox",
                    mailbox_id="work",
                )
            )
        )
        assert result["status"] == "sent"
        assert result["email"]["from"] == "alex@acmecorp.com"

    def test_send_email_wrong_mailbox_contact(self):
        from google_mail.server import mail_send_email

        # pam@acmecorp.com is a work contact, not personal
        result = json.loads(
            asyncio.run(
                mail_send_email(
                    to="pam@acmecorp.com",
                    subject="Should fail",
                    body="Wrong mailbox",
                    mailbox_id="personal",
                )
            )
        )
        assert "error" in result  # recipient not found in personal contacts

    def test_invalid_mailbox_id(self):
        from google_mail.server import mail_get_emails

        with pytest.raises(ValueError, match="not found"):
            asyncio.run(mail_get_emails(mailbox_id="nonexistent"))

    def test_write_to_one_doesnt_affect_other(self):
        from google_mail.server import mail_create_folder, mail_get_folders

        # Create folder in work
        asyncio.run(mail_create_folder(folder_name="Projects", mailbox_id="work"))

        # Verify it's in work
        work_folders = json.loads(asyncio.run(mail_get_folders(mailbox_id="work")))
        folder_names = [f["name"] for f in work_folders["folders"]]
        assert "Projects" in folder_names

        # Verify it's NOT in personal
        personal_folders = json.loads(asyncio.run(mail_get_folders(mailbox_id="personal")))
        personal_names = [f["name"] for f in personal_folders["folders"]]
        assert "Projects" not in personal_names


class TestMultiMailboxSnapshot:
    def test_snapshot_includes_all_mailboxes(self, outputdir):
        from google_mail.server import mail_send_email

        final = outputdir / "final.json"

        asyncio.run(
            mail_send_email(
                to="pam@acmecorp.com",
                subject="Trigger snapshot",
                body="test",
                mailbox_id="work",
            )
        )

        assert final.exists()
        snapshot = json.loads(final.read_text())
        # Multi-mailbox format
        assert "mailboxes" in snapshot
        assert "work" in snapshot["mailboxes"]
        assert "personal" in snapshot["mailboxes"]


class TestMultiMailboxStateTools:
    def test_export_state_returns_typed_multi_mailbox_state(self):
        from google_mail.server import export_state

        state = asyncio.run(export_state())

        assert isinstance(state, MultiMailboxData)
        assert set(state.mailboxes) == {"work", "personal"}

    def test_import_state_accepts_multi_mailbox_dict(self):
        import google_mail.state as mail_state
        from google_mail.server import import_state, mail_list_mailboxes

        state = {
            "mailboxes": {
                "replacement": {
                    "mailbox": {"email": "replacement@example.com", "name": "Replacement"},
                    "contacts": [],
                    "folders": [],
                    "emails": [],
                    "next_email_id": 1,
                }
            }
        }

        result = asyncio.run(import_state(state))

        assert result == {"ok": True}
        assert set(mail_state.get_mailboxes()) == {"replacement"}
        listed = json.loads(asyncio.run(mail_list_mailboxes()))
        assert listed["mailboxes"][0]["mailbox_id"] == "replacement"

    def test_import_state_flat_payload_replaces_multi_mailbox_registry(self):
        import google_mail.state as mail_state
        from google_mail.server import import_state, mail_list_mailboxes

        state = {
            "mailbox": {"email": "replacement@example.com", "name": "Replacement"},
            "contacts": [],
            "folders": [],
            "emails": [],
            "next_email_id": 1,
        }

        result = asyncio.run(import_state(state))

        assert result == {"ok": True}
        assert set(mail_state.get_mailboxes()) == {"default"}
        listed = json.loads(asyncio.run(mail_list_mailboxes()))
        assert listed["total"] == 1
        assert listed["mailboxes"][0]["mailbox_id"] == "default"
        assert listed["mailboxes"][0]["email"] == "replacement@example.com"
