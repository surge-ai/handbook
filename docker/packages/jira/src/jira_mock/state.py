"""State management for the Jira mock server."""

from __future__ import annotations

import json
import logging
import os
import re
from copy import deepcopy
from pathlib import Path
from typing import Any, cast

from jira_mock.models import JiraIssue, JiraMockState, JiraProject, JiraSitesState, JiraState, JiraStatus

_logger = logging.getLogger(__name__)

SERVICE_NAME = "jira"
_state_file: Path | None = None
_sites: dict[str, JiraState] = {}
_active_site_id = "default"
_current_state: JiraState | None = None
_bundle_state_path: Path | None = None
_final_path: Path | None = None
_UNSET = object()


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
    """Merge a flat site seed into ``target``: dicts update, lists extend, scalars overwrite."""
    for key, value in source.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            target[key].update(value)
        elif isinstance(value, list) and isinstance(target.get(key), list):
            target[key].extend(value)
        else:
            target[key] = value


def _coalesce_site_files(paths: list[Path]) -> dict[str, Any] | None:
    """Coalesce a folder of seed files into one seed dict.

    A single file passes through unchanged (flat single-site or ``{sites:
    {...}}`` wrapper). With multiple files, flat (non-wrapper) files are merged
    into ONE ``default`` site — the raw entities layout splits one site
    across per-entity files, and those belong together, not in separate sites.
    Files carrying an explicit ``{sites: {...}}`` wrapper contribute their
    named sites.
    """
    if not paths:
        return None
    if len(paths) == 1:
        # Single file: pass through unchanged so a flat seed stays flat and a
        # wrapper stays a wrapper (back-compat + canonical state.json).
        return json.loads(paths[0].read_text(encoding="utf-8"))
    sites: dict[str, Any] = {}
    default_site: dict[str, Any] = {}
    for path in paths:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and "sites" in data:
            sites.update(data["sites"])
        elif isinstance(data, dict):
            _merge_flat_into(default_site, data)
    if default_site:
        sites.setdefault("default", default_site)
    return {"sites": sites}


def resolve_bundle_output_path() -> Path | None:
    output_dir = os.environ.get("BUNDLE_OUTPUT_DIR")
    if not output_dir:
        return None
    return Path(output_dir) / "state.json"


def get_jira_state_path(agent_workspace: str) -> Path:
    return Path(agent_workspace).parent / "external_services" / "jira_state.json"


def set_agent_workspace(agent_workspace: str) -> None:
    global _current_state, _state_file
    _state_file = get_jira_state_path(agent_workspace)
    _state_file.parent.mkdir(parents=True, exist_ok=True)
    _sites.clear()
    _current_state = None
    _logger.info("Jira state file: %s", _state_file)


def set_snapshot_paths(
    *,
    final_path: Path | str | None | object = _UNSET,
    bundle_state_path: Path | str | None | object = _UNSET,
) -> None:
    global _bundle_state_path, _final_path
    if bundle_state_path is not _UNSET:
        _bundle_state_path = Path(cast(str | Path, bundle_state_path)) if bundle_state_path is not None else None
    if final_path is not _UNSET:
        _final_path = Path(cast(str | Path, final_path)) if final_path is not None else None


def configure_snapshots_from_env() -> None:
    bundle_state_path = resolve_bundle_output_path()
    final_path = Path(output_dir) / "final.json" if (output_dir := os.environ.get("OUTPUTDIR")) else None
    set_snapshot_paths(bundle_state_path=bundle_state_path, final_path=final_path)


def write_snapshots() -> None:
    if _bundle_state_path is not None:
        dump_state(_bundle_state_path, "bundle")
    if _final_path is not None:
        dump_state(_final_path, "final")


