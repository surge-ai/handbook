"""State registry, startup loading, and snapshots for the Google Mail mock."""

from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any, cast

from google_mail.models import GoogleMailState, MailboxData, MultiMailboxData
from google_mail.services.mailbox import MailboxService

_logger = logging.getLogger(__name__)

SERVICE_NAME = "google_mail"

_mailboxes: dict[str, MailboxService] = {}
_final_path: Path | None = None
_bundle_state_path: Path | None = None
_UNSET = object()


def resolve_bundle_state_paths() -> list[Path]:
    """Resolve the seed-state files inside this service's bundle subdir.

    The folder ``<BUNDLEDIR>/services/<name>/`` is the unit: everything in it
    is this service's seed. Prefer the canonical single-file ``state.json``
    (the output round-trip shape); otherwise hand back ALL ``*.json`` in the
    folder (the raw entities layout, e.g. one file per mailbox), read in
    full the same way INPUTDIR files are.
    """
    bundle_dir = os.environ.get("BUNDLEDIR")
    if not bundle_dir:
        return []
    service_dir = Path(bundle_dir) / "services" / SERVICE_NAME
    state_file = service_dir / "state.json"
    if state_file.is_file():
        return [state_file]
    if service_dir.is_dir():
        return sorted(service_dir.glob("*.json"))
    return []


def resolve_bundle_state_path() -> Path | None:
    """Back-compat single-file view of :func:`resolve_bundle_state_paths`."""
    paths = resolve_bundle_state_paths()
    return paths[0] if paths else None


def resolve_bundle_output_path() -> Path | None:
    output_dir = os.environ.get("BUNDLE_OUTPUT_DIR")
    if not output_dir:
        return None
    return Path(output_dir) / "state.json"


def _merge_mailbox_into(target: dict[str, Any], source: dict[str, Any]) -> None:
    """Merge a flat mailbox seed into ``target``: lists extend, dicts update,
    ``next_email_id`` takes the max, remaining scalars overwrite."""
    for key, value in source.items():
        if key == "next_email_id":
            target[key] = max(target.get(key, 0), value)
        elif isinstance(value, list) and isinstance(target.get(key), list):
            target[key].extend(value)
        elif isinstance(value, dict) and isinstance(target.get(key), dict):
            target[key].update(value)
        else:
            target[key] = value


def _temporary_json_path(suffix: str) -> Path:
    """Create and return a closed temporary JSON path."""
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        return Path(tmp.name)


def get_mailboxes() -> dict[str, MailboxService]:
    """Return the live mailbox registry."""
    return _mailboxes


def set_mailboxes(mailboxes: dict[str, MailboxService]) -> None:
    """Replace the live mailbox registry."""
    _mailboxes.clear()
    _mailboxes.update(mailboxes)


def get_mailbox(mailbox_id: str = "default") -> MailboxService:
    """Get a mailbox service instance by ID."""
    if not _mailboxes:
        raise RuntimeError("Mailbox service not initialized")
    if mailbox_id not in _mailboxes:
        available = ", ".join(sorted(_mailboxes.keys()))
        raise ValueError(f"Mailbox '{mailbox_id}' not found. Available: {available}")
    return _mailboxes[mailbox_id]


def set_snapshot_paths(
    *,
    final_path: Path | None | object = _UNSET,
    bundle_state_path: Path | None | object = _UNSET,
) -> None:
    """Update post-write snapshot destinations.

    Omitted arguments leave the existing path unchanged. Pass ``None``
    explicitly to clear a path.
    """
    global _final_path, _bundle_state_path
    if final_path is not _UNSET:
        _final_path = None if final_path is None else Path(cast(Path, final_path))
    if bundle_state_path is not _UNSET:
        _bundle_state_path = None if bundle_state_path is None else Path(cast(Path, bundle_state_path))


def get_final_path() -> Path | None:
    """Return the configured legacy final.json path."""
    return _final_path


def get_bundle_state_path() -> Path | None:
    """Return the configured bundle snapshot path."""
    return _bundle_state_path


def create_default_mailbox(data_path: Path) -> None:
    """Create a default empty mailbox file."""
    default_email = os.environ.get("MAIL_MCP_DEFAULT_EMAIL", "agent@mail.com")
    default_name = os.environ.get("MAIL_MCP_DEFAULT_NAME", "Agent")
    default_data = {
        "mailbox": {"email": default_email, "name": default_name},
        "contacts": [],
        "groups": [],
        "folders": [],
        "emails": [],
        "next_email_id": 1,
    }
    data_path.parent.mkdir(parents=True, exist_ok=True)
    with open(data_path, "w") as f:
        json.dump(default_data, f, indent=2)
    _logger.info("Created default empty mailbox at %s", data_path)


def dump_state(dest: Path, label: str) -> None:
    """Write a JSON snapshot of all mailboxes to *dest*."""
    if not _mailboxes:
        return
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        snapshot = state_to_json()
        with open(dest, "w") as f:
            json.dump(snapshot, f, indent=2, default=str)
        _logger.info("Wrote %s state to %s", label, dest)
    except Exception:
        _logger.exception("Failed to write %s state to %s", label, dest)


def write_snapshots() -> None:
    """Write configured post-tool snapshots."""
    if _bundle_state_path is not None:
        dump_state(_bundle_state_path, "bundle")
    if _final_path is not None:
        dump_state(_final_path, "final")


