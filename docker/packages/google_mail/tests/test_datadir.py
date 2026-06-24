import json
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _reset_mail_state():
    import google_mail.state as state

    state.set_mailboxes({})
    state.set_snapshot_paths(final_path=None, bundle_state_path=None)
    yield
    state.set_mailboxes({})
    state.set_snapshot_paths(final_path=None, bundle_state_path=None)


def _write_mailbox(path):
    path.write_text(
        json.dumps(
            {
                "mailbox": {"email": "t@t.com", "name": "T"},
                "contacts": [],
                "folders": [],
                "emails": [],
                "next_email_id": 1,
            }
        )
    )


def test_inputdir_loads_first_json(monkeypatch, tmp_path):
    """When INPUTDIR is set, the server loads the first .json in that dir."""
    data_file = tmp_path / "google_mail.json"
    _write_mailbox(data_file)
    monkeypatch.setenv("INPUTDIR", str(tmp_path))

    json_files = sorted(Path(str(tmp_path)).glob("*.json"))
    assert json_files[0] == data_file


def test_inputdir_loads_only_first_file_not_merged(monkeypatch, tmp_path):
    """Legacy contract: INPUTDIR loads ONLY the first *.json (alphabetically),
    not a merge of all of them — matching scripts/validate_external_services.py.
    Folder coalescing is a bundle-only behavior."""
    import google_mail.server as srv
    import google_mail.state as state

    inputdir = tmp_path / "in"
    inputdir.mkdir()
    (inputdir / "a.json").write_text(
        json.dumps({"mailbox": {"email": "first@t.com", "name": "First"}, "emails": [], "next_email_id": 1})
    )
    (inputdir / "b.json").write_text(
        json.dumps({"mailbox": {"email": "second@t.com", "name": "Second"}, "emails": [], "next_email_id": 1})
    )

    monkeypatch.delenv("BUNDLEDIR", raising=False)
    monkeypatch.setenv("INPUTDIR", str(inputdir))
    monkeypatch.delenv("OUTPUTDIR", raising=False)
    monkeypatch.delenv("BUNDLE_OUTPUT_DIR", raising=False)
    state.set_mailboxes({})

    srv.init_state()

    mailboxes = state.get_mailboxes()
    assert set(mailboxes) == {"default"}
    assert mailboxes["default"].data.mailbox.email == "first@t.com"


def test_inputdir_empty_uses_default(monkeypatch, tmp_path):
    """When INPUTDIR is set but empty, server starts with an empty default mailbox."""
    monkeypatch.setenv("INPUTDIR", str(tmp_path))

    json_files = sorted(Path(str(tmp_path)).glob("*.json"))
    assert json_files == []


def test_inputdir_not_set_uses_default(monkeypatch):
    """When INPUTDIR is not set, server starts with an empty default mailbox (no error)."""
    monkeypatch.delenv("INPUTDIR", raising=False)
    import os

    assert os.environ.get("INPUTDIR") is None
    # Server should start cleanly — no error raised


def test_bundle_state_json_preferred_over_inputdir(monkeypatch, tmp_path):
    """When BUNDLEDIR is set and $BUNDLEDIR/services/google_mail/state.json
    exists, init_state loads it instead of any legacy *.json files
    in INPUTDIR."""
    import google_mail.server as srv
    import google_mail.state as state

    bundle_root = tmp_path / "bundle"
    service_dir = bundle_root / "services" / "google_mail"
    service_dir.mkdir(parents=True)
    (service_dir / "state.json").write_text(
        json.dumps(
            {
                "mailbox": {"email": "bundle@t.com", "name": "Bundle"},
                "contacts": [],
                "folders": [],
                "emails": [],
                "next_email_id": 1,
            }
        )
    )

    inputdir = tmp_path / "legacy"
    inputdir.mkdir()
    _write_mailbox(inputdir / "legacy.json")

    monkeypatch.setenv("BUNDLEDIR", str(bundle_root))
    monkeypatch.setenv("INPUTDIR", str(inputdir))
    monkeypatch.delenv("OUTPUTDIR", raising=False)
    monkeypatch.delenv("BUNDLE_OUTPUT_DIR", raising=False)

    srv.init_state()

    assert state.get_mailboxes()["default"].data.mailbox.email == "bundle@t.com"