def _default_state_data() -> dict[str, Any]:
    return {
        "is_admin": False,
        "currentUserAccountId": "reporter-001",
        "users": {
            "reporter-001": {
                "accountId": "reporter-001",
                "accountType": "atlassian",
                "emailAddress": "reporter-001@example.com",
                "displayName": "Reporter User",
                "active": True,
                "timeZone": "America/New_York",
            },
            "creator-001": {
                "accountId": "creator-001",
                "accountType": "atlassian",
                "emailAddress": "creator-001@example.com",
                "displayName": "Creator User",
                "active": True,
                "timeZone": "America/New_York",
            },
            "commenter-001": {
                "accountId": "commenter-001",
                "accountType": "atlassian",
                "emailAddress": "commenter-001@example.com",
                "displayName": "Commenter User",
                "active": True,
                "timeZone": "America/New_York",
            },
            "worker-001": {
                "accountId": "worker-001",
                "accountType": "atlassian",
                "emailAddress": "worker-001@example.com",
                "displayName": "Worker User",
                "active": True,
                "timeZone": "America/New_York",
            },
            "uploader-001": {
                "accountId": "uploader-001",
                "accountType": "atlassian",
                "emailAddress": "uploader-001@example.com",
                "displayName": "Uploader User",
                "active": True,
                "timeZone": "America/New_York",
            },
            "user-1": {
                "accountId": "user-1",
                "accountType": "atlassian",
                "emailAddress": "user-1@example.com",
                "displayName": "User 1",
                "active": True,
                "timeZone": "America/New_York",
            },
        },
        "issues": {},
        "sprints": {},
        "comments": {},
        "worklogs": {},
        "projects": {
            "MOCK": {
                "id": "10001",
                "key": "MOCK",
                "name": "Mock Project",
                "description": "Default mock project",
                "projectTypeKey": "software",
                "simplified": False,
            },
            "TEST": {
                "id": "10002",
                "key": "TEST",
                "name": "Test Project",
                "description": "Default test project",
                "projectTypeKey": "software",
                "simplified": False,
            },
        },
        "boards": {
            "1000": {
                "id": 1000,
                "name": "Mock Scrum Board",
                "type": "scrum",
                "projectKey": "MOCK",
                "filterJql": "project = MOCK",
            },
            "1001": {
                "id": 1001,
                "name": "Test Scrum Board",
                "type": "scrum",
                "projectKey": "TEST",
                "filterJql": "project = TEST",
            },
        },
        "fields": [
            {"id": "summary", "key": "summary", "name": "Summary", "custom": False, "searchable": True},
            {"id": "description", "key": "description", "name": "Description", "custom": False, "searchable": True},
            {"id": "status", "key": "status", "name": "Status", "custom": False, "searchable": True},
            {"id": "priority", "key": "priority", "name": "Priority", "custom": False, "searchable": True},
            {"id": "assignee", "key": "assignee", "name": "Assignee", "custom": False, "searchable": True},
            {"id": "reporter", "key": "reporter", "name": "Reporter", "custom": False, "searchable": True},
            {"id": "created", "key": "created", "name": "Created", "custom": False, "searchable": True},
            {"id": "updated", "key": "updated", "name": "Updated", "custom": False, "searchable": True},
            {"id": "labels", "key": "labels", "name": "Labels", "custom": False, "searchable": True},
            {"id": "issuetype", "key": "issuetype", "name": "Issue Type", "custom": False, "searchable": True},
            {
                "id": "customfield_10001",
                "key": "customfield_10001",
                "name": "Story Points",
                "custom": True,
                "searchable": True,
            },
            {
                "id": "customfield_10002",
                "key": "customfield_10002",
                "name": "Sprint",
                "custom": True,
                "searchable": True,
            },
            {
                "id": "customfield_10003",
                "key": "customfield_10003",
                "name": "Epic Link",
                "custom": True,
                "searchable": True,
            },
        ],
        "linkTypes": [
            {"id": "10001", "name": "Blocks", "inward": "is blocked by", "outward": "blocks"},
            {"id": "10002", "name": "Cloners", "inward": "is cloned by", "outward": "clones"},
            {"id": "10003", "name": "Duplicate", "inward": "is duplicated by", "outward": "duplicates"},
            {"id": "10004", "name": "Relates", "inward": "relates to", "outward": "relates to"},
        ],
        "defaultStatusValue": "To Do",
        "statuses": {
            "10001": {
                "id": "10001",
                "name": "To Do",
                "description": "Issue is open and not yet started",
                "statusCategory": {"id": 2, "key": "new", "name": "To Do", "colorName": "blue-gray"},
            },
            "10002": {
                "id": "10002",
                "name": "In Progress",
                "description": "Issue is actively being worked on",
                "statusCategory": {"id": 4, "key": "indeterminate", "name": "In Progress", "colorName": "yellow"},
            },
            "10003": {
                "id": "10003",
                "name": "In Review",
                "description": "Issue is being reviewed",
                "statusCategory": {"id": 4, "key": "indeterminate", "name": "In Progress", "colorName": "yellow"},
            },
            "10004": {
                "id": "10004",
                "name": "Done",
                "description": "Issue is complete",
                "statusCategory": {"id": 3, "key": "done", "name": "Done", "colorName": "green"},
            },
        },
        "workflow": {
            "To Do": [{"id": "1", "name": "Start Progress", "to": "In Progress"}],
            "In Progress": [
                {"id": "2", "name": "Submit for Review", "to": "In Review"},
                {"id": "3", "name": "Mark Done", "to": "Done"},
                {"id": "4", "name": "Back to To Do", "to": "To Do"},
            ],
            "In Review": [
                {"id": "5", "name": "Approve", "to": "Done"},
                {"id": "6", "name": "Request Changes", "to": "In Progress"},
            ],
            "Done": [{"id": "7", "name": "Reopen", "to": "To Do"}],
        },
        "counters": {
            "issueId": 10000,
            "sprintId": 1000,
            "boardId": 1001,
            "commentId": 0,
            "worklogId": 0,
            "attachmentId": 0,
            "issueLinkId": 0,
        },
    }


