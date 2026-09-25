from __future__ import annotations

import pytest
from jira_mock.server import (
    add_comment,
    add_worklog,
    create_board,
    create_issue,
    create_sprint,
    export_state,
    get_boards,
    get_sprint_issues,
    get_sprints_from_board,
    get_worklogs,
    import_state,
    update_estimate,
    update_sprint,
)
from jira_mock.state import init_state, set_snapshot_paths, write_snapshots
from jira_mock.viewer import create_app
from starlette.testclient import TestClient


@pytest.mark.asyncio
async def test_sprints_and_sprint_issues() -> None:
    await create_issue("MOCK", "Sprint item", "Story", additional_fields='{"labels":["seed"]}')
    boards = await get_boards(project_key="MOCK")
    assert boards["total"] == 1
    assert boards["values"][0]["id"] == 1000
    assert boards["values"][0]["projectKey"] == "MOCK"

    sprint = await create_sprint("1000", "Sprint 1", "2026-01-01T00:00:00Z", "2026-01-15T00:00:00Z", goal="Ship")
    state = await export_state()
    state["issues"]["MOCK-1"]["fields"]["customfield_10002"] = sprint["id"]
    await import_state(state)

    assert (await get_sprints_from_board("1000"))["values"][0]["name"] == "Sprint 1"
    assert (await get_sprint_issues(str(sprint["id"])))["total"] == 1
    filtered_issue = (await get_sprint_issues(str(sprint["id"]), fields="summary"))["issues"][0]
    assert filtered_issue["fields"] == {"summary": "Sprint item"}
    assert (await update_sprint(str(sprint["id"]), state="closed"))["state"] == "closed"


@pytest.mark.asyncio
async def test_sprints_are_scoped_to_boards() -> None:
    mock_sprint = await create_sprint("1000", "Mock sprint", "2026-01-01T00:00:00Z", "2026-01-15T00:00:00Z")
    test_sprint = await create_sprint("1001", "Test sprint", "2026-02-01T00:00:00Z", "2026-02-15T00:00:00Z")

    assert [sprint["id"] for sprint in (await get_sprints_from_board("1000"))["values"]] == [mock_sprint["id"]]
    assert [sprint["id"] for sprint in (await get_sprints_from_board("1001"))["values"]] == [test_sprint["id"]]


@pytest.mark.asyncio
async def test_admin_can_create_boards() -> None:
    with pytest.raises(PermissionError):
        await create_board("MOCK", "Unauthorized Board")

    state = await export_state()
    state["is_admin"] = True
    await import_state(state)

    board = await create_board("MOCK", "Operations Scrum Board")
    assert board["id"] == 1002
    assert board["type"] == "scrum"
    assert board["projectKey"] == "MOCK"
    assert board["filterJql"] == "project = MOCK"

    sprint = await create_sprint(str(board["id"]), "Operations sprint", "2026-03-01T00:00:00Z", "2026-03-15T00:00:00Z")
    assert sprint["originBoardId"] == board["id"]


@pytest.mark.asyncio
async def test_create_board_recovers_seeded_board_counter() -> None:
    state = await export_state()
    state["is_admin"] = True
    state["boards"]["2000"] = {
        "id": 2000,
        "name": "Seeded Scrum Board",
        "type": "scrum",
        "projectKey": "MOCK",
        "filterJql": "project = MOCK",
    }
    await import_state(state)

    assert (await create_board("MOCK", "Next Scrum Board"))["id"] == 2001


@pytest.mark.asyncio
async def test_sprints_require_existing_scrum_board() -> None:
    with pytest.raises(ValueError, match="Board 9999 not found"):
        await create_sprint("9999", "Missing board", "2026-01-01T00:00:00Z", "2026-01-15T00:00:00Z")

    state = await export_state()
    state["boards"]["2000"] = {
        "id": 2000,
        "name": "Mock Kanban Board",
        "type": "kanban",
        "projectKey": "MOCK",
        "filterJql": "project = MOCK",
    }
    await import_state(state)

    with pytest.raises(ValueError, match="Sprints can only be created for Scrum boards"):
        await create_sprint("2000", "Kanban sprint", "2026-01-01T00:00:00Z", "2026-01-15T00:00:00Z")


