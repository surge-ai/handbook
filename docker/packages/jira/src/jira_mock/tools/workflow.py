"""Workflow and status tool handlers."""

from __future__ import annotations

from typing import Any

from jira_mock.models import (
    JiraStatus,
    JiraStatusCategory,
    JiraWorkflowTransitionConfig,
    NumericIdString,
    ShortNameString,
    StatusCategoryKey,
)
from jira_mock.state import get_state, get_status_by_name, get_transitions_for_issue, save_state
from jira_mock.tools.common import IssueKey, TransitionIdArg, _dump, _now, require_admin


def get_transitions(issue_key: IssueKey) -> dict[str, Any]:
    """Get available workflow transitions for an issue."""
    issue = get_state().issues.get(issue_key)
    if issue is None:
        raise ValueError(f"Issue {issue_key} not found")
    return {"transitions": get_transitions_for_issue(issue)}


def transition_issue(issue_key: IssueKey, transition_id: TransitionIdArg) -> dict[str, Any]:
    """Move an issue through a workflow transition."""
    issue = get_state().issues.get(issue_key)
    if issue is None:
        raise ValueError(f"Issue {issue_key} not found")
    transitions = get_transitions_for_issue(issue)
    transition = next((item for item in transitions if item["id"] == transition_id), None)
    if transition is None:
        available = ", ".join(f"{item['id']}: {item['name']} -> {item['to']['name']}" for item in transitions)
        raise ValueError(
            f"Transition '{transition_id}' is not available for issue {issue_key} (current status: {issue.fields.status.name}). Available transitions: {available or 'none'}"
        )
    target = get_status_by_name(transition["to"]["name"])
    if target is None:
        raise ValueError(f"Unknown target status: {transition['to']['name']}")
    issue.fields.status = target
    issue.fields.updated = _now()
    issue.fields.resolutiondate = _now() if target.statusCategory and target.statusCategory.key == "done" else None
    save_state()
    return _dump(issue)


def create_status(
    status_id: NumericIdString,
    name: ShortNameString,
    status_category: StatusCategoryKey,
    color_name: ShortNameString | None = None,
    description: str | None = None,
) -> dict[str, Any]:
    """Create a Jira status. Requires is_admin=true in imported state."""
    require_admin()
    state = get_state()
    if status_id in state.statuses:
        raise ValueError(f"Status id {status_id} already exists")
    if get_status_by_name(name) is not None:
        raise ValueError(f"Status named {name!r} already exists")
    category_names = {
        "new": "To Do",
        "indeterminate": "In Progress",
        "done": "Done",
        "undefined": "No Category",
    }
    category_ids = {"new": 2, "indeterminate": 4, "done": 3, "undefined": 1}
    category_colors = {
        "new": "blue-gray",
        "indeterminate": "yellow",
        "done": "green",
        "undefined": "medium-gray",
    }
    status = JiraStatus(
        id=status_id,
        name=name,
        description=description,
        statusCategory=JiraStatusCategory(
            id=category_ids[status_category],
            key=status_category,
            name=category_names[status_category],
            colorName=color_name or category_colors[status_category],
        ),
    )
    state.statuses[status_id] = status
    save_state()
    return _dump(status)


def upsert_workflow_transition(
    from_status: ShortNameString,
    transition_id: NumericIdString,
    transition_name: ShortNameString,
    to_status: ShortNameString,
) -> dict[str, Any]:
    """Create or update a workflow transition between configured Jira statuses. Requires is_admin=true."""
    require_admin()
    state = get_state()
    from_status_model = get_status_by_name(from_status)
    if from_status_model is None:
        raise ValueError(f"From status {from_status!r} is not configured")
    to_status_model = get_status_by_name(to_status)
    if to_status_model is None:
        raise ValueError(f"To status {to_status!r} is not configured")
    transition = JiraWorkflowTransitionConfig(id=transition_id, name=transition_name, to=to_status_model.name)
    transitions = state.workflow.setdefault(from_status_model.name, [])
    for index, existing in enumerate(transitions):
        if existing.id == transition_id:
            transitions[index] = transition
            break
    else:
        transitions.append(transition)
    save_state()
    return _dump(transition)