def get_default_state() -> JiraState:
    return JiraState.model_validate(deepcopy(_default_state_data()))


def get_default_sites() -> dict[str, JiraState]:
    return {"default": get_default_state()}


def _canonicalize_state(state: JiraState) -> JiraState:
    return JiraState.model_validate(state.model_dump(mode="json", by_alias=True, exclude_none=True))


def state_to_json() -> dict[str, Any]:
    _ensure_loaded()
    return _storage_from_sites(_sites)


def _max_attachment_id(state: JiraState) -> int:
    max_id = state.counters.attachmentId
    for issue in state.issues.values():
        for attachment in issue.fields.attachment or []:
            data = attachment.model_dump(mode="json", by_alias=True) if not isinstance(attachment, dict) else attachment
            try:
                value = data.get("id")
                if value is not None:
                    max_id = max(max_id, int(value))
            except (TypeError, ValueError):
                continue
    return max_id


def _max_issue_id(state: JiraState) -> int:
    max_id = state.counters.issueId
    for issue in state.issues.values():
        try:
            max_id = max(max_id, int(issue.id))
        except (TypeError, ValueError):
            continue
    return max_id


def _max_sprint_id(state: JiraState) -> int:
    max_id = state.counters.sprintId
    for key, sprint in state.sprints.items():
        max_id = max(max_id, sprint.id)
        try:
            max_id = max(max_id, int(key))
        except (TypeError, ValueError):
            continue
    return max_id


def _max_board_id(state: JiraState) -> int:
    max_id = state.counters.boardId
    for key, board in state.boards.items():
        max_id = max(max_id, board.id)
        try:
            max_id = max(max_id, int(key))
        except (TypeError, ValueError):
            continue
    return max_id


def _max_comment_id(state: JiraState) -> int:
    max_id = state.counters.commentId
    for comments in state.comments.values():
        for comment in comments:
            try:
                max_id = max(max_id, int(comment.id))
            except (TypeError, ValueError):
                continue
    return max_id


