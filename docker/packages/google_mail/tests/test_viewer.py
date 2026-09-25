"""Regression tests for the google_mail viewer HTTP app.

These tests exercise the viewer routes against a real ``MailboxService`` loaded
from fixture data. The viewer reads from the shared ``google_mail.state``
mailbox registry so it uses the same state surface as the MCP server.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest
from starlette.testclient import TestClient

import google_mail.state as state_mod
from google_mail.services.mailbox import MailboxService
from google_mail.viewer import create_mail_viewer_app

SAMPLE_DATA = {
    "mailbox": {"email": "alice@example.com", "name": "Alice"},
    "contacts": [
        {"email": "bob@example.com", "name": "Bob"},
    ],
    "groups": [
        {
            "email": "team@example.com",
            "name": "Team",
            "members": ["alice@example.com", "bob@example.com"],
        },
    ],
    "folders": [],
    "emails": [
        {
            "email_id": "1",
            "folder": "INBOX",
            "subject": "Hello Alice",
            "from_addr": "bob@example.com",
            "to_addr": "alice@example.com",
            "cc_addr": "carol@example.com",
            "date": "2024-01-15T10:00:00Z",
            "message_id": "<msg1@example.com>",
            "body_text": "Hi Alice.",
            "is_read": False,
            "is_important": True,
            "attachments": [
                {
                    "filename": "doc.pdf",
                    "content_type": "application/pdf",
                    "content_base64": "SGVsbG8=",
                }
            ],
        },
        {
            "email_id": "2",
            "folder": "INBOX",
            "subject": "Re: meeting",
            "from_addr": "carol@example.com",
            "to_addr": "alice@example.com",
            "date": "2024-01-14T09:00:00Z",
            "message_id": "<msg2@example.com>",
            "body_text": "See you Monday.",
            "is_read": True,
            "is_important": False,
        },
    ],
    "next_email_id": 3,
}


@pytest.fixture
def populated_mailbox(monkeypatch):
    """Install a ``default`` MailboxService into the state registry."""
    monkeypatch.delenv("MCP_PROXY_TOKEN", raising=False)

    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(SAMPLE_DATA, f)
        path = Path(f.name)

    service = MailboxService(path)
    service.load()

    original = dict(state_mod.get_mailboxes())
    state_mod.set_mailboxes({"default": service})
    try:
        yield service
    finally:
        state_mod.set_mailboxes(original)
        path.unlink(missing_ok=True)


@pytest.fixture
def client(populated_mailbox) -> TestClient:
    return TestClient(create_mail_viewer_app(), raise_server_exceptions=False)


class TestViewerRoutes:
    def test_root_serves_html(self, client: TestClient) -> None:
        resp = client.get("/")
        assert resp.status_code == 200
        assert "<title>Mail</title>" in resp.text

    def test_folders_returns_counts(self, client: TestClient) -> None:
        resp = client.get("/api/folders")
        assert resp.status_code == 200
        folders = {f["name"]: f for f in resp.json()["folders"]}
        assert "INBOX" in folders
        assert folders["INBOX"]["total"] == 2
        assert folders["INBOX"]["unread"] == 1

    def test_emails_list_returns_summary(self, client: TestClient) -> None:
        resp = client.get("/api/emails?folder=INBOX")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 2
        ids = {e["email_id"] for e in body["emails"]}
        assert ids == {"1", "2"}
        first = next(e for e in body["emails"] if e["email_id"] == "1")
        assert first["has_attachments"] is True
        assert first["is_important"] is True

    def test_emails_search_filters(self, client: TestClient) -> None:
        resp = client.get("/api/emails?search=meeting")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 1
        assert body["emails"][0]["email_id"] == "2"

    def test_email_detail_returns_full(self, client: TestClient) -> None:
        resp = client.get("/api/emails/1")
        assert resp.status_code == 200
        email = resp.json()["email"]
        assert email["body_text"] == "Hi Alice."
        assert email["cc_addr"] == "carol@example.com"
        assert email["attachments"][0]["filename"] == "doc.pdf"

    def test_email_detail_marks_read(self, client: TestClient, populated_mailbox) -> None:
        # The unread email becomes read after viewing.
        client.get("/api/emails/1")
        email = populated_mailbox.get_email("1")
        assert email.is_read is True

    def test_email_detail_missing_returns_404(self, client: TestClient) -> None:
        resp = client.get("/api/emails/does-not-exist")
        assert resp.status_code == 404
        assert resp.json()["error"]

    def test_contacts_route(self, client: TestClient) -> None:
        resp = client.get("/api/contacts")
        assert resp.status_code == 200
        contacts = resp.json()["contacts"]
        assert {"email": "bob@example.com", "name": "Bob"} in contacts
        groups = resp.json()["groups"]
        team = next(g for g in groups if g["email"] == "team@example.com")
        assert team["members"] == ["alice@example.com", "bob@example.com"]

    def test_stats_route(self, client: TestClient) -> None:
        resp = client.get("/api/stats")
        assert resp.status_code == 200
        stats = resp.json()
        assert stats["total_emails"] == 2
        assert stats["total_unread"] == 1
        assert stats["mailbox"]["email"] == "alice@example.com"


class TestAuth:
    def test_proxy_token_required_when_set(self, populated_mailbox, monkeypatch) -> None:
        monkeypatch.setenv("MCP_PROXY_TOKEN", "secret")
        client = TestClient(create_mail_viewer_app(), raise_server_exceptions=False)

        unauth = client.get("/api/folders")
        assert unauth.status_code == 403

        ok = client.get("/api/folders", headers={"x-proxy-token": "secret"})
        assert ok.status_code == 200


class TestMultiMailbox:
    @pytest.fixture
    def multi_mailbox(self, monkeypatch):
        monkeypatch.delenv("MCP_PROXY_TOKEN", raising=False)

        def _load(email: str) -> MailboxService:
            data = {
                "mailbox": {"email": email, "name": email},
                "contacts": [],
                "folders": [],
                "emails": [],
                "next_email_id": 1,
            }
            with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
                json.dump(data, f)
                p = Path(f.name)
            svc = MailboxService(p)
            svc.load()
            return svc

        original = dict(state_mod.get_mailboxes())
        state_mod.set_mailboxes(
            {
                "alice": _load("alice@example.com"),
                "bob": _load("bob@example.com"),
            }
        )
        try:
            yield
        finally:
            state_mod.set_mailboxes(original)

    def test_defaults_to_first_mailbox_when_no_default(self, multi_mailbox) -> None:
        client = TestClient(create_mail_viewer_app(), raise_server_exceptions=False)
        resp = client.get("/api/stats")
        assert resp.status_code == 200
        # Sorted ids → "alice" comes first.
        assert resp.json()["mailbox"]["email"] == "alice@example.com"

    def test_mailbox_id_query_param_selects(self, multi_mailbox) -> None:
        client = TestClient(create_mail_viewer_app(), raise_server_exceptions=False)
        resp = client.get("/api/stats?mailbox_id=bob")
        assert resp.status_code == 200
        assert resp.json()["mailbox"]["email"] == "bob@example.com"

    def test_unknown_mailbox_id_returns_404(self, multi_mailbox) -> None:
        client = TestClient(create_mail_viewer_app(), raise_server_exceptions=False)
        resp = client.get("/api/folders?mailbox_id=nope")
        assert resp.status_code == 404


class TestLazyInit:
    """Regression: when the registry is empty, hitting the viewer must not 500.

    The viewer used to reference a non-existent server mailbox attribute.
    With the registry empty, ``init_state()`` should be invoked and a default
    mailbox materialized.
    """

    def test_empty_registry_initializes_default(self, monkeypatch) -> None:
        monkeypatch.delenv("MCP_PROXY_TOKEN", raising=False)
        monkeypatch.delenv("BUNDLEDIR", raising=False)
        monkeypatch.delenv("INPUTDIR", raising=False)
        monkeypatch.delenv("OUTPUTDIR", raising=False)
        monkeypatch.delenv("BUNDLE_OUTPUT_DIR", raising=False)

        original = dict(state_mod.get_mailboxes())
        state_mod.set_mailboxes({})
        try:
            client = TestClient(create_mail_viewer_app(), raise_server_exceptions=False)
            for path in ("/api/folders", "/api/emails", "/api/contacts", "/api/stats"):
                resp = client.get(path)
                assert resp.status_code == 200, f"{path} → {resp.status_code}: {resp.text}"
            assert "default" in state_mod.get_mailboxes()
        finally:
            state_mod.set_mailboxes(original)