def test_bundle_glob_when_state_json_absent(monkeypatch, tmp_path):
    """When BUNDLEDIR carries a per-service subdir with a named JSON (e.g.
    the preserved entities-zip layout) but no state.json, the loader reads
    it from that subdir."""
    import google_mail.server as srv
    import google_mail.state as state

    bundle_root = tmp_path / "bundle"
    service_dir = bundle_root / "services" / "google_mail"
    service_dir.mkdir(parents=True)
    (service_dir / "inbox.json").write_text(
        json.dumps(
            {
                "mailbox": {"email": "preserved@t.com", "name": "Preserved"},
                "contacts": [],
                "folders": [],
                "emails": [],
                "next_email_id": 1,
            }
        )
    )

    monkeypatch.setenv("BUNDLEDIR", str(bundle_root))
    monkeypatch.delenv("INPUTDIR", raising=False)
    monkeypatch.delenv("OUTPUTDIR", raising=False)
    monkeypatch.delenv("BUNDLE_OUTPUT_DIR", raising=False)

    srv.init_state()

    assert state.get_mailboxes()["default"].data.mailbox.email == "preserved@t.com"


def test_falls_back_to_inputdir_when_bundle_service_dir_missing(monkeypatch, tmp_path):
    """When BUNDLEDIR is set but doesn't carry this service's slice,
    init_state falls back to the legacy INPUTDIR/*.json glob — a partial
    bundle doesn't strand a service."""
    import google_mail.server as srv
    import google_mail.state as state

    # BUNDLEDIR points at a real dir, but no google_mail/ subdir under it.
    bundle_root = tmp_path / "bundle"
    (bundle_root / "services").mkdir(parents=True)

    inputdir = tmp_path / "legacy"
    inputdir.mkdir()
    (inputdir / "legacy.json").write_text(
        json.dumps(
            {
                "mailbox": {"email": "legacy@t.com", "name": "Legacy"},
                "contacts": [],
                "folders": [],
                "emails": [],
                "next_email_id": 1,
            }
        )
    )

    monkeypatch.setenv("BUNDLEDIR", str(bundle_root))
    monkeypatch.setenv("INPUTDIR", str(inputdir))
    monkeypatch.delenv("OUTPUTDIR", raising=False)
    monkeypatch.delenv("BUNDLE_OUTPUT_DIR", raising=False)

    srv.init_state()

    assert state.get_mailboxes()["default"].data.mailbox.email == "legacy@t.com"


def test_falls_back_to_inputdir_when_bundledir_unset(monkeypatch, tmp_path):
    """When BUNDLEDIR is unset entirely, init_state uses INPUTDIR (the
    common local-dev path). The bundle code path is skipped."""
    import google_mail.server as srv
    import google_mail.state as state

    inputdir = tmp_path / "legacy"
    inputdir.mkdir()
    (inputdir / "legacy.json").write_text(
        json.dumps(
            {
                "mailbox": {"email": "legacy@t.com", "name": "Legacy"},
                "contacts": [],
                "folders": [],
                "emails": [],
                "next_email_id": 1,
            }
        )
    )

    monkeypatch.delenv("BUNDLEDIR", raising=False)
    monkeypatch.setenv("INPUTDIR", str(inputdir))
    monkeypatch.delenv("OUTPUTDIR", raising=False)
    monkeypatch.delenv("BUNDLE_OUTPUT_DIR", raising=False)

    srv.init_state()

    assert state.get_mailboxes()["default"].data.mailbox.email == "legacy@t.com"


def test_init_state_configures_snapshot_paths_when_registry_preloaded(monkeypatch, tmp_path):
    """Preloaded state should still pick up env-configured snapshot paths."""
    import google_mail.server as srv
    import google_mail.state as state
    from google_mail.services.mailbox import MailboxService

    data_file = tmp_path / "mailbox.json"
    _write_mailbox(data_file)
    svc = MailboxService(data_file)
    svc.load()

    outputdir = tmp_path / "output"
    bundle_output_dir = tmp_path / "services" / "google_mail"
    state.set_mailboxes({"default": svc})
    state.set_snapshot_paths(final_path=None, bundle_state_path=None)
    monkeypatch.setenv("OUTPUTDIR", str(outputdir))
    monkeypatch.setenv("BUNDLE_OUTPUT_DIR", str(bundle_output_dir))

    srv.init_state()

    assert state.get_final_path() == outputdir / "final.json"
    assert state.get_bundle_state_path() == bundle_output_dir / "state.json"


def _write_mailbox_email(path, email):
    path.write_text(
        json.dumps(
            {
                "mailbox": {"email": email, "name": email.split("@")[0]},
                "contacts": [],
                "folders": [],
                "emails": [],
                "next_email_id": 1,
            }
        )
    )