def configure_snapshots_from_env(*, write_initial: bool = False) -> None:
    """Configure snapshot paths from OUTPUTDIR and BUNDLE_OUTPUT_DIR."""
    outputdir = os.environ.get("OUTPUTDIR")
    bundle_output_dir = os.environ.get("BUNDLE_OUTPUT_DIR")
    _logger.info("OUTPUTDIR=%s BUNDLE_OUTPUT_DIR=%s", outputdir or "<unset>", bundle_output_dir or "<unset>")

    bundle_state_path = resolve_bundle_output_path()
    final_path = Path(outputdir) / "final.json" if outputdir else None
    set_snapshot_paths(final_path=final_path, bundle_state_path=bundle_state_path)

    if write_initial and bundle_state_path is not None:
        dump_state(bundle_state_path, "bundle")
    if write_initial and outputdir:
        dump_state(Path(outputdir) / "initial.json", "initial")


def state_to_json() -> dict[str, Any]:
    """Return the full Google Mail state as a JSON-native dict."""
    if len(_mailboxes) == 1 and "default" in _mailboxes:
        return _mailboxes["default"].data.model_dump(mode="json")
    return {"mailboxes": {mid: svc.data.model_dump(mode="json") for mid, svc in _mailboxes.items()}}


def export_state_model() -> GoogleMailState:
    """Return the full Google Mail state as a typed model."""
    if len(_mailboxes) == 1 and "default" in _mailboxes:
        return MailboxData.model_validate(_mailboxes["default"].to_json())
    return MultiMailboxData(
        mailboxes={mid: MailboxData.model_validate(svc.to_json()) for mid, svc in _mailboxes.items()}
    )


def state_from_json(state: GoogleMailState | dict[str, Any]) -> None:
    """Full-replace the mailbox registry from a JSON-native dict or model."""
    if isinstance(state, MailboxData | MultiMailboxData):
        validated_state = state
    elif "mailboxes" in state:
        validated_state = MultiMailboxData.model_validate(state)
    else:
        validated_state = MailboxData.model_validate(state)

    _mailboxes.clear()
    if isinstance(validated_state, MultiMailboxData):
        for mid, mdata in validated_state.mailboxes.items():
            payload = mdata.model_dump(mode="json")
            tmp = _temporary_json_path(suffix=f"_{mid}.json")
            with open(tmp, "w") as f:
                json.dump(payload, f, indent=2, default=str)
            svc = MailboxService(tmp)
            svc.from_json(payload, persist=True)
            _mailboxes[mid] = svc
        return

    payload = validated_state.model_dump(mode="json")
    tmp = _temporary_json_path(suffix="_default.json")
    with open(tmp, "w") as f:
        json.dump(payload, f, indent=2, default=str)
    _mailboxes["default"] = MailboxService(tmp)
    _mailboxes["default"].from_json(payload, persist=True)


def init_state() -> None:
    """Eagerly initialize mailbox(es) and write the initial state snapshot.

    Supports two data formats:
    - Single mailbox (backward compat): flat {mailbox, contacts, emails, ...}
    - Multi-mailbox: {mailboxes: {id: {mailbox, contacts, emails, ...}, ...}}
    """
    if _mailboxes:
        configure_snapshots_from_env()
        return

    inputdir = os.environ.get("INPUTDIR")
    # Bundle folder is coalesced (read in full); INPUTDIR keeps its legacy
    # first-file-only contract (see scripts/validate_external_services.py).
    bundle_files = resolve_bundle_state_paths()
    inputdir_files: list[Path] = []
    if not bundle_files and inputdir and Path(inputdir).is_dir():
        inputdir_files = sorted(Path(inputdir).glob("*.json"))[:1]
    json_files = bundle_files or inputdir_files
    _logger.info(
        "BUNDLEDIR=%s INPUTDIR=%s seed_files=%s",
        os.environ.get("BUNDLEDIR") or "<unset>",
        inputdir or "<unset>",
        [str(p) for p in json_files] or "<none>",
    )

    if json_files:
        # Coalesce the bundle folder: flat single-mailbox files merge into ONE
        # default mailbox (the raw entities layout splits one mailbox
        # across per-entity files, e.g. services/google_mail/inbox.json — those
        # belong together, not in separate mailboxes). Files carrying a
        # {mailboxes: {...}} wrapper contribute their named mailboxes. The
        # legacy INPUTDIR path is a single file, so the loop is a no-op merge.
        mailboxes_data: dict[str, dict[str, Any]] = {}
        default_mailbox: dict[str, Any] = {}
        for data_path in json_files:
            _logger.info("Loading mailbox(es) from %s", data_path)
            with open(data_path) as f:
                raw = json.load(f)
            if "mailboxes" in raw:
                mailboxes_data.update(raw["mailboxes"])
            else:
                _merge_mailbox_into(default_mailbox, raw)
        if default_mailbox:
            mailboxes_data.setdefault("default", default_mailbox)

        for mid, mdata in mailboxes_data.items():
            tmp = _temporary_json_path(suffix=f"_{mid}.json")
            with open(tmp, "w") as f:
                json.dump(mdata, f, indent=2)
            svc = MailboxService(tmp)
            svc.load()
            _mailboxes[mid] = svc
            _logger.info("Loaded mailbox '%s' (%s)", mid, mdata.get("mailbox", {}).get("email", "?"))
    else:
        data_path = _temporary_json_path(suffix=".json")
        create_default_mailbox(data_path)
        if inputdir:
            _logger.warning("INPUTDIR set but no .json files found in %s", inputdir)
        svc = MailboxService(data_path)
        svc.load()
        _mailboxes["default"] = svc

    _logger.info("Mail MCP server initialized with %d mailbox(es)", len(_mailboxes))
    configure_snapshots_from_env(write_initial=True)
