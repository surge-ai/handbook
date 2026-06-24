"""FastMCP Jira mock server."""

from __future__ import annotations

import functools
import inspect
from collections.abc import Callable
from typing import Annotated, Any

from fastmcp import FastMCP
from pydantic import EmailStr, Field

from jira_mock.models import (
    AccountId,
    Base64String,
    BoardType,
    IssueTypeName,
    JiraDateTime,
    JiraTimeSpent,
    JiraTimeZone,
    NumericIdString,
    ShortNameString,
    SprintState,
    StatusCategoryKey,
)
from jira_mock.state import set_active_site, write_snapshots
from jira_mock.tools import collaboration, issues, sprints, time, users, workflow
from jira_mock.tools import state as state_tools
from jira_mock.tools.common import (
    DEFAULT_READ_JIRA_FIELDS,
    BoardIdArg,
    IssueKey,
    LimitArg,
    ProjectKeyArg,
    SiteIdArg,
    SprintIdArg,
    StartAtArg,
    TransitionIdArg,
)

mcp = FastMCP("jira-mock-service")


def _snapshot_on_write(fn: Callable[..., Any]) -> Callable[..., Any]:
    @functools.wraps(fn)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        result = fn(*args, **kwargs)
        if inspect.isawaitable(result):
            result = await result
        write_snapshots()
        return result

    return wrapper


def _with_site(fn: Callable[..., Any]) -> Callable[..., Any]:
    @functools.wraps(fn)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        site_id = kwargs.pop("site_id", "default")
        set_active_site(site_id)
        result = fn(*args, **kwargs)
        if inspect.isawaitable(result):
            result = await result
        return result

    return wrapper


@mcp.tool()
@_with_site
def search(
    jql: Annotated[str, Field(description="JQL query string")],
    fields: Annotated[str, Field(description="Comma-separated fields to return")] = DEFAULT_READ_JIRA_FIELDS,
    limit: Annotated[int | None, Field(description="Maximum number of results (1-50)")] = 10,
    startAt: Annotated[int | None, Field(description="Starting index for pagination")] = 0,
    projects_filter: Annotated[str | None, Field(description="Comma-separated project keys to filter")] = None,
    site_id: SiteIdArg = "default",
) -> dict[str, Any]:
    """Search Jira issues using the mock's supported JQL subset.

    Supported filters include project, key, status, assignee, reporter, priority,
    issue type, labels, components, sprint/customfield_10002, fixVersion,
    statusCategory, parent, created, updated, due/duedate, summary ~,
    description ~, and text ~. Equality, inequality, IN, NOT IN, IS EMPTY,
    IS NOT EMPTY, date comparisons, top-level OR, and ORDER BY are supported.

    Unsupported JQL fields/operators are reported in warningMessages and may be
    ignored rather than treated as hard errors. Parenthesized OR clauses are not
    supported and return no results with a warning. Use fields to request a
    comma-separated field subset, or *all for the full issue fields.
    """
    return issues.search(jql, fields, limit, startAt, projects_filter)


@mcp.tool()
@_with_site
def get_issue(
    issue_key: IssueKey,
    fields: Annotated[str, Field(description="Fields to return")] = DEFAULT_READ_JIRA_FIELDS,
    expand: Annotated[str | None, Field(description="Optional fields to expand")] = None,
    comment_limit: Annotated[int, Field(description="Maximum number of comments")] = 10,
    site_id: SiteIdArg = "default",
) -> dict[str, Any]:
    """Get details of a specific Jira issue."""
    return issues.get_issue(issue_key, fields, expand, comment_limit)


@mcp.tool()
@_with_site
def get_project_issues(
    project_key: ProjectKeyArg,
    limit: LimitArg = 10,
    startAt: StartAtArg = 0,
    site_id: SiteIdArg = "default",
) -> dict[str, Any]:
    """Get all issues for a specific Jira project."""
    return issues.get_project_issues(project_key, limit, startAt)


@mcp.tool()
@_with_site
def get_epic_issues(
    epic_key: IssueKey,
    limit: LimitArg = 10,
    startAt: StartAtArg = 0,
    site_id: SiteIdArg = "default",
) -> dict[str, Any]:
    """Get all issues linked to a specific epic."""
    return issues.get_epic_issues(epic_key, limit, startAt)


@mcp.tool()
@_with_site
@_snapshot_on_write
def create_issue(
    project_key: ProjectKeyArg,
    summary: str,
    issue_type: IssueTypeName,
    assignee: str | None = None,
    description: str = "",
    components: str = "",
    additional_fields: str = "{}",
    site_id: SiteIdArg = "default",
) -> dict[str, Any]:
    """Create a new Jira issue."""
    return issues.create_issue(project_key, summary, issue_type, assignee, description, components, additional_fields)


@mcp.tool()
@_with_site
@_snapshot_on_write
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
    site_id: SiteIdArg = "default",
) -> dict[str, Any]:
    """Update editable Jira issue fields. Use transition_issue for status/workflow changes."""
    return issues.update_issue(issue_key, fields)