def _max_worklog_id(state: JiraState) -> int:
    max_id = state.counters.worklogId
    for worklogs in state.worklogs.values():
        for worklog in worklogs:
            try:
                max_id = max(max_id, int(worklog.id))
            except (TypeError, ValueError):
                continue
    return max_id


def _max_issue_link_id(state: JiraState) -> int:
    max_id = state.counters.issueLinkId
    for issue in state.issues.values():
        for link in issue.fields.issuelinks or []:
            try:
                max_id = max(max_id, int(link.id))
            except (TypeError, ValueError):
                continue
    return max_id


def _max_project_id(state: JiraState) -> int:
    max_id = 10000
    for project in state.projects.values():
        try:
            max_id = max(max_id, int(project.id))
        except (TypeError, ValueError):
            continue
    return max_id


def _next_project_id(state: JiraState) -> str:
    return str(_max_project_id(state) + 1)


def _max_issue_key_suffix(state: JiraState, project_key: str) -> int:
    pattern = re.compile(rf"^{re.escape(project_key)}-(\d+)$")
    max_suffix = 0
    for key in state.issues:
        if match := pattern.fullmatch(key):
            max_suffix = max(max_suffix, int(match.group(1)))
    return max_suffix


def _collect_embedded_users(data: dict[str, Any]) -> dict[str, Any]:
    users: dict[str, Any] = {}

    def add_user(value: Any) -> None:
        if isinstance(value, dict) and isinstance(value.get("accountId"), str):
            users.setdefault(value["accountId"], value)

    for issue in (data.get("issues") or {}).values():
        if not isinstance(issue, dict):
            continue
        fields = issue.get("fields") or {}
        if not isinstance(fields, dict):
            continue
        for field in ("assignee", "reporter", "creator"):
            add_user(fields.get(field))
        for component in fields.get("components") or []:
            if isinstance(component, dict):
                add_user(component.get("lead"))
        for attachment in fields.get("attachment") or []:
            if isinstance(attachment, dict):
                add_user(attachment.get("author"))
        watches = fields.get("watches") or {}
        if isinstance(watches, dict):
            for watcher in watches.get("watchers") or []:
                add_user(watcher)
        changelog = issue.get("changelog") or {}
        if isinstance(changelog, dict):
            for history in changelog.get("histories") or []:
                if isinstance(history, dict):
                    add_user(history.get("author"))

    for comments in (data.get("comments") or {}).values():
        for comment in comments or []:
            if isinstance(comment, dict):
                add_user(comment.get("author"))
                add_user(comment.get("updateAuthor"))

    for worklogs in (data.get("worklogs") or {}).values():
        for worklog in worklogs or []:
            if isinstance(worklog, dict):
                add_user(worklog.get("author"))
                add_user(worklog.get("updateAuthor"))

    return users


def _state_from_flat_json(data: dict[str, Any] | JiraState | None) -> JiraState:
    defaults = get_default_state().model_dump(mode="json", by_alias=True)
    incoming = data.model_dump(mode="json", by_alias=True) if isinstance(data, JiraState) else (data or {})
    embedded_users = _collect_embedded_users(incoming)
    explicit_users = "users" in incoming
    users = (
        {**embedded_users, **incoming.get("users", {})} if explicit_users else {**defaults["users"], **embedded_users}
    )
    current_user_account_id = incoming.get("currentUserAccountId")
    if current_user_account_id is None:
        current_user_account_id = next(iter(users), None) if explicit_users else defaults["currentUserAccountId"]
    if current_user_account_id is None:
        raise ValueError("Jira state requires at least one user or a currentUserAccountId")

    merged = {
        "is_admin": incoming.get("is_admin", defaults["is_admin"]),
        "currentUserAccountId": current_user_account_id,
        "users": users,
        "issues": incoming.get("issues", defaults["issues"]),
        "sprints": incoming.get("sprints", defaults["sprints"]),
        "comments": incoming.get("comments", defaults["comments"]),
        "worklogs": incoming.get("worklogs", defaults["worklogs"]),
        "projects": incoming.get("projects", defaults["projects"]),
        "boards": incoming.get("boards", defaults["boards"]),
        "fields": incoming.get("fields", defaults["fields"]),
        "linkTypes": incoming.get("linkTypes", defaults["linkTypes"]),
        "defaultStatusValue": incoming.get("defaultStatusValue") or defaults["defaultStatusValue"],
        "statuses": incoming.get("statuses") or defaults["statuses"],
        "workflow": incoming.get("workflow") or defaults["workflow"],
        "counters": {**defaults["counters"], **incoming.get("counters", {})},
    }
    state = JiraState.model_validate(merged)
    state.counters.issueId = _max_issue_id(state)
    state.counters.sprintId = _max_sprint_id(state)
    state.counters.boardId = _max_board_id(state)
    state.counters.commentId = _max_comment_id(state)
    state.counters.worklogId = _max_worklog_id(state)
    state.counters.attachmentId = _max_attachment_id(state)
    state.counters.issueLinkId = _max_issue_link_id(state)
    return _canonicalize_state(state)


