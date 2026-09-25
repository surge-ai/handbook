"""Board and sprint tool handlers."""

from __future__ import annotations

from typing import Any

from jira_mock.models import BoardType, JiraBoard, JiraDateTime, JiraSprint, ShortNameString, SprintState
from jira_mock.state import get_next_board_id, get_next_sprint_id, get_state, save_state
from jira_mock.tools.common import (
    DEFAULT_READ_JIRA_FIELDS,
    BoardIdArg,
    LimitArg,
    ProjectKeyArg,
    SprintIdArg,
    StartAtArg,
    _dump,
    _dump_issues,
    _field,
    _now,
    require_admin,
)


def create_board(
    project_key: ProjectKeyArg,
    name: ShortNameString,
    board_type: BoardType = "scrum",
    filter_jql: str | None = None,
) -> dict[str, Any]:
    """Create a Jira board for an existing project. Requires is_admin=true."""
    require_admin()
    state = get_state()
    if project_key not in state.projects:
        raise ValueError(f"Project {project_key} not found")
    board_id = get_next_board_id()
    board = JiraBoard(
        id=board_id,
        name=name,
        type=board_type,
        projectKey=project_key,
        filterJql=filter_jql or f"project = {project_key}",
    )
    state.boards[str(board_id)] = board
    save_state()
    return _dump(board)


def get_boards(
    project_key: ProjectKeyArg | None = None,
    board_type: BoardType | None = None,
    startAt: StartAtArg = 0,
    limit: LimitArg = 10,
) -> dict[str, Any]:
    """Get Jira boards, optionally filtered by project key or board type."""
    boards = list(get_state().boards.values())
    if project_key is not None:
        boards = [board for board in boards if board.projectKey == project_key]
    if board_type is not None:
        boards = [board for board in boards if board.type == board_type]
    return {
        "maxResults": limit,
        "startAt": startAt,
        "total": len(boards),
        "isLast": startAt + limit >= len(boards),
        "values": _dump(boards[startAt : startAt + limit]),
    }


def get_sprints_from_board(
    board_id: BoardIdArg = "1000", state: SprintState | None = None, startAt: StartAtArg = 0, limit: LimitArg = 10
) -> dict[str, Any]:
    """Get Jira sprints from board by state."""
    if board_id not in get_state().boards:
        raise ValueError(f"Board {board_id} not found")
    board_id_int = int(board_id)
    sprints = [sprint for sprint in get_state().sprints.values() if sprint.originBoardId == board_id_int]
    if state:
        sprints = [sprint for sprint in sprints if sprint.state == state]
    return {
        "maxResults": limit,
        "startAt": startAt,
        "isLast": startAt + limit >= len(sprints),
        "values": _dump(sprints[startAt : startAt + limit]),
    }


def create_sprint(
    board_id: BoardIdArg, sprint_name: str, start_date: JiraDateTime, end_date: JiraDateTime, goal: str | None = None
) -> dict[str, Any]:
    """Create Jira sprint for a board."""
    state = get_state()
    board = state.boards.get(board_id)
    if board is None:
        raise ValueError(f"Board {board_id} not found")
    if board.type != "scrum":
        raise ValueError("Sprints can only be created for Scrum boards")
    sprint_id = get_next_sprint_id()
    sprint = {
        "id": sprint_id,
        "state": "future",
        "name": sprint_name,
        "startDate": start_date,
        "endDate": end_date,
        "originBoardId": board.id,
        "goal": goal,
    }
    state.sprints[str(sprint_id)] = JiraSprint.model_validate(sprint)
    save_state()
    return _dump(state.sprints[str(sprint_id)])


def get_sprint_issues(
    sprint_id: SprintIdArg, fields: str = DEFAULT_READ_JIRA_FIELDS, startAt: StartAtArg = 0, limit: LimitArg = 10
) -> dict[str, Any]:
    """Get Jira issues from sprint."""
    issues = [issue for issue in get_state().issues.values() if str(_field(issue, "customfield_10002")) == sprint_id]
    return {
        "startAt": startAt,
        "maxResults": limit,
        "total": len(issues),
        "issues": _dump_issues(issues[startAt : startAt + limit], fields),
    }


def update_sprint(
    sprint_id: SprintIdArg,
    sprint_name: str | None = None,
    state: SprintState | None = None,
    start_date: JiraDateTime | None = None,
    end_date: JiraDateTime | None = None,
    goal: str | None = None,
) -> dict[str, Any]:
    """Update Jira sprint."""
    sprint = get_state().sprints.get(sprint_id)
    if sprint is None:
        raise ValueError(f"Sprint {sprint_id} not found")
    if sprint_name:
        sprint.name = sprint_name
    if state:
        sprint.state = state
        if state == "closed":
            sprint.completeDate = _now()
    if start_date:
        sprint.startDate = start_date
    if end_date:
        sprint.endDate = end_date
    if goal is not None:
        sprint.goal = goal
    save_state()
    return _dump(sprint)
