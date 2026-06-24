"""Slack state loading, saving, and mutation helpers."""

from __future__ import annotations

import json
import os
import re
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

from slack_mock.models import SlackFile, SlackMessage, SlackMockState, SlackState, SlackWorkspacesState

SERVICE_NAME = "slack"
DEFAULT_BOT_USER_ID = "U_MOCK_BOT"
_UNSET = object()

_workspaces: dict[str, SlackState] = {}
_active_workspace_id = "default"
_current_state: SlackState | None = None
_last_timestamp_us = 0
_final_path: Path | None = None
_bundle_state_path: Path | None = None


def resolve_bundle_state_paths() -> list[Path]:
    """Resolve the seed-state files inside this service's bundle subdir.

    The folder ``<BUNDLEDIR>/services/<name>/`` is the unit: everything in it
    is this service's seed. Prefer the canonical single-file ``state.json``
    (the output round-trip shape); otherwise hand back ALL ``*.json`` in the
    folder (the raw entities layout, e.g. per-entity files), which the
    caller coalesces the same way it merges INPUTDIR files.
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


def resolve_input_paths() -> list[Path]:
    input_dir = os.environ.get("INPUTDIR")
    if not input_dir:
        return []
    path = Path(input_dir)
    if not path.is_dir():
        return []
    return sorted(path.glob("*.json"))


def _default_bot_user() -> dict[str, Any]:
    return {
        "id": DEFAULT_BOT_USER_ID,
        "name": "slackbot",
        "real_name": "Mock Bot",
        "profile": {"display_name": "slackbot", "status_text": "", "status_emoji": ""},
        "is_bot": True,
        "deleted": False,
    }


def get_default_state() -> SlackState:
    return SlackState.model_validate(
        {
            "bot_user_id": DEFAULT_BOT_USER_ID,
            "users": {DEFAULT_BOT_USER_ID: _default_bot_user()},
        }
    )


def get_default_workspaces() -> dict[str, SlackState]:
    return {"default": get_default_state()}


def state_to_json() -> dict[str, Any]:
    _ensure_loaded()
    return _storage_from_workspaces(_workspaces)


def state_from_json(data: dict[str, Any] | SlackMockState) -> None:
    workspaces = _workspaces_from_storage(data)
    _install_workspaces(workspaces)


def _canonicalize_state(data: dict[str, Any] | SlackState) -> SlackState:
    next_state = data if isinstance(data, SlackState) else SlackState.model_validate(data)
    next_state.counters.channelId = max(next_state.counters.channelId, _next_channel_id_counter(next_state))
    next_state.counters.fileId = max(next_state.counters.fileId, _max_file_id(next_state))
    return SlackState.model_validate(next_state.model_dump(mode="json", by_alias=True))


def _workspaces_from_storage(data: dict[str, Any] | SlackMockState) -> dict[str, SlackState]:
    global _last_timestamp_us

    if isinstance(data, SlackWorkspacesState):
        raw_workspaces = data.workspaces
    elif isinstance(data, SlackState):
        raw_workspaces = {"default": data}
    elif "workspaces" in data:
        raw_workspaces = SlackWorkspacesState.model_validate(data).workspaces
    else:
        merged = get_default_state().model_dump(mode="json", by_alias=True)
        if "users" in data:
            merged["users"].update(data["users"])
        if "channels" in data:
            merged["channels"].update(data["channels"])
        if "messages" in data:
            merged["messages"].update(data["messages"])
        if "counters" in data:
            counters = data["counters"]
            if "channelId" in counters:
                merged["counters"]["channelId"] = max(merged["counters"]["channelId"], counters["channelId"])
            if "fileId" in counters:
                merged["counters"]["fileId"] = max(merged["counters"]["fileId"], counters["fileId"])
        if "bot_user_id" in data:
            merged["bot_user_id"] = data["bot_user_id"]
        if data.get("is_admin") is True:
            merged["is_admin"] = True
        raw_workspaces = {"default": merged}

    next_timestamp_us = 0
    workspaces: dict[str, SlackState] = {}
    for workspace_id, workspace_state in raw_workspaces.items():
        canonical = _canonicalize_state(workspace_state)
        next_timestamp_us = max(next_timestamp_us, _max_message_timestamp_us(canonical))
        workspaces[workspace_id] = canonical
    if not workspaces:
        raise ValueError("Slack state must contain at least one workspace")
    _last_timestamp_us = next_timestamp_us
    return workspaces


def _storage_from_workspaces(workspaces: dict[str, SlackState]) -> dict[str, Any]:
    if set(workspaces) == {"default"}:
        return workspaces["default"].model_dump(mode="json", by_alias=True, exclude_none=True)
    return {
        "workspaces": {
            workspace_id: workspace.model_dump(mode="json", by_alias=True, exclude_none=True)
            for workspace_id, workspace in workspaces.items()
        }
    }


def _install_workspaces(workspaces: dict[str, SlackState]) -> None:
    global _active_workspace_id, _current_state
    _workspaces.clear()
    _workspaces.update(workspaces)
    _active_workspace_id = "default" if "default" in _workspaces else next(iter(_workspaces))
    _current_state = _workspaces[_active_workspace_id]


def _ensure_loaded() -> None:
    if _current_state is None:
        load_state()


def _next_channel_id_counter(state: SlackState) -> int:
    next_channel_id = state.counters.channelId
    for channel_id in state.channels:
        match = re.match(r"^[CG]0*(\d+)$", channel_id)
        if match:
            next_channel_id = max(next_channel_id, int(match.group(1)) + 1)
    return next_channel_id


def _max_file_id(state: SlackState) -> int:
    max_file_id = state.counters.fileId
    for messages in state.messages.values():
        for message in messages:
            for file in message.files or []:
                match = re.match(r"^F0*(\d+)$", file.id)
                if match:
                    max_file_id = max(max_file_id, int(match.group(1)))
    return max_file_id


def _max_message_timestamp_us(state: SlackState) -> int:
    max_timestamp_us = 0
    for messages in state.messages.values():
        for message in messages:
            max_timestamp_us = max(max_timestamp_us, _timestamp_to_microseconds(message.ts))
    return max_timestamp_us


def _timestamp_to_microseconds(ts: str) -> int:
    seconds, micros = ts.split(".", 1)
    return int(seconds) * 1_000_000 + int(micros.ljust(6, "0")[:6])


def dump_state(dest: Path) -> None:
    if _current_state is None and not _workspaces:
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(state_to_json(), indent=2), encoding="utf-8")


def set_snapshot_paths(
    *,
    final_path: Path | str | None | object = _UNSET,
    bundle_state_path: Path | str | None | object = _UNSET,
) -> None:
    """Configure optional snapshot destinations used after write tools."""
    global _final_path, _bundle_state_path
    if final_path is not _UNSET:
        _final_path = Path(cast(str | Path, final_path)) if final_path is not None else None
    if bundle_state_path is not _UNSET:
        _bundle_state_path = Path(cast(str | Path, bundle_state_path)) if bundle_state_path is not None else None


def write_snapshots() -> None:
    if _bundle_state_path is not None:
        dump_state(_bundle_state_path)
    if _final_path is not None:
        dump_state(_final_path)


def merge_inputdir_files(paths: list[Path]) -> dict[str, Any]:
    if len(paths) == 1:
        try:
            only = json.loads(paths[0].read_text(encoding="utf-8"))
        except Exception as exc:
            print(f"Error loading Slack state from {paths[0]}: {exc}")
        else:
            if "workspaces" in only:
                print(f"Loaded Slack state from {paths[0]}")
                return only

    merged = get_default_state().model_dump(mode="json", by_alias=True)
    workspace_parts: dict[str, Any] = {}
    legacy_seen = False
    for file in paths:
        try:
            partial = json.loads(file.read_text(encoding="utf-8"))
        except Exception as exc:
            print(f"Error loading Slack state from {file}: {exc}")
            continue

        if "workspaces" in partial:
            workspace_parts.update(partial["workspaces"])
            print(f"Loaded Slack state from {file}")
            continue

        legacy_seen = True
        if "users" in partial:
            merged["users"].update(partial["users"])
        if "channels" in partial:
            merged["channels"].update(partial["channels"])
        if "messages" in partial:
            merged["messages"].update(partial["messages"])
        if "counters" in partial:
            counters = partial["counters"]
            if "channelId" in counters:
                merged["counters"]["channelId"] = max(merged["counters"]["channelId"], counters["channelId"])
            if "fileId" in counters:
                merged["counters"]["fileId"] = max(merged["counters"]["fileId"], counters["fileId"])
        if "bot_user_id" in partial:
            merged["bot_user_id"] = partial["bot_user_id"]
        if partial.get("is_admin") is True:
            merged["is_admin"] = True
        print(f"Loaded Slack state from {file}")
    if workspace_parts:
        if legacy_seen:
            workspace_parts.setdefault("default", merged)
        return {"workspaces": workspace_parts}
    return merged


def load_state() -> SlackState:
    if _current_state is not None:
        return _current_state

    # The bundle folder is read in full and coalesced exactly like INPUTDIR:
    # both feed the same merge so a folder of per-entity *.json works without
    # special-casing. A lone state.json is just a one-element list.
    seed_paths = resolve_bundle_state_paths() or resolve_input_paths()
    seed = merge_inputdir_files(seed_paths) if seed_paths else get_default_state().model_dump(mode="json")

    state_from_json(seed)

    return get_state()


def init_state() -> None:
    load_state()
    bundle_output = resolve_bundle_output_path()
    output_dir = Path(value) if (value := os.environ.get("OUTPUTDIR")) else None
    set_snapshot_paths(
        bundle_state_path=bundle_output,
        final_path=output_dir / "final.json" if output_dir is not None else None,
    )
    if bundle_output is not None:
        dump_state(bundle_output)
    if output_dir is not None:
        dump_state(output_dir / "initial.json")


def save_state() -> None:
    if _current_state is None:
        return
    _workspaces[_active_workspace_id] = _current_state
    _canonicalize_workspaces()


def _canonicalize_workspaces() -> None:
    """Validate all loaded workspaces after in-memory mutations."""
    global _current_state
    if not _workspaces:
        return
    validated = SlackWorkspacesState.model_validate(
        {
            "workspaces": {
                workspace_id: workspace.model_dump(mode="json", by_alias=True, exclude_none=True)
                for workspace_id, workspace in _workspaces.items()
            }
        }
    ).workspaces
    _workspaces.clear()
    _workspaces.update(validated)
    _current_state = _workspaces[_active_workspace_id]


def _restore_state(snapshot: SlackState) -> None:
    global _current_state
    _current_state = snapshot
    _workspaces[_active_workspace_id] = snapshot


def mutate_state[T](mutator: Callable[[SlackState], T]) -> T:
    state = get_state()
    snapshot = state.model_copy(deep=True)
    try:
        result = mutator(state)
        save_state()
    except Exception:
        _restore_state(snapshot)
        raise
    return result


def get_state() -> SlackState:
    if _current_state is None:
        return load_state()
    return _current_state


def reset_state() -> None:
    global _last_timestamp_us
    _install_workspaces(get_default_workspaces())
    _last_timestamp_us = 0
    save_state()


def get_active_workspace_id() -> str:
    _ensure_loaded()
    return _active_workspace_id


def set_active_workspace(workspace_id: str) -> None:
    global _active_workspace_id, _current_state
    _ensure_loaded()
    if workspace_id not in _workspaces:
        raise ValueError(f"Unknown Slack workspace {workspace_id!r}")
    _active_workspace_id = workspace_id
    _current_state = _workspaces[workspace_id]


def list_workspaces() -> dict[str, Any]:
    _ensure_loaded()
    workspaces = []
    for workspace_id, workspace in _workspaces.items():
        message_count = sum(len(messages) for messages in workspace.messages.values())
        workspaces.append(
            {
                "workspace_id": workspace_id,
                "is_active": workspace_id == _active_workspace_id,
                "user_count": len(workspace.users),
                "channel_count": len(workspace.channels),
                "message_count": message_count,
                "bot_user_id": workspace.bot_user_id,
                "is_admin": workspace.is_admin,
            }
        )
    return {"ok": True, "workspaces": workspaces, "total": len(workspaces)}


def get_bot_user_id() -> str:
    return get_state().bot_user_id or DEFAULT_BOT_USER_ID


def is_admin_mode() -> bool:
    return get_state().is_admin is True


def generate_timestamp() -> str:
    global _last_timestamp_us
    now_us = int(time.time() * 1_000_000)
    if now_us <= _last_timestamp_us:
        now_us = _last_timestamp_us + 1
    _last_timestamp_us = now_us
    seconds, micros = divmod(now_us, 1_000_000)
    return f"{seconds}.{micros:06d}"


def generate_channel_id() -> str:
    state = get_state()
    channel_id = f"C{state.counters.channelId:06d}"
    state.counters.channelId += 1
    return channel_id


def generate_mpim_channel_id() -> str:
    state = get_state()
    channel_id = f"G{state.counters.channelId:06d}"
    state.counters.channelId += 1
    return channel_id


def generate_file_id() -> str:
    state = get_state()
    state.counters.fileId += 1
    return f"F{state.counters.fileId:06d}"


def get_channel(channel_id: str):
    return get_state().channels.get(channel_id)


def get_user(user_id: str):
    return get_state().users.get(user_id)


def get_channel_messages(channel_id: str) -> list[SlackMessage]:
    return get_state().messages.get(channel_id, [])


def add_message(channel_id: str, message: SlackMessage | dict[str, Any]) -> None:
    new_message = SlackMessage.model_validate(message)

    def _add(state: SlackState) -> None:
        if channel_id not in state.messages:
            state.messages[channel_id] = []
        state.messages[channel_id].append(new_message)

    mutate_state(_add)


def _refresh_thread_metadata(messages: list[SlackMessage], parent_ts: str) -> None:
    parent = next((message for message in messages if message.ts == parent_ts), None)
    if parent is None:
        return
    replies = sorted(
        (message for message in messages if message.thread_ts == parent_ts and message.ts != parent_ts),
        key=lambda message: float(message.ts),
    )
    if not replies:
        parent.reply_count = None
        parent.reply_users = None
        parent.reply_users_count = None
        parent.latest_reply = None
        return
    reply_users = sorted({reply.user for reply in replies if reply.user is not None})
    parent.reply_count = len(replies)
    parent.reply_users = reply_users
    parent.reply_users_count = len(reply_users)
    parent.latest_reply = replies[-1].ts


def add_thread_reply(channel_id: str, parent_ts: str, message: SlackMessage | dict[str, Any]) -> None:
    new_message = SlackMessage.model_validate(message)

    def _add_reply(state: SlackState) -> None:
        messages = state.messages.setdefault(channel_id, [])
        messages.append(new_message)
        _refresh_thread_metadata(messages, parent_ts)

    mutate_state(_add_reply)


def find_message(channel_id: str, ts: str) -> SlackMessage | None:
    return next((message for message in get_channel_messages(channel_id) if message.ts == ts), None)


def delete_message(channel_id: str, ts: str) -> bool:
    messages = get_state().messages.get(channel_id)
    if not messages:
        return False
    if not any(message.ts == ts for message in messages):
        return False

    def _delete(state: SlackState) -> bool:
        messages = state.messages.get(channel_id)
        if not messages:
            return False
        target = next((message for message in messages if message.ts == ts), None)
        if target is None:
            return False
        parent_ts = target.thread_ts if target.thread_ts and target.thread_ts != target.ts else None
        state.messages[channel_id] = [message for message in messages if message.ts != ts and message.thread_ts != ts]
        if parent_ts is not None:
            _refresh_thread_metadata(state.messages[channel_id], parent_ts)
        return True

    return mutate_state(_delete)


def update_message(channel_id: str, ts: str, updater: Callable[[SlackMessage], None]) -> bool:
    if find_message(channel_id, ts) is None:
        return False

    def _update(state: SlackState) -> bool:
        message = next((message for message in state.messages.get(channel_id, []) if message.ts == ts), None)
        if message is None:
            return False
        updater(message)
        return True

    return mutate_state(_update)


def get_thread_replies(channel_id: str, thread_ts: str) -> list[SlackMessage]:
    messages = get_channel_messages(channel_id)
    parent = next((message for message in messages if message.ts == thread_ts), None)
    if parent is None:
        return []
    replies = [message for message in messages if message.thread_ts == thread_ts and message.ts != thread_ts]
    return sorted([parent, *replies], key=lambda message: float(message.ts))


def all_files(channel_id: str) -> list[SlackFile]:
    files: list[SlackFile] = []
    for message in get_channel_messages(channel_id):
        files.extend(message.files or [])
    return files