@mcp.tool()
@_with_site
@_snapshot_on_write
def delete_issue(issue_key: IssueKey, site_id: SiteIdArg = "default") -> dict[str, str]:
    """Delete an existing Jira issue."""
    return issues.delete_issue(issue_key)


@mcp.tool()
@_with_site
@_snapshot_on_write
def link_issues(
    inward_issue_key: IssueKey,
    outward_issue_key: IssueKey,
    link_type: str,
    comment: str | None = None,
    site_id: SiteIdArg = "default",
) -> dict[str, Any]:
    """Create a bidirectional link between two Jira issues.

    link_type may be a configured link type name or one of its inward/outward
    labels, such as Blocks, blocks, or is blocked by. Inward labels resolve to
    the same canonical link type with direction reversed. Default link types are
    Blocks, Cloners, Duplicate, and Relates; call get_link_types to inspect the
    current world's configured link types.
    """
    return issues.link_issues(inward_issue_key, outward_issue_key, link_type, comment)


@mcp.tool()
@_with_site
def search_fields(
    keyword: str = "",
    limit: LimitArg = 10,
    refresh: bool = False,
    site_id: SiteIdArg = "default",
) -> list[dict[str, Any]]:
    """Search Jira fields by keyword."""
    return issues.search_fields(keyword, limit, refresh)


@mcp.tool()
@_with_site
def get_link_types(site_id: SiteIdArg = "default") -> dict[str, Any]:
    """Get all available issue link types."""
    return issues.get_link_types()


@mcp.tool()
@_with_site
def get_transitions(issue_key: IssueKey, site_id: SiteIdArg = "default") -> dict[str, Any]:
    """Get available workflow transitions for an issue."""
    return workflow.get_transitions(issue_key)


@mcp.tool()
@_with_site
@_snapshot_on_write
def transition_issue(
    issue_key: IssueKey, transition_id: TransitionIdArg, site_id: SiteIdArg = "default"
) -> dict[str, Any]:
    """Move an issue through a workflow transition.

    transition_id must be one of the transitions currently available for the
    issue's status. Call get_transitions(issue_key) immediately before this tool
    to discover valid transition IDs and their target statuses.
    """
    return workflow.transition_issue(issue_key, transition_id)


@mcp.tool()
@_with_site
@_snapshot_on_write
def create_user(
    account_id: AccountId,
    display_name: ShortNameString,
    email_address: EmailStr | None = None,
    active: bool = True,
    time_zone: JiraTimeZone | None = "America/New_York",
    site_id: SiteIdArg = "default",
) -> dict[str, Any]:
    """Create a Jira user. Requires is_admin=true."""
    return users.create_user(account_id, display_name, email_address, active, time_zone)


@mcp.tool()
@_with_site
def get_users(
    query: str = "",
    active: bool | None = None,
    startAt: StartAtArg = 0,
    limit: LimitArg = 10,
    site_id: SiteIdArg = "default",
) -> dict[str, Any]:
    """Get Jira users, optionally filtered by query text or active status."""
    return users.get_users(query, active, startAt, limit)


@mcp.tool()
@_with_site
def get_current_user(site_id: SiteIdArg = "default") -> dict[str, Any]:
    """Get the Jira user whose account is currently authenticated for tool calls."""
    return users.get_current_user()


@mcp.tool()
@_with_site
@_snapshot_on_write
def create_status(
    status_id: NumericIdString,
    name: ShortNameString,
    status_category: StatusCategoryKey,
    color_name: ShortNameString | None = None,
    description: str | None = None,
    site_id: SiteIdArg = "default",
) -> dict[str, Any]:
    """Create a Jira status. Requires is_admin=true in imported state."""
    return workflow.create_status(status_id, name, status_category, color_name, description)


@mcp.tool()
@_with_site
@_snapshot_on_write
def upsert_workflow_transition(
    from_status: ShortNameString,
    transition_id: NumericIdString,
    transition_name: ShortNameString,
    to_status: ShortNameString,
    site_id: SiteIdArg = "default",
) -> dict[str, Any]:
    """Create or update a workflow transition between configured Jira statuses. Requires is_admin=true."""
    return workflow.upsert_workflow_transition(from_status, transition_id, transition_name, to_status)


@mcp.tool()
@_with_site
@_snapshot_on_write
def add_comment(issue_key: IssueKey, comment: str, site_id: SiteIdArg = "default") -> dict[str, Any]:
    """Add a comment to a Jira issue."""
    return collaboration.add_comment(issue_key, comment)


@mcp.tool()
@_with_site
@_snapshot_on_write
def create_board(
    project_key: ProjectKeyArg,
    name: ShortNameString,
    board_type: BoardType = "scrum",
    filter_jql: str | None = None,
    site_id: SiteIdArg = "default",
) -> dict[str, Any]:
    """Create a Jira board for an existing project. Requires is_admin=true."""
    return sprints.create_board(project_key, name, board_type, filter_jql)


@mcp.tool()
@_with_site
def get_boards(
    project_key: ProjectKeyArg | None = None,
    board_type: BoardType | None = None,
    startAt: StartAtArg = 0,
    limit: LimitArg = 10,
    site_id: SiteIdArg = "default",
) -> dict[str, Any]:
    """Get Jira boards, optionally filtered by project key or board type."""
    return sprints.get_boards(project_key, board_type, startAt, limit)


