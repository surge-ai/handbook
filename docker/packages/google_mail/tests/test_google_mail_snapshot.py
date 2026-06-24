"""Tests for the _snapshot_on_write decorator — final.json is written after every write tool call."""

import asyncio
import json

import pytest

from google_mail.services.mailbox import MailboxService


@pytest.fixture
def mailbox_service(tmp_path):
    """Create a MailboxService seeded with minimal data."""
    data_path = tmp_path / "mailbox.json"
    data_path.write_text(
        json.dumps(
            {
                "mailbox": {"email": "test@test.com", "name": "Test User"},
                "contacts": [{"email": "bob@test.com", "name": "Bob"}],
                "folders": [],
                "emails": [],
                "next_email_id": 1,
            }
        )
    )
    svc = MailboxService(data_path)
    svc.load()
    return svc


@pytest.fixture
def outputdir(tmp_path):
    """Return a temp directory for OUTPUTDIR, with a pre-configured final.json path."""
    out = tmp_path / "output" / "google_mail"
    out.mkdir(parents=True)
    return out


@pytest.fixture(autouse=True)
def _patch_server_globals(mailbox_service, outputdir):
    """Wire up state globals so tools and the decorator can run."""
    import google_mail.state as state

    state.set_mailboxes({"default": mailbox_service})
    state.set_snapshot_paths(final_path=outputdir / "final.json")
    yield
    state.set_mailboxes({})
    state.set_snapshot_paths(final_path=None, bundle_state_path=None)


def test_send_email_writes_final_json(outputdir):
    """Calling mail_send_email produces a final.json snapshot immediately."""
    from google_mail.server import mail_send_email

    final = outputdir / "final.json"
    assert not final.exists(), "final.json should not exist before any write"

    result = asyncio.run(
        mail_send_email(
            to="bob@test.com",
            subject="snapshot test",
            body="hello",
        )
    )
    data = json.loads(result)
    assert data.get("status") == "sent"
    assert final.exists(), "final.json must be written after mail_send_email"

    snapshot = json.loads(final.read_text())
    subjects = [e["subject"] for e in snapshot.get("emails", [])]
    assert "snapshot test" in subjects


def test_send_email_without_recipients_returns_error_without_consuming_id(mailbox_service):
    """Empty recipient sends return a structured error and leave counters untouched."""
    from google_mail.server import mail_send_email

    next_email_id = mailbox_service.data.next_email_id

    result = asyncio.run(mail_send_email(to="", subject="empty recipient", body="hello"))
    data = json.loads(result)

    assert data.get("status") == "bounced"
    assert data.get("error")
    assert mailbox_service.data.next_email_id == next_email_id


def test_save_draft_invalid_recipient_returns_error_without_consuming_id(mailbox_service):
    """Invalid draft recipients return structured errors instead of raising."""
    from google_mail.server import mail_save_draft

    next_email_id = mailbox_service.data.next_email_id

    result = asyncio.run(mail_save_draft(to="not an email", subject="bad draft", body="hello"))
    data = json.loads(result)

    assert data.get("status") == "failed"
    assert data.get("error")
    assert mailbox_service.data.next_email_id == next_email_id


def test_update_draft_invalid_recipient_returns_error_without_corrupting_draft(mailbox_service):
    """Invalid draft updates return structured errors and preserve the existing draft."""
    from google_mail.server import mail_save_draft, mail_update_draft

    created = json.loads(asyncio.run(mail_save_draft(to="bob@test.com", subject="draft", body="hello")))
    draft_id = created["draft"]["draft_id"]
    original = mailbox_service.get_draft(draft_id).model_dump(mode="json")

    result = asyncio.run(mail_update_draft(draft_id=draft_id, to="not an email", subject="bad update"))
    data = json.loads(result)

    assert data.get("status") == "failed"
    assert data.get("error")
    assert mailbox_service.get_draft(draft_id).model_dump(mode="json") == original