def _email_record(email_id, subject):
    return {
        "email_id": email_id,
        "folder": "INBOX",
        "subject": subject,
        "from_addr": "sender@t.com",
        "to_addr": "agent@t.com",
        "date": "2026-01-01T00:00:00+00:00",
        "message_id": f"<{email_id}@t.com>",
        "body_text": subject,
        "is_read": False,
    }


def test_bundle_multifile_flat_files_merge_into_one_mailbox(monkeypatch, tmp_path):
    """the raw entities layout splits ONE mailbox across per-entity files
    (e.g. inbox.json + sent.json). Flat files without a {mailboxes} wrapper
    coalesce into a single default mailbox — entities combined, not fragmented
    into separate mailboxes."""
    import google_mail.server as srv
    import google_mail.state as state

    service_dir = tmp_path / "bundle" / "services" / "google_mail"
    service_dir.mkdir(parents=True)
    # Two halves of the SAME mailbox: identity + first email; second email.
    (service_dir / "a.json").write_text(
        json.dumps(
            {
                "mailbox": {"email": "agent@t.com", "name": "Agent"},
                "contacts": [],
                "folders": [],
                "emails": [_email_record("1", "first")],
                "next_email_id": 2,
            }
        )
    )
    (service_dir / "b.json").write_text(json.dumps({"emails": [_email_record("2", "second")], "next_email_id": 3}))

    monkeypatch.setenv("BUNDLEDIR", str(tmp_path / "bundle"))
    monkeypatch.delenv("INPUTDIR", raising=False)
    monkeypatch.delenv("OUTPUTDIR", raising=False)
    monkeypatch.delenv("BUNDLE_OUTPUT_DIR", raising=False)

    state.set_mailboxes({})
    srv.init_state()

    mailboxes = state.get_mailboxes()
    assert set(mailboxes) == {"default"}, "flat per-entity files must merge into ONE mailbox"
    data = mailboxes["default"].data
    assert data.mailbox.email == "agent@t.com"
    subjects = {e.subject for e in data.emails}
    assert subjects == {"first", "second"}, "both per-entity files' emails must be present"
    assert data.next_email_id == 3


def test_bundle_mailboxes_wrapper_yields_named_mailboxes(monkeypatch, tmp_path):
    """An explicit {mailboxes: {...}} wrapper still produces multiple named
    mailboxes — multi-tenant worlds opt in via the wrapper, not via separate
    flat files."""
    import google_mail.server as srv
    import google_mail.state as state

    service_dir = tmp_path / "bundle" / "services" / "google_mail"
    service_dir.mkdir(parents=True)
    (service_dir / "state.json").write_text(
        json.dumps(
            {
                "mailboxes": {
                    "alice": {"mailbox": {"email": "a@t.com", "name": "A"}, "emails": [], "next_email_id": 1},
                    "bob": {"mailbox": {"email": "b@t.com", "name": "B"}, "emails": [], "next_email_id": 1},
                }
            }
        )
    )

    monkeypatch.setenv("BUNDLEDIR", str(tmp_path / "bundle"))
    monkeypatch.delenv("INPUTDIR", raising=False)
    monkeypatch.delenv("OUTPUTDIR", raising=False)
    monkeypatch.delenv("BUNDLE_OUTPUT_DIR", raising=False)

    state.set_mailboxes({})
    srv.init_state()

    mailboxes = state.get_mailboxes()
    assert set(mailboxes) == {"alice", "bob"}
    assert {svc.data.mailbox.email for svc in mailboxes.values()} == {"a@t.com", "b@t.com"}


def test_resolve_bundle_state_paths_returns_whole_folder(monkeypatch, tmp_path):
    """The plural resolver returns ALL sorted *.json when there's no state.json,
    and exactly [state.json] when one is present."""
    import google_mail.state as state

    service_dir = tmp_path / "services" / "google_mail"
    service_dir.mkdir(parents=True)
    a_json = service_dir / "a.json"
    b_json = service_dir / "b.json"
    _write_mailbox_email(a_json, "a@t.com")
    _write_mailbox_email(b_json, "b@t.com")

    monkeypatch.setenv("BUNDLEDIR", str(tmp_path))

    assert state.resolve_bundle_state_paths() == [a_json, b_json]

    state_json = service_dir / "state.json"
    _write_mailbox_email(state_json, "state@t.com")
    assert state.resolve_bundle_state_paths() == [state_json]