def _sites_from_storage(data: dict[str, Any] | JiraMockState | None) -> dict[str, JiraState]:
    if isinstance(data, JiraSitesState):
        raw_sites = data.model_dump(mode="json", by_alias=True, exclude_unset=True).get("sites", {})
    elif isinstance(data, JiraState):
        raw_sites = {"default": data.model_dump(mode="json", by_alias=True, exclude_unset=True)}
    else:
        raw_data = data or {}
        raw_sites = raw_data.get("sites", {"default": raw_data})

    sites: dict[str, JiraState] = {}
    for site_id, site_state in raw_sites.items():
        sites[site_id] = _state_from_flat_json(site_state)
    if not sites:
        raise ValueError("Jira state must contain at least one site")
    return sites


def _storage_from_sites(sites: dict[str, JiraState]) -> dict[str, Any]:
    if set(sites) == {"default"}:
        return sites["default"].model_dump(mode="json", by_alias=True, exclude_none=True)
    return {
        "sites": {
            site_id: site.model_dump(mode="json", by_alias=True, exclude_none=True) for site_id, site in sites.items()
        }
    }


def _install_sites(sites: dict[str, JiraState]) -> None:
    global _active_site_id, _current_state
    _sites.clear()
    _sites.update(sites)
    _active_site_id = "default" if "default" in _sites else next(iter(_sites))
    _current_state = _sites[_active_site_id]


def _ensure_loaded() -> None:
    if _current_state is None:
        load_state()


def state_from_json(data: dict[str, Any] | JiraMockState | None) -> None:
    sites = _sites_from_storage(data)
    _install_sites(sites)
    save_state()


def dump_state(dest: Path, label: str) -> None:
    if _current_state is None and not _sites:
        return
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(json.dumps(state_to_json(), indent=2), encoding="utf-8")
        _logger.info("Wrote Jira %s state to %s", label, dest)
    except Exception:
        _logger.exception("Failed to write Jira %s state to %s", label, dest)


def load_state() -> JiraState:
    if _current_state is not None:
        return _current_state

    seed: dict[str, Any] | None = None
    bundle_paths = resolve_bundle_state_paths()
    if bundle_paths:
        seed = _coalesce_site_files(bundle_paths)
        _logger.info("Loaded state from bundle: %s", [str(p) for p in bundle_paths])

    if seed is None and (input_dir := os.environ.get("INPUTDIR")):
        json_files = sorted(Path(input_dir).glob("*.json"))
        if json_files:
            seed = json.loads(json_files[0].read_text(encoding="utf-8"))
            _logger.info("Loaded state from INPUTDIR: %s", json_files[0])

    if seed is None and _state_file is not None and _state_file.exists():
        seed = json.loads(_state_file.read_text(encoding="utf-8"))
        _logger.info("Loaded state from %s", _state_file)

    state_from_json(seed or _default_state_data())

    return get_state()