def test_delete_email_writes_final_json(outputdir):
    """Calling mail_delete_emails produces a final.json snapshot."""
    from google_mail.server import mail_delete_emails, mail_send_email

    # First, create an email to delete
    result = asyncio.run(mail_send_email(to="bob@test.com", subject="to-delete", body="bye"))
    sent = json.loads(result)
    email_id = sent["email"]["email_id"]

    # Remove existing final.json to isolate the delete's snapshot
    final = outputdir / "final.json"
    final.unlink()

    asyncio.run(mail_delete_emails([email_id]))

    assert final.exists(), "final.json must be written after mail_delete_emails"


def test_delete_emails_reports_partial_batch_status():
    from google_mail.server import mail_delete_emails, mail_send_email

    result = asyncio.run(mail_send_email(to="bob@test.com", subject="to-delete", body="bye"))
    email_id = json.loads(result)["email"]["email_id"]

    data = json.loads(asyncio.run(mail_delete_emails([email_id, "missing"])))

    assert data["status"] == "partial_success"
    assert data["summary"] == "1 of 2 attempts succeeded"
    assert data["requestedCount"] == 2
    assert data["succeededCount"] == 1
    assert data["failedCount"] == 1
    assert data["deletedIds"] == [email_id]
    assert data["errors"][0]["email_id"] == "missing"


def test_delete_emails_reports_all_failed_batch_status():
    from google_mail.server import mail_delete_emails

    data = json.loads(asyncio.run(mail_delete_emails(["missing-1", "missing-2"])))

    assert data["status"] == "all_failed"
    assert data["summary"] == "All 2 attempts failed"
    assert data["requestedCount"] == 2
    assert data["succeededCount"] == 0
    assert data["failedCount"] == 2


def test_mark_emails_requires_an_action_flag():
    from google_mail.server import mail_mark_emails, mail_send_email

    result = asyncio.run(mail_send_email(to="bob@test.com", subject="to-mark", body="hi"))
    email_id = json.loads(result)["email"]["email_id"]

    data = json.loads(asyncio.run(mail_mark_emails([email_id])))

    assert data["status"] == "failed"
    assert "At least one" in data["error"]


def test_mark_emails_reports_partial_batch_status():
    from google_mail.server import mail_mark_emails, mail_send_email

    result = asyncio.run(mail_send_email(to="bob@test.com", subject="to-mark", body="hi"))
    email_id = json.loads(result)["email"]["email_id"]

    data = json.loads(asyncio.run(mail_mark_emails([email_id, "missing"], is_read=False)))

    assert data["status"] == "partial_success"
    assert data["summary"] == "1 of 2 attempts succeeded"
    assert data["markedCount"] == 1
    assert data["markedIds"] == [email_id]
    assert data["errors"][0]["email_id"] == "missing"


def test_create_folder_writes_final_json(outputdir):
    """Calling mail_create_folder produces a final.json snapshot."""
    from google_mail.server import mail_create_folder

    final = outputdir / "final.json"
    asyncio.run(mail_create_folder("TestFolder"))

    assert final.exists(), "final.json must be written after mail_create_folder"
    snapshot = json.loads(final.read_text())
    folder_names = [f["name"] for f in snapshot.get("folders", [])]
    assert "TestFolder" in folder_names


def test_read_only_tool_does_not_write_final_json(outputdir):
    """Calling a read-only tool (mail_get_emails) does NOT write final.json."""
    from google_mail.server import mail_get_emails

    final = outputdir / "final.json"
    asyncio.run(mail_get_emails(folder="INBOX"))

    assert not final.exists(), "final.json must NOT be written after a read-only tool"


def test_final_json_updates_incrementally(outputdir):
    """Each write tool call overwrites final.json with the latest state."""
    from google_mail.server import mail_send_email

    final = outputdir / "final.json"

    asyncio.run(mail_send_email(to="bob@test.com", subject="first", body="1"))
    snap1 = json.loads(final.read_text())
    count1 = len(snap1.get("emails", []))

    asyncio.run(mail_send_email(to="bob@test.com", subject="second", body="2"))
    snap2 = json.loads(final.read_text())
    count2 = len(snap2.get("emails", []))

    assert count2 > count1, "final.json should reflect each incremental mutation"


