"""Shared helpers for Jira tool handlers."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any

from pydantic import Field

from jira_mock.models import IssueKey as JiraIssueKey
from jira_mock.models import (
    JiraComponent,
    JiraIssue,
    JiraIssueLinkType,
    JiraLinkedIssue,
    JiraSiteId,
    JiraUser,
    JiraWatches,
    NumericIdString,
    ProjectKey,
)
from jira_mock.state import (
    generate_issue_key,
    get_next_issue_id,
    get_or_create_project,
    get_state,
    get_status_by_name,
    is_admin_mode,
    save_state,
)

DEFAULT_READ_JIRA_FIELDS = "summary,status,assignee,issuetype,priority,created,updated,description,labels"

IssueKey = Annotated[JiraIssueKey, Field(description="Jira issue key, for example MOCK-1")]
ProjectKeyArg = Annotated[ProjectKey, Field(description="Jira project key, for example MOCK")]
BoardIdArg = Annotated[NumericIdString, Field(description="Jira board ID, for example 1000")]
LimitArg = Annotated[int, Field(default=10, ge=0, le=50, description="Maximum number of results")]
SprintIdArg = Annotated[NumericIdString, Field(description="Jira sprint ID, for example 1001")]
StartAtArg = Annotated[int, Field(default=0, ge=0, description="Starting index for pagination")]
TransitionIdArg = Annotated[NumericIdString, Field(description="Jira workflow transition ID")]
SiteIdArg = Annotated[JiraSiteId, Field(description="Jira site ID. Defaults to the default site.")]


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _dump(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json", by_alias=True, exclude_none=True)
    if isinstance(value, list):
        return [_dump(item) for item in value]
    if isinstance(value, dict):
        return {key: _dump(item) for key, item in value.items() if item is not None}
    return value


def _require_user(account_id: str) -> JiraUser:
    user = get_state().users.get(account_id)
    if user is None:
        raise ValueError(f"User {account_id} not found")
    return user


def _current_user() -> JiraUser:
    account_id = get_state().currentUserAccountId
    if account_id is None:
        raise ValueError("No current Jira user is configured")
    return _require_user(account_id)


def _resolve_user_ref(value: str | dict[str, Any] | JiraUser) -> JiraUser:
    if isinstance(value, str):
        return _require_user(value)
    user = JiraUser.model_validate(value)
    return _require_user(user.accountId)


def _linked_issue(issue: JiraIssue) -> JiraLinkedIssue:
    return JiraLinkedIssue.model_validate(
        {
            "id": issue.id,
            "key": issue.key,
            "fields": {
                "summary": issue.fields.summary,
                "status": _dump(issue.fields.status),
                "issuetype": _dump(issue.fields.issuetype),
            },
        }
    )


def _resolve_link_type(link_type: str) -> tuple[JiraIssueLinkType, bool]:
    lowered = link_type.lower()
    for candidate in get_state().linkTypes:
        if lowered in {candidate.name.lower(), candidate.outward.lower()}:
            return candidate, False
        if lowered == candidate.inward.lower():
            return candidate, True
    valid_types = sorted({value for item in get_state().linkTypes for value in (item.name, item.inward, item.outward)})
    raise ValueError(f"Unknown link type: {link_type}. Valid types: {', '.join(valid_types)}")


def _parse_components(components: str) -> list[JiraComponent]:
    names = [name.strip() for name in components.split(",") if name.strip()]
    return [JiraComponent(name=name) for name in names]


def _adf(text: str) -> dict[str, Any]:
    return {
        "type": "doc",
        "version": 1,
        "content": [{"type": "paragraph", "content": [{"type": "text", "text": text}]}],
    }


def _adf_to_text(node: Any) -> str:
    if node is None:
        return ""
    if isinstance(node, str):
        return node
    if hasattr(node, "model_dump"):
        node = node.model_dump(mode="json", by_alias=True)
    if not isinstance(node, dict):
        return ""
    if isinstance(node.get("text"), str):
        return node["text"]
    if isinstance(node.get("content"), list):
        return " ".join(_adf_to_text(item) for item in node["content"])
    return ""


def _field_dict(issue: JiraIssue) -> dict[str, Any]:
    return issue.fields.model_dump(mode="json", by_alias=True, exclude_none=True)


def _requested_fields(fields: str | None) -> set[str] | None:
    if fields is None:
        return None
    requested = {field.strip() for field in fields.split(",") if field.strip()}
    if not requested or "*all" in requested:
        return None
    return requested


def _dump_issue(
    issue: JiraIssue, fields: str | None = None, extra_fields: dict[str, Any] | None = None
) -> dict[str, Any]:
    response = _dump(issue)
    requested = _requested_fields(fields)
    response_fields = response.setdefault("fields", {})
    if extra_fields:
        response_fields.update(extra_fields)
    if requested is not None:
        response["fields"] = {key: value for key, value in response_fields.items() if key in requested}
    return response


def _dump_issues(issues: list[JiraIssue], fields: str | None = None) -> list[dict[str, Any]]:
    return [_dump_issue(issue, fields) for issue in issues]


def _field(issue: JiraIssue, key: str, default: Any = None) -> Any:
    if hasattr(issue.fields, key):
        return getattr(issue.fields, key)
    return (issue.fields.model_extra or {}).get(key, default)


def _set_field(issue: JiraIssue, key: str, value: Any) -> None:
    setattr(issue.fields, key, value)


def require_admin() -> None:
    if not is_admin_mode():
        raise PermissionError("Admin privileges are required to configure Jira statuses, workflows, or boards")


def create_new_issue(
    project_key: ProjectKey, summary: str, issue_type: str, description: str | None = None, assignee: str | None = None
) -> JiraIssue:
    state = get_state()
    project = get_or_create_project(project_key)
    issue_key = generate_issue_key(project_key)
    issue_id = get_next_issue_id()
    now = _now()
    default_status = get_status_by_name(state.defaultStatusValue)
    if default_status is None:
        raise ValueError(f"Default status {state.defaultStatusValue!r} is not configured")
    if default_status.name not in state.workflow:
        raise ValueError(f"Default status {default_status.name!r} does not have a workflow entry")
    issue = JiraIssue.model_validate(
        {
            "id": str(issue_id),
            "key": issue_key,
            "fields": {
                "summary": summary,
                "description": _adf(description) if description else None,
                "issuetype": {
                    "id": "10001",
                    "name": issue_type,
                    "description": f"A {issue_type.lower()}",
                    "subtask": issue_type.lower() == "subtask",
                    "hierarchyLevel": 1 if issue_type.lower() == "epic" else 0,
                },
                "project": project.model_dump(mode="json", by_alias=True),
                "status": default_status.model_dump(mode="json", by_alias=True),
                "priority": {"id": "3", "name": "Medium"},
                "assignee": _dump(_require_user(assignee)) if assignee else None,
                "reporter": _dump(_current_user()),
                "creator": _dump(_current_user()),
                "created": now,
                "updated": now,
                "labels": [],
                "components": [],
                "fixVersions": [],
                "versions": [],
            },
        }
    )
    state.issues[issue_key] = issue
    save_state()
    return issue


def ensure_watches(issue: JiraIssue) -> JiraWatches:
    issue.fields.watches = issue.fields.watches or JiraWatches()
    return issue.fields.watches
