"""Issue and JQL tool handlers."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Annotated, Any

from pydantic import Field

from jira_mock.models import (
    IssueTypeName,
    JiraCommentPage,
    JiraDocumentContent,
    JiraIssue,
    JiraIssueLink,
    JiraPriority,
    JiraWorklogPage,
)
from jira_mock.state import get_next_issue_link_id, get_state, save_state
from jira_mock.tools.common import (
    DEFAULT_READ_JIRA_FIELDS,
    IssueKey,
    LimitArg,
    ProjectKeyArg,
    StartAtArg,
    _adf,
    _adf_to_text,
    _dump,
    _dump_issue,
    _dump_issues,
    _field,
    _field_dict,
    _linked_issue,
    _now,
    _parse_components,
    _requested_fields,
    _resolve_link_type,
    _resolve_user_ref,
    create_new_issue,
)


def _user_matches(user: Any, filters: list[str]) -> bool:
    if user is None:
        return False
    data = _dump(user)
    if not isinstance(data, dict):
        return False
    account_id = str(data.get("accountId", "")).lower()
    display_name = str(data.get("displayName", "")).lower()
    email = str(data.get("emailAddress", "")).lower()
    current_account_id = get_state().currentUserAccountId
    normalized_filters = [
        current_account_id.lower() if value.lower() == "currentuser()" and current_account_id else value
        for value in filters
        if value.lower() != "currentuser()" or current_account_id
    ]
    return any(account_id == value or value in display_name or value in email for value in normalized_filters)


def _normalize_jql_value(value: str) -> str:
    return value.strip().strip("'\"").strip()


def _parse_list_values(raw: str) -> list[str]:
    values: list[str] = []
    for match in re.finditer(r'"([^"]+)"|\'([^\']+)\'|([^,\s][^,]*)', raw):
        value = _normalize_jql_value(next(group for group in match.groups() if group is not None))
        if value:
            values.append(value)
    return values


def _parse_query_tokens(query: str) -> list[str]:
    return [
        (match.group(1) or match.group(0)).strip().lower()
        for match in re.finditer(r'"([^"]+)"|\S+', query)
        if (match.group(1) or match.group(0)).strip()
    ]


def _all_tokens_match(query: str, haystack: str) -> bool:
    tokens = _parse_query_tokens(query)
    lowered = haystack.lower()
    return bool(tokens) and all(token in lowered for token in tokens)


def _extract_contains(jql: str, field: str) -> str | None:
    match = re.search(rf"\b{field}\s*~\s*(?:\"([^\"]+)\"|'([^']+)'|(\S+))", jql, re.IGNORECASE)
    if not match:
        return None
    return _normalize_jql_value(next(group for group in match.groups() if group is not None))


def _find_top_level_order_by_index(jql: str) -> int | None:
    depth = 0
    quote: str | None = None
    for i, char in enumerate(jql):
        if quote:
            if char == quote:
                quote = None
            continue
        if char in {"'", '"'}:
            quote = char
            continue
        if char == "(":
            depth += 1
            continue
        if char == ")":
            depth = max(0, depth - 1)
            continue
        if (
            depth == 0
            and re.match(r"order\s+by\b", jql[i:], re.IGNORECASE)
            and (i == 0 or not re.match(r"[A-Za-z0-9_]", jql[i - 1]))
        ):
            return i
    return None


def _strip_order_by(jql: str) -> str:
    index = _find_top_level_order_by_index(jql)
    return (jql if index is None else jql[:index]).strip()


def _parse_order_by(jql: str) -> tuple[str, str] | None:
    index = _find_top_level_order_by_index(jql)
    if index is None:
        return None
    match = re.match(r"order\s+by\s+(\w+)(?:\s+(asc|desc))?", jql[index:], re.IGNORECASE)
    if not match:
        return None
    return match.group(1).lower(), (match.group(2) or "asc").lower()


def _split_top_level_or_clauses(jql: str) -> list[str]:
    source = _strip_order_by(jql)
    clauses: list[str] = []
    start = 0
    depth = 0
    quote: str | None = None
    i = 0
    while i < len(source):
        char = source[i]
        if quote:
            if char == quote:
                quote = None
            i += 1
            continue
        if char in {"'", '"'}:
            quote = char
            i += 1
            continue
        if char == "(":
            depth += 1
            i += 1
            continue
        if char == ")":
            depth = max(0, depth - 1)
            i += 1
            continue
        if (
            depth == 0
            and source[i : i + 2].lower() == "or"
            and (i == 0 or not re.match(r"[A-Za-z0-9_]", source[i - 1]))
            and (i + 2 >= len(source) or not re.match(r"[A-Za-z0-9_]", source[i + 2]))
        ):
            if clause := source[start:i].strip():
                clauses.append(clause)
            start = i + 2
            i += 2
            continue
        i += 1
    if tail := source[start:].strip():
        clauses.append(tail)
    return clauses or [source]


def _has_parenthesized_or(jql: str) -> bool:
    source = _strip_order_by(jql)
    depth = 0
    quote: str | None = None
    for i, char in enumerate(source):
        if quote:
            if char == quote:
                quote = None
            continue
        if char in {"'", '"'}:
            quote = char
            continue
        if char == "(":
            depth += 1
            continue
        if char == ")":
            depth = max(0, depth - 1)
            continue
        if (
            depth > 0
            and source[i : i + 2].lower() == "or"
            and (i == 0 or not re.match(r"[A-Za-z0-9_]", source[i - 1]))
            and (i + 2 >= len(source) or not re.match(r"[A-Za-z0-9_]", source[i + 2]))
        ):
            return True
    return False


FIELD_ALIASES: dict[str, list[str]] = {
    "key": ["key", "issuekey"],
    "project": ["project"],
    "status": ["status"],
    "assignee": ["assignee"],
    "reporter": ["reporter"],
    "priority": ["priority"],
    "issueType": ["issuetype", "type"],
    "labels": ["labels", "label"],
    "components": ["components", "component"],
    "sprint": ["sprint", "customfield_10002"],
    "fixVersion": ["fixVersions", "fixVersion"],
    "statusCategory": ["statusCategory"],
    "resolution": ["resolution"],
    "parent": ["parent"],
    "due": ["due", "duedate"],
}


def _field_pattern(names: list[str]) -> str:
    return "|".join(re.escape(name) for name in names)


def _get_field_condition(jql: str, names: list[str]) -> dict[str, Any] | None:
    fields = _field_pattern(names)
    if match := re.search(rf"\b(?:{fields})\b\s+is\s+(not\s+)?(?:empty|null)\b", jql, re.IGNORECASE):
        return {"values": [], "negate": False, "empty": not match.group(1)}
    if match := re.search(rf"\b(?:{fields})\b\s+(not\s+in|in)\s*\(([^)]*)\)", jql, re.IGNORECASE):
        values = _parse_list_values(match.group(2))
        return {"values": values, "negate": match.group(1).lower().startswith("not"), "empty": None}
    if match := re.search(rf"\b(?:{fields})\b\s*(!=|=)\s*(?:\"([^\"]+)\"|'([^']+)'|(\S+))", jql, re.IGNORECASE):
        value = _normalize_jql_value(next(group for group in match.groups()[1:] if group is not None))
        return {
            "values": [value] if value and value.lower() not in {"empty", "null"} else [],
            "negate": match.group(1) == "!=",
            "empty": True if value.lower() in {"empty", "null"} else None,
        }
    return None


def _apply_condition(
    issues: list[JiraIssue],
    condition: dict[str, Any] | None,
    value_matches: Callable[[JiraIssue, list[str]], bool],
    is_empty: Callable[[JiraIssue], bool],
) -> list[JiraIssue]:
    if not condition:
        return issues
    if condition["empty"] is not None:
        return [issue for issue in issues if (is_empty(issue) == condition["empty"]) != condition["negate"]]
    filters = [value.lower() for value in condition["values"]]
    return [issue for issue in issues if value_matches(issue, filters) != condition["negate"]]


def _parse_date_bound(value: str) -> int | None:
    normalized = _normalize_jql_value(value)
    if not normalized:
        return None
    normalized = normalized.replace("Z", "+00:00")
    try:
        return int(datetime.fromisoformat(normalized).timestamp() * 1000)
    except ValueError:
        try:
            return int(datetime.strptime(normalized, "%Y-%m-%d").replace(tzinfo=UTC).timestamp() * 1000)
        except ValueError:
            return None


def _apply_date_filters(
    issues: list[JiraIssue], jql: str, names: list[str], get_value: Callable[[JiraIssue], str | None]
) -> list[JiraIssue]:
    filtered = issues
    regex = re.compile(
        rf"\b(?:{_field_pattern(names)})\b\s*(<=|>=|<|>|=)\s*(?:\"([^\"]+)\"|'([^']+)'|(\S+))", re.IGNORECASE
    )
    for match in regex.finditer(jql):
        op = match.group(1)
        raw = next(group for group in match.groups()[1:] if group is not None)
        bound = _parse_date_bound(raw)
        if bound is None:
            return []
        if op == "=" and re.fullmatch(r"\d{4}-\d{2}-\d{2}", _normalize_jql_value(raw)):
            end = bound + 24 * 3600 * 1000
            filtered = [
                issue
                for issue in filtered
                if (time_value := _parse_date_bound(get_value(issue) or "")) is not None and bound <= time_value < end
            ]
        else:
            filtered = [
                issue
                for issue in filtered
                if (time_value := _parse_date_bound(get_value(issue) or "")) is not None
                and (
                    (op == "<" and time_value < bound)
                    or (op == "<=" and time_value <= bound)
                    or (op == ">" and time_value > bound)
                    or (op == ">=" and time_value >= bound)
                    or (op == "=" and time_value == bound)
                )
            ]
    return filtered


def _collect_sprint_field_names() -> list[str]:
    configured: list[str] = []
    for field in get_state().fields:
        if field.name.lower() == "sprint" or field.key.lower() == "sprint":
            configured.extend([field.key, field.id])
    return list(dict.fromkeys([*FIELD_ALIASES["sprint"], *configured]))


def _apply_jql_filters(initial_issues: list[JiraIssue], jql: str) -> list[JiraIssue]:
    issues = initial_issues
    issues = _apply_condition(
        issues,
        _get_field_condition(jql, FIELD_ALIASES["key"]),
        lambda i, f: i.key.lower() in f,
        lambda _i: False,
    )
    issues = _apply_condition(
        issues,
        _get_field_condition(jql, FIELD_ALIASES["project"]),
        lambda i, f: i.fields.project.key.lower() in f,
        lambda _i: False,
    )
    issues = _apply_condition(
        issues,
        _get_field_condition(jql, FIELD_ALIASES["status"]),
        lambda i, f: i.fields.status.name.lower() in f,
        lambda _i: False,
    )
    issues = _apply_condition(
        issues,
        _get_field_condition(jql, FIELD_ALIASES["assignee"]),
        lambda i, f: _user_matches(i.fields.assignee, f),
        lambda i: i.fields.assignee is None,
    )
    issues = _apply_condition(
        issues,
        _get_field_condition(jql, FIELD_ALIASES["reporter"]),
        lambda i, f: _user_matches(i.fields.reporter, f),
        lambda i: i.fields.reporter is None,
    )
    issues = _apply_condition(
        issues,
        _get_field_condition(jql, FIELD_ALIASES["priority"]),
        lambda i, f: (
            str(_dump(i.fields.priority).get("name", "") if isinstance(_dump(i.fields.priority), dict) else "").lower()
            in f
        ),
        lambda i: i.fields.priority is None,
    )
    issues = _apply_condition(
        issues,
        _get_field_condition(jql, FIELD_ALIASES["issueType"]),
        lambda i, f: i.fields.issuetype.name.lower() in f,
        lambda _i: False,
    )
    issues = _apply_condition(
        issues,
        _get_field_condition(jql, FIELD_ALIASES["labels"]),
        lambda i, f: any(label.lower() in f for label in i.fields.labels or []),
        lambda i: not i.fields.labels,
    )
    issues = _apply_condition(
        issues,
        _get_field_condition(jql, FIELD_ALIASES["components"]),
        lambda i, f: any(str(_dump(component).get("name", "")).lower() in f for component in i.fields.components or []),
        lambda i: not i.fields.components,
    )
    sprint_fields = _collect_sprint_field_names()
    issues = _apply_condition(
        issues,
        _get_field_condition(jql, sprint_fields),
        lambda i, f: any((value := _field(i, name)) is not None and str(value).lower() in f for name in sprint_fields),
        lambda i: all(_field(i, name) is None for name in sprint_fields),
    )
    issues = _apply_condition(
        issues,
        _get_field_condition(jql, FIELD_ALIASES["fixVersion"]),
        lambda i, f: any(str(_dump(version).get("name", "")).lower() in f for version in i.fields.fixVersions or []),
        lambda i: not i.fields.fixVersions,
    )
    issues = _apply_condition(
        issues,
        _get_field_condition(jql, FIELD_ALIASES["statusCategory"]),
        lambda i, f: any(
            str(value).lower() in f
            for value in [
                i.fields.status.statusCategory.name if i.fields.status.statusCategory else None,
                i.fields.status.statusCategory.key if i.fields.status.statusCategory else None,
                i.fields.status.statusCategory.id if i.fields.status.statusCategory else None,
            ]
            if value is not None
        ),
        lambda i: i.fields.status.statusCategory is None,
    )
    issues = _apply_condition(
        issues,
        _get_field_condition(jql, FIELD_ALIASES["parent"]),
        lambda i, f: any(
            str(value).lower() in f
            for value in [_field_dict(i).get("parent", {}).get("key"), _field_dict(i).get("parent", {}).get("id")]
            if value is not None
        ),
        lambda i: _field(i, "parent") is None,
    )
    issues = _apply_date_filters(issues, jql, ["created"], lambda i: i.fields.created)
    issues = _apply_date_filters(issues, jql, ["updated"], lambda i: i.fields.updated)
    issues = _apply_date_filters(issues, jql, FIELD_ALIASES["due"], lambda i: _field(i, "duedate") or _field(i, "due"))
    if summary := _extract_contains(jql, "summary"):
        issues = [issue for issue in issues if _all_tokens_match(summary, issue.fields.summary or "")]
    if description := _extract_contains(jql, "description"):
        issues = [issue for issue in issues if _all_tokens_match(description, _adf_to_text(issue.fields.description))]
    if text := _extract_contains(jql, "text"):
        issues = [
            issue
            for issue in issues
            if _all_tokens_match(text, f"{issue.fields.summary or ''}\n{_adf_to_text(issue.fields.description)}")
        ]
    return issues


def _apply_ordering(issues: list[JiraIssue], jql: str) -> list[JiraIssue]:
    order_by = _parse_order_by(jql)
    if not order_by:
        return issues
    field, direction = order_by
    reverse = direction == "desc"

    def issue_key_sort_key(issue_key: str) -> tuple[str, int]:
        project_key, _, number = issue_key.partition("-")
        try:
            return (project_key, int(number))
        except ValueError:
            return (project_key, 0)

    def key(issue: JiraIssue) -> Any:
        if field in {"created", "updated"}:
            return _parse_date_bound(getattr(issue.fields, field)) or 0
        if field == "priority":
            data = _dump(issue.fields.priority)
            return data.get("name", "") if isinstance(data, dict) else ""
        if field == "status":
            return issue.fields.status.name
        if field in {"issuetype", "type"}:
            return issue.fields.issuetype.name
        if field == "key":
            return issue_key_sort_key(issue.key)
        return issue.fields.summary or ""

    return sorted(issues, key=key, reverse=reverse)


def _collect_jql_warnings(jql: str) -> list[str]:
    warnings: list[str] = []
    if re.search(r"\bcurrentUser\(\)", jql, re.IGNORECASE) and get_state().currentUserAccountId is None:
        warnings.append("currentUser() was not applied because no current Jira user is configured.")
    known_fields = {
        *[field.lower() for fields in FIELD_ALIASES.values() for field in fields],
        "created",
        "updated",
        "summary",
        "description",
        "text",
    }
    recognized = False
    condition_regex = re.compile(
        r"\b([A-Za-z][A-Za-z0-9_]*)\b\s*(not\s+in|in|is\s+not|is|!=|=|~|<=|>=|<|>|was|changed)", re.IGNORECASE
    )
    for match in condition_regex.finditer(jql):
        field = match.group(1)
        operator = match.group(2).lower()
        if field.lower() == "by":
            continue
        if field.lower() not in known_fields:
            message = f"Unsupported JQL field '{field}' was not applied by this search."
            if message not in warnings:
                warnings.append(message)
            continue
        recognized = True
        if operator in {"was", "changed"}:
            warnings.append(
                f"Unsupported JQL operator '{operator}' for field '{field}' was not applied by this search."
            )
    if _has_parenthesized_or(jql):
        warnings.append("Parenthesized OR clauses are not supported by this search.")
    if jql.strip() and not recognized and not re.search(r"\border\s+by\b", jql, re.IGNORECASE):
        warnings.append("No supported JQL clauses were recognized; results may be unfiltered.")
    return list(dict.fromkeys(warnings))


def _normalize_search_limit(limit: int | None, warnings: list[str]) -> int:
    if limit is None:
        return 10
    if limit < 0:
        warnings.append("limit must be non-negative; using 0.")
        return 0
    if limit > 50:
        warnings.append("limit exceeds the maximum of 50; using 50.")
        return 50
    return limit


def _normalize_start_at(start_at: int | None, warnings: list[str]) -> int:
    if start_at is None:
        return 0
    if start_at < 0:
        warnings.append("startAt must be non-negative; using 0.")
        return 0
    return start_at


def search(
    jql: Annotated[str, Field(description="JQL query string")],
    fields: Annotated[str, Field(description="Comma-separated fields to return")] = DEFAULT_READ_JIRA_FIELDS,
    limit: Annotated[int | None, Field(description="Maximum number of results (1-50)")] = 10,
    startAt: Annotated[int | None, Field(description="Starting index for pagination")] = 0,
    projects_filter: Annotated[str | None, Field(description="Comma-separated project keys to filter")] = None,
) -> dict[str, Any]:
    """Search Jira issues using JQL."""
    state = get_state()
    warnings = _collect_jql_warnings(jql)
    normalized_limit = _normalize_search_limit(limit, warnings)
    normalized_start = _normalize_start_at(startAt, warnings)
    if _has_parenthesized_or(jql):
        return {
            "startAt": normalized_start,
            "maxResults": normalized_limit,
            "total": 0,
            "issues": [],
            "warningMessages": warnings,
        }
    all_issues = list(state.issues.values())
    clauses = _split_top_level_or_clauses(jql)
    if len(clauses) > 1:
        keys = {issue.key for clause in clauses for issue in _apply_jql_filters(all_issues, clause)}
        issues = [issue for issue in all_issues if issue.key in keys]
    else:
        issues = _apply_jql_filters(all_issues, jql)
    if projects_filter:
        project_keys = {project.strip().lower() for project in projects_filter.split(",")}
        issues = [issue for issue in issues if issue.fields.project.key.lower() in project_keys]
    issues = _apply_ordering(issues, jql)
    total = len(issues)
    page = issues[normalized_start : normalized_start + normalized_limit]
    return {
        "startAt": normalized_start,
        "maxResults": normalized_limit,
        "total": total,
        "issues": _dump_issues(page, fields),
        "warningMessages": warnings,
    }


def get_issue(
    issue_key: IssueKey,
    fields: Annotated[str, Field(description="Fields to return")] = DEFAULT_READ_JIRA_FIELDS,
    expand: Annotated[str | None, Field(description="Optional fields to expand")] = None,
    comment_limit: Annotated[int, Field(description="Maximum number of comments")] = 10,
) -> dict[str, Any]:
    """Get details of a specific Jira issue."""
    del expand
    state = get_state()
    issue = state.issues.get(issue_key)
    if issue is None:
        raise ValueError(f"Issue {issue_key} not found")
    requested = _requested_fields(fields)
    extra_fields = {}
    if (requested is None or "comment" in requested) and comment_limit and comment_limit > 0:
        comments = state.comments.get(issue_key, [])
        extra_fields["comment"] = _dump(
            JiraCommentPage(
                comments=comments[:comment_limit],
                maxResults=comment_limit,
                total=len(comments),
                startAt=0,
            )
        )
    if requested is None or "worklog" in requested:
        worklogs = state.worklogs.get(issue_key, [])
        extra_fields["worklog"] = _dump(
            JiraWorklogPage(
                worklogs=worklogs,
                maxResults=len(worklogs),
                total=len(worklogs),
                startAt=0,
            )
        )
    return _dump_issue(issue, fields, extra_fields)


def get_project_issues(project_key: ProjectKeyArg, limit: LimitArg = 10, startAt: StartAtArg = 0) -> dict[str, Any]:
    """Get all issues for a specific Jira project."""
    issues = [issue for issue in get_state().issues.values() if issue.fields.project.key == project_key]
    return {
        "startAt": startAt,
        "maxResults": limit,
        "total": len(issues),
        "issues": _dump(issues[startAt : startAt + limit]),
    }


def get_epic_issues(epic_key: IssueKey, limit: LimitArg = 10, startAt: StartAtArg = 0) -> dict[str, Any]:
    """Get all issues linked to a specific epic."""
    issues = [
        issue
        for issue in get_state().issues.values()
        if (_field_dict(issue).get("parent") or {}).get("key") == epic_key
    ]
    return {
        "startAt": startAt,
        "maxResults": limit,
        "total": len(issues),
        "issues": _dump(issues[startAt : startAt + limit]),
    }


def create_issue(
    project_key: ProjectKeyArg,
    summary: str,
    issue_type: IssueTypeName,
    assignee: str | None = None,
    description: str = "",
    components: str = "",
    additional_fields: str = "{}",
) -> dict[str, Any]:
    """Create a new Jira issue."""
    parsed_components = _parse_components(components) if components.strip() else []
    try:
        additional = json.loads(additional_fields) if additional_fields else {}
    except json.JSONDecodeError as exc:
        raise ValueError("Invalid JSON in `additional_fields` parameter") from exc
    if not isinstance(additional, dict):
        raise ValueError("Invalid JSON in `additional_fields` parameter")
    parent = None
    if parent_key := additional.get("parent"):
        parent = get_state().issues.get(parent_key)
    labels = additional.get("labels")
    priority = JiraPriority.model_validate(additional["priority"]) if "priority" in additional else None

    issue = create_new_issue(project_key, summary, issue_type, description, assignee)
    if parsed_components:
        issue.fields.components = parsed_components
        save_state()
    if additional:
        if parent:
            issue.fields.parent = _linked_issue(parent)
        if labels is not None:
            issue.fields.labels = labels
        if priority is not None:
            issue.fields.priority = priority
        save_state()
    return {"id": issue.id, "key": issue.key}


def update_issue(
    issue_key: IssueKey,
    fields: Annotated[
        str,
        Field(
            description=(
                "JSON object string of editable issue fields. Supports summary, description, priority, assignee, "
                'and labels. Example: {"summary":"New title","description":"Updated details","labels":["backend"]}. '
                "Do not use this for status changes; use get_transitions and transition_issue instead."
            )
        ),
    ],
) -> dict[str, Any]:
    """Update editable Jira issue fields. Use transition_issue for status/workflow changes."""
    issue = get_state().issues.get(issue_key)
    if issue is None:
        raise ValueError(f"Issue {issue_key} not found")
    try:
        fields_obj = json.loads(fields)
    except json.JSONDecodeError as exc:
        raise ValueError("Invalid JSON in `fields` parameter") from exc
    if not isinstance(fields_obj, dict):
        raise ValueError("Invalid JSON in `fields` parameter")
    supported_fields = {"summary", "description", "status", "priority", "assignee", "labels"}
    unsupported_fields = sorted(set(fields_obj) - supported_fields)
    if unsupported_fields:
        raise ValueError(f"Unsupported update_issue field(s): {', '.join(unsupported_fields)}")
    if "status" in fields_obj:
        raise ValueError(
            "Cannot change status directly via update_issue. Use transition_issue with a valid workflow transition instead. Call get_transitions to see available transitions."
        )
    description_update = None
    if "description" in fields_obj:
        description = fields_obj["description"]
        if description is not None and isinstance(description, str):
            description_update = JiraDocumentContent.model_validate(_adf(description))
        elif description is not None:
            description_update = JiraDocumentContent.model_validate(description)
    priority_update = None
    if "priority" in fields_obj:
        priority = fields_obj["priority"]
        priority_update = (
            None
            if priority is None
            else JiraPriority.model_validate(
                {"id": issue.fields.priority.id if issue.fields.priority else "3", "name": priority}
            )
            if isinstance(priority, str)
            else JiraPriority.model_validate(priority)
        )
    assignee_update = None
    if "assignee" in fields_obj and fields_obj["assignee"] is not None:
        assignee_update = _resolve_user_ref(fields_obj["assignee"])

    draft = issue.model_copy(deep=True)
    if "summary" in fields_obj:
        draft.fields.summary = fields_obj["summary"]
    if "description" in fields_obj:
        draft.fields.description = description_update
    if "priority" in fields_obj:
        draft.fields.priority = priority_update
    if "assignee" in fields_obj:
        draft.fields.assignee = assignee_update
    if "labels" in fields_obj:
        draft.fields.labels = fields_obj["labels"]
    draft.fields.updated = _now()
    get_state().issues[issue_key] = JiraIssue.model_validate(draft.model_dump(mode="json", by_alias=True))
    save_state()
    return _dump(get_state().issues[issue_key])


def delete_issue(issue_key: IssueKey) -> dict[str, str]:
    """Delete an existing Jira issue."""
    state = get_state()
    if issue_key not in state.issues:
        raise ValueError(f"Issue {issue_key} not found")
    del state.issues[issue_key]
    state.comments.pop(issue_key, None)
    state.worklogs.pop(issue_key, None)
    for issue in state.issues.values():
        if issue.fields.parent is not None and issue.fields.parent.key == issue_key:
            issue.fields.parent = None
        if issue.fields.subtasks:
            issue.fields.subtasks = [subtask for subtask in issue.fields.subtasks if subtask.key != issue_key]
        if issue.fields.issuelinks:
            issue.fields.issuelinks = [
                link
                for link in issue.fields.issuelinks
                if (link.inwardIssue is None or link.inwardIssue.key != issue_key)
                and (link.outwardIssue is None or link.outwardIssue.key != issue_key)
            ]
    save_state()
    return {"message": f"Issue {issue_key} deleted successfully"}


def link_issues(
    inward_issue_key: IssueKey, outward_issue_key: IssueKey, link_type: str, comment: str | None = None
) -> dict[str, Any]:
    """Create a bidirectional link between two Jira issues."""
    del comment
    if inward_issue_key == outward_issue_key:
        raise ValueError("Cannot link an issue to itself")
    state = get_state()
    inward_issue = state.issues.get(inward_issue_key)
    outward_issue = state.issues.get(outward_issue_key)
    if inward_issue is None:
        raise ValueError(f"Inward issue {inward_issue_key} not found")
    if outward_issue is None:
        raise ValueError(f"Outward issue {outward_issue_key} not found")
    found_type, should_reverse = _resolve_link_type(link_type)
    if should_reverse:
        inward_issue, outward_issue = outward_issue, inward_issue
    inward_issue.fields.issuelinks = inward_issue.fields.issuelinks or []
    outward_issue.fields.issuelinks = outward_issue.fields.issuelinks or []
    existing_link = next(
        (
            link
            for link in outward_issue.fields.issuelinks
            if link.type.id == found_type.id
            and link.outwardIssue is not None
            and link.outwardIssue.key == inward_issue.key
        ),
        None,
    )
    if existing_link is None:
        existing_link = next(
            (
                link
                for link in inward_issue.fields.issuelinks
                if link.type.id == found_type.id
                and link.inwardIssue is not None
                and link.inwardIssue.key == outward_issue.key
            ),
            None,
        )
    if existing_link is not None:
        return {
            "id": existing_link.id,
            "type": _dump(found_type),
            "inwardIssue": {"key": inward_issue.key, "summary": inward_issue.fields.summary},
            "outwardIssue": {"key": outward_issue.key, "summary": outward_issue.fields.summary},
        }
    link_id = str(get_next_issue_link_id())
    outward_issue.fields.issuelinks.append(
        JiraIssueLink(id=link_id, type=found_type, outwardIssue=_linked_issue(inward_issue))
    )
    inward_issue.fields.issuelinks.append(
        JiraIssueLink(id=str(get_next_issue_link_id()), type=found_type, inwardIssue=_linked_issue(outward_issue))
    )
    save_state()
    return {
        "id": link_id,
        "type": _dump(found_type),
        "inwardIssue": {"key": inward_issue.key, "summary": inward_issue.fields.summary},
        "outwardIssue": {"key": outward_issue.key, "summary": outward_issue.fields.summary},
    }


def search_fields(keyword: str = "", limit: LimitArg = 10, refresh: bool = False) -> list[dict[str, Any]]:
    """Search Jira fields by keyword."""
    del refresh
    fields = list(get_state().fields)
    if keyword.strip():
        lowered = keyword.lower()
        fields = [field for field in fields if lowered in field.name.lower() or lowered in field.id.lower()]
    return _dump(fields[:limit])


def get_link_types() -> dict[str, Any]:
    """Get all available issue link types."""
    return {"issueLinkTypes": _dump(get_state().linkTypes)}