@mcp.tool()
@_with_site
def get_sprints_from_board(
    board_id: BoardIdArg = "1000",
    state: SprintState | None = None,
    startAt: StartAtArg = 0,
    limit: LimitArg = 10,
    site_id: SiteIdArg = "default",
) -> dict[str, Any]:
    """Get Jira sprints from board by state."""
    return sprints.get_sprints_from_board(board_id, state, startAt, limit)


@mcp.tool()
@_with_site
@_snapshot_on_write
def create_sprint(
    board_id: BoardIdArg,
    sprint_name: str,
    start_date: JiraDateTime,
    end_date: JiraDateTime,
    goal: str | None = None,
    site_id: SiteIdArg = "default",
) -> dict[str, Any]:
    """Create Jira sprint for a board."""
    return sprints.create_sprint(board_id, sprint_name, start_date, end_date, goal)


@mcp.tool()
@_with_site
def get_sprint_issues(
    sprint_id: SprintIdArg,
    fields: str = DEFAULT_READ_JIRA_FIELDS,
    startAt: StartAtArg = 0,
    limit: LimitArg = 10,
    site_id: SiteIdArg = "default",
) -> dict[str, Any]:
    """Get Jira issues from sprint."""
    return sprints.get_sprint_issues(sprint_id, fields, startAt, limit)


@mcp.tool()
@_with_site
@_snapshot_on_write
def update_sprint(
    sprint_id: SprintIdArg,
    sprint_name: str | None = None,
    state: SprintState | None = None,
    start_date: JiraDateTime | None = None,
    end_date: JiraDateTime | None = None,
    goal: str | None = None,
    site_id: SiteIdArg = "default",
) -> dict[str, Any]:
    """Update Jira sprint."""
    return sprints.update_sprint(sprint_id, sprint_name, state, start_date, end_date, goal)


@mcp.tool()
@_with_site
@_snapshot_on_write
def add_worklog(
    issue_key: IssueKey,
    time_spent: JiraTimeSpent,
    comment: str | None = None,
    started: JiraDateTime | None = None,
    site_id: SiteIdArg = "default",
) -> dict[str, Any]:
    """Log time spent on a Jira issue."""
    return time.add_worklog(issue_key, time_spent, comment, started)


@mcp.tool()
@_with_site
def get_worklogs(issue_key: IssueKey, site_id: SiteIdArg = "default") -> dict[str, Any]:
    """Get all work logs for a Jira issue."""
    return time.get_worklogs(issue_key)


@mcp.tool()
@_with_site
@_snapshot_on_write
def update_estimate(
    issue_key: IssueKey,
    original_estimate: JiraTimeSpent,
    remaining_estimate: JiraTimeSpent | None = None,
    site_id: SiteIdArg = "default",
) -> dict[str, Any]:
    """Set or update time estimates for an issue."""
    return time.update_estimate(issue_key, original_estimate, remaining_estimate)


@mcp.tool()
@_with_site
@_snapshot_on_write
def add_watcher(issue_key: IssueKey, account_id: AccountId, site_id: SiteIdArg = "default") -> dict[str, str]:
    """Add a watcher to a Jira issue."""
    return collaboration.add_watcher(issue_key, account_id)


@mcp.tool()
@_with_site
@_snapshot_on_write
def remove_watcher(issue_key: IssueKey, account_id: AccountId, site_id: SiteIdArg = "default") -> dict[str, str]:
    """Remove a watcher from a Jira issue."""
    return collaboration.remove_watcher(issue_key, account_id)


@mcp.tool()
@_with_site
def get_watchers(issue_key: IssueKey, site_id: SiteIdArg = "default") -> dict[str, Any]:
    """Get all watchers of a Jira issue."""
    return collaboration.get_watchers(issue_key)


@mcp.tool()
@_with_site
@_snapshot_on_write
def add_attachment(
    issue_key: IssueKey,
    filename: str,
    content_base64: Base64String,
    mime_type: str | None = None,
    site_id: SiteIdArg = "default",
) -> dict[str, Any]:
    """Attach a base64-encoded file to a Jira issue."""
    return collaboration.add_attachment(issue_key, filename, content_base64, mime_type)


@mcp.tool()
@_with_site
def get_attachments(issue_key: IssueKey, site_id: SiteIdArg = "default") -> dict[str, Any]:
    """List all attachments on a Jira issue."""
    return collaboration.get_attachments(issue_key)


@mcp.tool()
async def list_sites() -> dict[str, Any]:
    """List available Jira sites in the loaded mock state."""
    return state_tools.list_sites()


@mcp.tool()
async def export_state() -> dict[str, Any]:
    """Export the full Jira state as JSON."""
    return state_tools.export_state()


@mcp.tool()
@_snapshot_on_write
def import_state(state: dict[str, Any]) -> dict[str, bool]:
    """Replace the full Jira state with provided JSON."""
    return state_tools.import_state(state)
