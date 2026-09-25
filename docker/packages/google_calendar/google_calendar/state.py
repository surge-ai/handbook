"""State loading, saving, and storage helpers for the Google Calendar mock."""

import json
import os
import uuid
from copy import deepcopy
from pathlib import Path
from typing import Any, cast

from pydantic import ValidationError

from .models import DEFAULT_CALENDAR_TIME_ZONE, CalendarAccountsState, CalendarState
from .utils import get_calendar_data_path

SERVICE_NAME = "google_calendar"
_UNSET = object()

# Will be set by argparse before server starts.
_AGENT_WORKSPACE_ARG: str | None = None

# Cached data file path (lazy initialization). ``None`` means in-memory only.
_DATA_FILE: Path | None = None
_final_path: Path | None = None
_bundle_state_path: Path | None = None
_active_account_id: str = "default"
_accounts: dict[str, dict[str, Any]] = {}


def resolve_bundle_state_paths() -> list[Path]:
    """Resolve the seed-state files inside this service's bundle subdir.

    The folder ``<BUNDLEDIR>/services/<name>/`` is the unit: everything in it
    is this service's seed. Prefer the canonical single-file ``state.json``
    (the output round-trip shape); otherwise hand back ALL ``*.json`` in the
    folder (the raw entities layout), coalesced by the loader.
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


def _merge_flat_into(target: dict[str, Any], source: dict[str, Any]) -> None:
    """Merge a flat account seed into ``target``: dicts update, lists extend, scalars overwrite."""
    for key, value in source.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            target[key].update(value)
        elif isinstance(value, list) and isinstance(target.get(key), list):
            target[key].extend(value)
        else:
            target[key] = value


def _coalesce_account_files(paths: list[Path]) -> dict[str, Any] | None:
    """Coalesce a folder of seed files into one seed dict.

    A single file passes through unchanged (flat single-account or ``{accounts:
    {...}}`` wrapper). With multiple files, flat (non-wrapper) files are merged
    into ONE ``default`` account — the raw entities layout splits a single
    account across per-entity files (``events.json`` + ``calendars.json``), and
    those halves belong together, not in separate accounts. Files carrying an
    explicit ``{accounts: {...}}`` wrapper contribute their named accounts.
    """
    if not paths:
        return None
    if len(paths) == 1:
        return cast(dict[str, Any], json.loads(paths[0].read_text()))
    accounts: dict[str, Any] = {}
    default_account: dict[str, Any] = {}
    for path in paths:
        data = json.loads(path.read_text())
        if isinstance(data, dict) and "accounts" in data:
            accounts.update(data["accounts"])
        elif isinstance(data, dict):
            _merge_flat_into(default_account, data)
    if default_account:
        # Flat files form the default account unless a wrapper already named one.
        accounts.setdefault("default", default_account)
    return {"accounts": accounts}


def resolve_bundle_output_path() -> Path | None:
    output_dir = os.environ.get("BUNDLE_OUTPUT_DIR")
    if not output_dir:
        return None
    return Path(output_dir) / "state.json"


def set_agent_workspace(workspace: str | None) -> None:
    """Set the agent workspace used to resolve the calendar data file."""
    global _AGENT_WORKSPACE_ARG, _DATA_FILE, _active_account_id
    _AGENT_WORKSPACE_ARG = workspace
    _DATA_FILE = None
    _active_account_id = "default"
    _accounts.clear()


def get_data_file_path() -> Path | None:
    """
    Get the data file path. Checks in order:
    1. --agent-workspace CLI argument (computes path via shared function)
    2. AGENT_WORKSPACE environment variable
    3. None for in-memory local testing
    """
    global _AGENT_WORKSPACE_ARG

    # Check CLI arg first (set by argparse before mcp.run())
    if _AGENT_WORKSPACE_ARG:
        return get_calendar_data_path(_AGENT_WORKSPACE_ARG)

    # Check env var
    env_workspace = os.environ.get("AGENT_WORKSPACE")
    if env_workspace:
        return get_calendar_data_path(env_workspace)

    return None


def get_data_file() -> Path | None:
    """Get cached data file path, initializing on first call."""
    global _DATA_FILE
    if _DATA_FILE is None:
        _DATA_FILE = get_data_file_path()
    return _DATA_FILE


def set_snapshot_paths(
    *,
    final_path: Path | str | None | object = _UNSET,
    bundle_state_path: Path | str | None | object = _UNSET,
) -> None:
    """Set snapshot output paths.

    Omitted keyword arguments leave the corresponding path unchanged. Passing
    ``None`` explicitly clears that path.
    """
    global _final_path, _bundle_state_path
    if final_path is not _UNSET:
        _final_path = Path(cast(str | Path, final_path)) if final_path is not None else None
    if bundle_state_path is not _UNSET:
        _bundle_state_path = Path(cast(str | Path, bundle_state_path)) if bundle_state_path is not None else None


def get_final_path() -> Path | None:
    return _final_path


def get_bundle_state_path() -> Path | None:
    return _bundle_state_path


def dump_state(dest: Path, label: str) -> None:
    """Write a JSON snapshot of the current calendar state to *dest*."""
    try:
        data = state_to_json()
        dest.parent.mkdir(parents=True, exist_ok=True)
        with open(dest, "w") as f:
            json.dump(data, f, indent=2)
        print(f"Wrote {label} state to {dest}")
    except Exception as e:
        print(f"Error writing {label} state to {dest}: {e}")


def write_snapshots() -> None:
    """Write configured post-tool snapshots."""
    if _bundle_state_path is not None:
        dump_state(_bundle_state_path, "bundle")
    if _final_path is not None:
        dump_state(_final_path, "final")


def configure_snapshots_from_env(*, write_initial: bool = False) -> None:
    """Configure snapshot paths from OUTPUTDIR/BUNDLE_OUTPUT_DIR."""
    outputdir = os.environ.get("OUTPUTDIR")

    set_snapshot_paths(
        bundle_state_path=resolve_bundle_output_path(),
        final_path=(Path(outputdir) / "final.json") if outputdir else None,
    )

    if write_initial:
        if _bundle_state_path is not None:
            dump_state(_bundle_state_path, "bundle")
        if outputdir:
            dump_state(Path(outputdir) / "initial.json", "initial")


def load_seed_state_from_env() -> None:
    """Load startup seed state from BUNDLEDIR or INPUTDIR, if present."""
    # The bundle folder is read in full and coalesced (a lone state.json is a
    # one-element list); otherwise fall back to the first INPUTDIR/*.json.
    bundle_paths = resolve_bundle_state_paths()
    inputdir = os.environ.get("INPUTDIR")
    if bundle_paths:
        try:
            seed = _coalesce_account_files(bundle_paths)
            assert seed is not None
            state_from_json(seed)
            print(f"Loaded state from {[str(p) for p in bundle_paths]}")
        except Exception as e:
            print(f"Warning: failed to load {[str(p) for p in bundle_paths]}: {e}")
        return

    if inputdir and Path(inputdir).is_dir():
        json_files = sorted(Path(inputdir).glob("*.json"))
        if json_files:
            try:
                with open(json_files[0]) as f:
                    state_from_json(json.load(f))
                print(f"Loaded state from {json_files[0]}")
            except Exception as e:
                print(f"Warning: failed to load {json_files[0]}: {e}")


def init_state(agent_workspace: str | None = None, *, write_initial_snapshot: bool = True) -> None:
    """Initialize calendar state paths, seed data, and snapshot outputs."""
    if agent_workspace:
        set_agent_workspace(agent_workspace)

    print(f"BUNDLEDIR={os.environ.get('BUNDLEDIR', '<unset>')}")
    print(f"INPUTDIR={os.environ.get('INPUTDIR', '<unset>')}")
    print(f"OUTPUTDIR={os.environ.get('OUTPUTDIR', '<unset>')}")

    load_seed_state_from_env()
    configure_snapshots_from_env(write_initial=write_initial_snapshot)


def _read_storage_data() -> dict[str, Any]:
    data_file = get_data_file()
    if data_file is not None and data_file.exists():
        with open(data_file) as f:
            return json.load(f)
    return {"events": {}}


def _accounts_from_storage(
    data: dict[str, Any] | CalendarState | CalendarAccountsState,
    *,
    validate: bool = False,
) -> dict[str, dict[str, Any]]:
    if isinstance(data, (CalendarAccountsState, CalendarState)):
        data = data.model_dump(mode="json", exclude_none=True)

    if "accounts" in data:
        if validate:
            accounts = CalendarAccountsState.model_validate(data).accounts
            return {
                account_id: account.model_dump(mode="json", exclude_none=True)
                for account_id, account in accounts.items()
            }
        return {account_id: deepcopy(account) for account_id, account in data["accounts"].items()}

    if validate:
        account = CalendarState.model_validate(data)
        return {"default": account.model_dump(mode="json", exclude_none=True)}
    return {"default": deepcopy(data)}


def _storage_from_accounts(accounts: dict[str, dict[str, Any]]) -> dict[str, Any]:
    if len(accounts) == 1 and "default" in accounts:
        return accounts["default"]
    return {"accounts": accounts}


def _write_storage(accounts: dict[str, dict[str, Any]]) -> None:
    data_file = get_data_file()
    if data_file is None:
        return
    with open(data_file, "w") as f:
        json.dump(_storage_from_accounts(accounts), f, indent=2)


def _ensure_loaded() -> None:
    global _active_account_id
    if _accounts:
        return
    _accounts.update(_accounts_from_storage(_read_storage_data()))
    _active_account_id = "default" if "default" in _accounts else next(iter(_accounts), "default")


def set_active_account(account_id: str) -> None:
    """Select the account used by subsequent tool handlers."""
    global _active_account_id
    _ensure_loaded()
    if account_id not in _accounts:
        available = ", ".join(sorted(_accounts.keys()))
        raise ValueError(f"Calendar account '{account_id}' not found. Available: {available}")
    _active_account_id = account_id


def get_active_account_id() -> str:
    return _active_account_id


def get_default_account_id() -> str:
    """Return the deterministic default account for viewer reads."""
    _ensure_loaded()
    if "default" in _accounts:
        return "default"
    return next(iter(_accounts), _active_account_id)


def list_accounts() -> list[dict[str, Any]]:
    """List available isolated calendar accounts."""
    _ensure_loaded()
    result = []
    for account_id, account in _accounts.items():
        calendars = get_all_calendars(account)
        result.append(
            {
                "account_id": account_id,
                "calendar_count": len(calendars),
                "event_count": sum(calendar["eventCount"] for calendar in calendars),
                "timeZone": account.get("timeZone", DEFAULT_CALENDAR_TIME_ZONE),
            }
        )
    return result


def load_data(account_id: str | None = None) -> dict:
    """Return the in-memory calendar data for the selected account.

    INPUTDIR seeding happens once at startup via state_from_json() in
    __main__; after that the in-memory account registry is the canonical
    state and the data file is its persisted serialization.
    """
    _ensure_loaded()
    selected_account = account_id or _active_account_id
    if selected_account not in _accounts:
        available = ", ".join(sorted(_accounts.keys()))
        raise ValueError(f"Calendar account '{selected_account}' not found. Available: {available}")
    return _accounts[selected_account]


def save_data(data: dict, account_id: str | None = None) -> None:
    """Validate and save calendar data for the selected account to JSON file."""
    _ensure_loaded()
    selected_account = account_id or _active_account_id
    validated = CalendarState.model_validate(data).model_dump(mode="json", exclude_none=True)
    if selected_account not in _accounts:
        available = ", ".join(sorted(_accounts.keys()))
        raise ValueError(f"Calendar account '{selected_account}' not found. Available: {available}")
    _accounts[selected_account] = validated
    _write_storage(_accounts)


def save_data_or_error(data: dict) -> dict | None:
    try:
        save_data(data)
    except ValidationError as e:
        return {"status": "error", "message": str(e)}
    return None


def generate_event_id() -> str:
    """Generate a unique event ID."""
    return str(uuid.uuid4())[:8]


# ---------------------------------------------------------------------------
# Multi-Calendar Helpers
# ---------------------------------------------------------------------------


def get_calendar_events(data: dict, calendar_id: str) -> dict:
    """Get the events dict for a calendar. Handles backward compatibility:
    - If data has 'calendars' dict, looks up by calendar_id
    - If data only has flat 'events' dict, treats it as 'primary'
    Returns the events dict (mutable reference into data).
    """
    if "calendars" in data and calendar_id in data["calendars"]:
        return data["calendars"][calendar_id].setdefault("events", {})

    # Backward compat: flat 'events' dict is the primary calendar
    if calendar_id == "primary":
        return data.setdefault("events", {})

    # Calendar doesn't exist
    return {}


def set_calendar_event(data: dict, calendar_id: str, event_id: str, event: dict) -> bool:
    """Set an event in a calendar. Returns False if calendar doesn't exist."""
    if "calendars" in data and calendar_id in data["calendars"]:
        if "events" not in data["calendars"][calendar_id]:
            data["calendars"][calendar_id]["events"] = {}
        data["calendars"][calendar_id]["events"][event_id] = event
        return True

    if calendar_id == "primary":
        if "events" not in data:
            data["events"] = {}
        data["events"][event_id] = event
        return True

    return False