def init_state() -> None:
    load_state()
    configure_snapshots_from_env()
    if _bundle_state_path is not None:
        dump_state(_bundle_state_path, "bundle")
    if output_dir := os.environ.get("OUTPUTDIR"):
        dump_state(Path(output_dir) / "initial.json", "initial")


def save_state() -> None:
    if _current_state is None:
        return
    _sites[_active_site_id] = _current_state
    _validate_sites()
    payload = _storage_from_sites(_sites)
    if _state_file is None:
        return
    _state_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _validate_sites() -> None:
    if not _sites:
        return
    JiraSitesState.model_validate(
        {
            "sites": {
                site_id: site.model_dump(mode="json", by_alias=True, exclude_none=True)
                for site_id, site in _sites.items()
            }
        }
    )


def get_state() -> JiraState:
    if _current_state is None:
        return load_state()
    return _current_state


def reset_state() -> None:
    _install_sites(get_default_sites())
    save_state()


def get_active_site_id() -> str:
    _ensure_loaded()
    return _active_site_id


def set_active_site(site_id: str) -> None:
    global _active_site_id, _current_state
    _ensure_loaded()
    if site_id not in _sites:
        raise ValueError(f"Unknown Jira site {site_id!r}")
    _active_site_id = site_id
    _current_state = _sites[site_id]


def list_sites() -> dict[str, Any]:
    _ensure_loaded()
    sites = []
    for site_id, site in _sites.items():
        sites.append(
            {
                "site_id": site_id,
                "is_active": site_id == _active_site_id,
                "project_count": len(site.projects),
                "issue_count": len(site.issues),
                "user_count": len(site.users),
                "board_count": len(site.boards),
                "current_user_account_id": site.currentUserAccountId,
                "is_admin": site.is_admin,
            }
        )
    return {"status": "success", "sites": sites, "total": len(sites)}


def _next_counter(name: str) -> int:
    state = get_state()
    value = getattr(state.counters, name) + 1
    setattr(state.counters, name, value)
    save_state()
    return value


def get_next_issue_id() -> int:
    return _next_counter("issueId")


def get_next_sprint_id() -> int:
    return _next_counter("sprintId")


def get_next_board_id() -> int:
    return _next_counter("boardId")


def get_next_comment_id() -> int:
    return _next_counter("commentId")


def get_next_worklog_id() -> int:
    return _next_counter("worklogId")


def get_next_attachment_id() -> int:
    return _next_counter("attachmentId")


def get_next_issue_link_id() -> int:
    return _next_counter("issueLinkId")


def get_or_create_project(project_key: str) -> JiraProject:
    state = get_state()
    if project_key not in state.projects:
        state.projects[project_key] = JiraProject.model_validate(
            {
                "id": _next_project_id(state),
                "key": project_key,
                "name": f"{project_key} Project",
                "description": f"Auto-created project for {project_key}",
                "projectTypeKey": "software",
                "simplified": False,
            }
        )
        save_state()
    return state.projects[project_key]


def generate_issue_key(project_key: str) -> str:
    return f"{project_key}-{_max_issue_key_suffix(get_state(), project_key) + 1}"


def is_admin_mode() -> bool:
    return get_state().is_admin is True


def get_status_by_name(name: str) -> JiraStatus | None:
    return next((status for status in get_state().statuses.values() if status.name.lower() == name.lower()), None)


def get_transitions_for_issue(issue: JiraIssue) -> list[dict[str, Any]]:
    state = get_state()
    return [
        {
            "id": transition.id,
            "name": transition.name,
            "to": (get_status_by_name(transition.to) or JiraStatus(id="0", name=transition.to)).model_dump(
                mode="json", by_alias=True
            ),
            "hasScreen": False,
            "isGlobal": False,
            "isInitial": False,
            "isAvailable": True,
            "isConditional": False,
            "isLooped": False,
        }
        for transition in state.workflow.get(issue.fields.status.name, [])
    ]