def test_add_contact_writes_final_json(outputdir):
    """Calling mail_add_contact produces a final.json snapshot."""
    from google_mail.server import mail_add_contact

    final = outputdir / "final.json"
    assert not final.exists()

    result = asyncio.run(mail_add_contact(email="alice@test.com", name="Alice"))
    data = json.loads(result)
    assert data.get("status") == "created"
    assert final.exists(), "final.json must be written after mail_add_contact"


def test_schedule_email_writes_final_json(outputdir):
    """Calling mail_schedule_email produces a final.json snapshot."""
    from google_mail.server import mail_schedule_email

    final = outputdir / "final.json"
    assert not final.exists()

    result = asyncio.run(
        mail_schedule_email(
            to="bob@test.com",
            subject="scheduled test",
            body="hello",
            scheduled_time="2025-06-01T09:00:00Z",
        )
    )
    data = json.loads(result)
    assert data.get("status") == "scheduled"
    assert final.exists(), "final.json must be written after mail_schedule_email"


def test_schedule_email_without_recipients_returns_error_without_consuming_id(mailbox_service):
    """Empty scheduled sends return a structured error and leave counters untouched."""
    from google_mail.server import mail_schedule_email

    next_email_id = mailbox_service.data.next_email_id

    result = asyncio.run(
        mail_schedule_email(
            to="",
            subject="empty recipient",
            body="hello",
            scheduled_time="2025-06-01T09:00:00Z",
        )
    )
    data = json.loads(result)

    assert data.get("status") == "failed"
    assert data.get("error")
    assert mailbox_service.data.next_email_id == next_email_id


def test_get_contacts_does_not_write_final_json(outputdir):
    """Calling a read-only contact tool does NOT write final.json."""
    from google_mail.server import mail_get_contacts

    final = outputdir / "final.json"
    asyncio.run(mail_get_contacts())
    assert not final.exists(), "final.json must NOT be written after a read-only tool"


def test_no_final_path_skips_snapshot():
    """When _final_path is None (no OUTPUTDIR), write tools still work without error."""
    import google_mail.state as state
    from google_mail.server import mail_send_email

    state.set_snapshot_paths(final_path=None, bundle_state_path=None)

    result = asyncio.run(mail_send_email(to="bob@test.com", subject="no-outputdir", body="ok"))
    data = json.loads(result)
    assert data.get("status") == "sent"


def test_dual_writes_bundle_path_and_final_json(outputdir, tmp_path):
    """Bundle migration: writable tools dual-write the bundle path (nested
    services/<name>/state.json layout) AND legacy final.json."""
    import google_mail.state as state
    from google_mail.server import mail_send_email

    bundle_output_dir = tmp_path / "services" / "google_mail"
    bundle_output_dir.mkdir(parents=True)
    bundle_path = bundle_output_dir / "state.json"
    final_path = outputdir / "final.json"
    state.set_snapshot_paths(final_path=final_path, bundle_state_path=bundle_path)

    asyncio.run(mail_send_email(to="bob@test.com", subject="dual-write", body="hi"))

    assert bundle_path.exists(), "<BUNDLE_OUTPUT_DIR>/state.json must be written"
    assert final_path.exists(), "legacy final.json must still be written"
    assert bundle_path.read_text() == final_path.read_text()


def test_set_snapshot_paths_partial_update_preserves_existing_path(tmp_path):
    import google_mail.state as state

    final_path = tmp_path / "final.json"
    bundle_path = tmp_path / "bundle.json"
    updated_final_path = tmp_path / "updated-final.json"

    state.set_snapshot_paths(final_path=final_path, bundle_state_path=bundle_path)
    state.set_snapshot_paths(final_path=updated_final_path)

    assert state.get_final_path() == updated_final_path
    assert state.get_bundle_state_path() == bundle_path


def test_set_snapshot_paths_can_clear_individual_path(tmp_path):
    import google_mail.state as state

    final_path = tmp_path / "final.json"
    bundle_path = tmp_path / "bundle.json"

    state.set_snapshot_paths(final_path=final_path, bundle_state_path=bundle_path)
    state.set_snapshot_paths(bundle_state_path=None)

    assert state.get_final_path() == final_path
    assert state.get_bundle_state_path() is None