def delete_calendar_event(data: dict, calendar_id: str, event_id: str) -> dict | None:
    """Delete an event from a calendar. Returns the deleted event or None."""
    events = get_calendar_events(data, calendar_id)
    if event_id in events:
        return events.pop(event_id)
    return None


def get_all_calendars(data: dict) -> list[dict]:
    """Get list of all calendars with metadata."""
    calendars = []

    # Always include primary
    primary_events = data.get("events", {})
    primary_time_zone = data.get("timeZone", DEFAULT_CALENDAR_TIME_ZONE)
    calendars.append(
        {
            "id": "primary",
            "summary": "Primary",
            "description": "Default calendar",
            "timeZone": primary_time_zone,
            "primary": True,
            "eventCount": len(primary_events),
        }
    )

    # Add any explicit calendars (except primary which we already handled)
    for cal_id, cal_data in data.get("calendars", {}).items():
        if cal_id == "primary":
            continue
        calendars.append(
            {
                "id": cal_id,
                "summary": cal_data.get("summary", cal_id),
                "description": cal_data.get("description", ""),
                "timeZone": cal_data.get("timeZone", primary_time_zone),
                "primary": False,
                "eventCount": len(cal_data.get("events", {})),
            }
        )

    return calendars


# ---------------------------------------------------------------------------
# State Management Tools
# ---------------------------------------------------------------------------


def state_to_json() -> dict:
    """Return the full calendar state as a JSON-native dict.

    Round-trips with state_from_json. Emits both legacy flat ``events`` shape
    and the multi-calendar ``calendars`` dict when present, so multi-calendar
    worlds survive an export/import cycle.
    """
    _ensure_loaded()
    return deepcopy(_storage_from_accounts(_accounts))


def state_from_json(data: dict[str, Any] | CalendarState | CalendarAccountsState) -> None:
    """Full-replace the calendar state from a JSON-native dict."""
    global _active_account_id
    accounts = _accounts_from_storage(data, validate=True)
    _accounts.clear()
    _accounts.update(accounts)
    _write_storage(_accounts)
    _active_account_id = "default" if "default" in _accounts else next(iter(_accounts), "default")
