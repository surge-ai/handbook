"""Time tracking tool handlers."""

from __future__ import annotations

import re
from typing import Any

from jira_mock.models import JiraDateTime, JiraTimeSpent, JiraTimeTracking, JiraWorklog
from jira_mock.state import get_next_worklog_id, get_state, save_state
from jira_mock.tools.common import IssueKey, _adf, _current_user, _dump, _now


def _parse_time_spent(time_spent: str) -> int:
    total = 0
    for value, unit in re.findall(r"(\d+)\s*([wdhm])", time_spent.lower()):
        n = int(value)
        total += {"w": n * 5 * 8 * 3600, "d": n * 8 * 3600, "h": n * 3600, "m": n * 60}[unit]
    return total


def _format_seconds(seconds: int) -> str:
    if seconds == 0:
        return "0m"
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    parts = []
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    return " ".join(parts) or "0m"


def add_worklog(
    issue_key: IssueKey, time_spent: JiraTimeSpent, comment: str | None = None, started: JiraDateTime | None = None
) -> dict[str, Any]:
    """Log time spent on a Jira issue."""
    state = get_state()
    issue = state.issues.get(issue_key)
    if issue is None:
        raise ValueError(f"Issue {issue_key} not found")
    seconds = _parse_time_spent(time_spent)
    if seconds == 0:
        raise ValueError(f"Invalid time format: {time_spent}. Use Jira notation like '2h', '1d 4h', '30m'.")
    now = _now()
    worklog = JiraWorklog.model_validate(
        {
            "id": str(get_next_worklog_id()),
            "author": _dump(_current_user()),
            "updateAuthor": _dump(_current_user()),
            "comment": _adf(comment) if comment else None,
            "created": now,
            "updated": now,
            "started": started or now,
            "timeSpent": time_spent,
            "timeSpentSeconds": seconds,
        }
    )
    state.worklogs.setdefault(issue_key, []).append(worklog)
    issue.fields.timetracking = issue.fields.timetracking or JiraTimeTracking()
    spent = int(issue.fields.timetracking.timeSpentSeconds or 0) + seconds
    issue.fields.timetracking.timeSpentSeconds = spent
    issue.fields.timetracking.timeSpent = _format_seconds(spent)
    if issue.fields.timetracking.remainingEstimateSeconds:
        remaining = max(0, int(issue.fields.timetracking.remainingEstimateSeconds) - seconds)
        issue.fields.timetracking.remainingEstimateSeconds = remaining
        issue.fields.timetracking.remainingEstimate = _format_seconds(remaining)
    issue.fields.updated = now
    save_state()
    return _dump(worklog)


def get_worklogs(issue_key: IssueKey) -> dict[str, Any]:
    """Get all work logs for a Jira issue."""
    issue = get_state().issues.get(issue_key)
    if issue is None:
        raise ValueError(f"Issue {issue_key} not found")
    worklogs = get_state().worklogs.get(issue_key, [])
    return {
        "worklogs": _dump(worklogs),
        "timetracking": _dump(issue.fields.timetracking or {}),
        "totalTimeSpentSeconds": sum(w.timeSpentSeconds for w in worklogs),
    }


def update_estimate(
    issue_key: IssueKey, original_estimate: JiraTimeSpent, remaining_estimate: JiraTimeSpent | None = None
) -> dict[str, Any]:
    """Set or update time estimates for an issue."""
    issue = get_state().issues.get(issue_key)
    if issue is None:
        raise ValueError(f"Issue {issue_key} not found")
    original = _parse_time_spent(original_estimate)
    if original == 0:
        raise ValueError(f"Invalid time format: {original_estimate}")
    issue.fields.timetracking = issue.fields.timetracking or JiraTimeTracking()
    issue.fields.timetracking.originalEstimate = original_estimate
    issue.fields.timetracking.originalEstimateSeconds = original
    if remaining_estimate:
        issue.fields.timetracking.remainingEstimate = remaining_estimate
        issue.fields.timetracking.remainingEstimateSeconds = _parse_time_spent(remaining_estimate)
    else:
        remaining = max(0, original - int(issue.fields.timetracking.timeSpentSeconds or 0))
        issue.fields.timetracking.remainingEstimate = _format_seconds(remaining)
        issue.fields.timetracking.remainingEstimateSeconds = remaining
    issue.fields.updated = _now()
    save_state()
    return {"timetracking": _dump(issue.fields.timetracking)}