@pytest.mark.asyncio
async def test_worklogs_update_timetracking() -> None:
    await create_issue("MOCK", "Timed work", "Task")
    await update_estimate("MOCK-1", "1d")
    worklog = await add_worklog(
        "MOCK-1",
        "2h 30m",
        comment="Investigated",
        started="2026-05-08T14:30:00Z",
    )

    assert worklog["timeSpentSeconds"] == 9000
    assert worklog["started"] == "2026-05-08T14:30:00Z"
    summary = await get_worklogs("MOCK-1")
    assert summary["totalTimeSpentSeconds"] == 9000
    assert summary["timetracking"]["remainingEstimate"] == "5h 30m"


@pytest.mark.asyncio
async def test_state_round_trips() -> None:
    await create_issue("MOCK", "Round trip", "Task")
    state = await export_state()
    state["issues"]["MOCK-1"]["fields"]["summary"] = "Changed outside tool"

    assert (await import_state(state)) == {"ok": True}
    assert (await export_state())["issues"]["MOCK-1"]["fields"]["summary"] == "Changed outside tool"


def test_snapshot_paths_support_partial_updates(tmp_path) -> None:
    bundle_path = tmp_path / "bundle.json"
    first_final_path = tmp_path / "first-final.json"
    second_final_path = tmp_path / "second-final.json"

    try:
        set_snapshot_paths(final_path=first_final_path, bundle_state_path=bundle_path)
        write_snapshots()
        assert bundle_path.exists()
        assert first_final_path.exists()

        bundle_path.unlink()
        set_snapshot_paths(final_path=second_final_path)
        write_snapshots()

        assert bundle_path.exists()
        assert second_final_path.exists()
    finally:
        set_snapshot_paths(final_path=None, bundle_state_path=None)


def test_init_state_writes_initial_json_without_final_snapshot(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("OUTPUTDIR", str(tmp_path))

    try:
        init_state()

        assert (tmp_path / "initial.json").exists()
        assert not (tmp_path / "final.json").exists()
    finally:
        set_snapshot_paths(final_path=None, bundle_state_path=None)


@pytest.mark.asyncio
async def test_viewer_api_smoke(monkeypatch) -> None:
    await create_issue("MOCK", "Viewer issue", "Task", components="API,Frontend")
    await add_comment("MOCK-1", "Viewer comment")
    client = TestClient(create_app())

    home = client.get("/")
    assert home.status_code == 200
    assert "list-view" in home.text
    assert "kanban-view" in home.text
    assert "detail-panel" in home.text
    issues = client.get("/api/issues")
    assert issues.status_code == 200
    assert issues.json()["issues"][0]["key"] == "MOCK-1"
    assert issues.json()["issues"][0]["status"] == "To Do"
    assert client.get("/api/issues", params={"project": "MOCK"}).json()["total"] == 1
    assert client.get("/api/issues", params={"type": "Bug"}).json()["total"] == 0
    assert client.get("/api/projects").json()["projects"][0]["issueCount"] == 1
    detail = client.get("/api/issues/MOCK-1").json()
    assert detail["issue"]["components"] == ["API", "Frontend"]
    assert detail["comments"][0]["body"] == "Viewer comment"

    monkeypatch.setenv("MCP_PROXY_TOKEN", "secret")
    authed_client = TestClient(create_app())
    assert authed_client.get("/api/issues").status_code == 403
    assert authed_client.get("/api/issues", headers={"x-proxy-token": "secret"}).status_code == 200


@pytest.mark.asyncio
async def test_viewer_shows_issue_attachments() -> None:
    import base64

    from jira_mock.models import JiraAttachment
    from jira_mock.state import get_state

    await create_issue("MOCK", "Issue with attachment", "Task")
    state = get_state()
    author = next(iter(state.users.values()))
    state.issues["MOCK-1"].fields.attachment = [
        JiraAttachment(
            id="1",
            filename="report.txt",
            author=author,
            created="2026-01-01T00:00:00Z",
            size=11,
            mimeType="text/plain",
            content=base64.b64encode(b"hello world").decode(),
        )
    ]
    client = TestClient(create_app())

    # Issue list signals attachment presence.
    assert client.get("/api/issues").json()["issues"][0]["attachmentCount"] == 1

    # Issue detail exposes attachment metadata, never the stored bytes.
    detail = client.get("/api/issues/MOCK-1").json()["issue"]
    assert len(detail["attachments"]) == 1
    attachment = detail["attachments"][0]
    assert attachment["filename"] == "report.txt"
    assert attachment["mimeType"] == "text/plain"
    assert attachment["size"] == 11
    assert "content" not in attachment
