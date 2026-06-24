from __future__ import annotations

import base64

import pytest
from jira_mock.server import (
    add_attachment,
    add_comment,
    add_watcher,
    create_issue,
    create_status,
    create_user,
    export_state,
    get_attachments,
    get_issue,
    get_transitions,
    get_watchers,
    import_state,
    remove_watcher,
    transition_issue,
    upsert_workflow_transition,
)


@pytest.mark.asyncio
async def test_transitions_follow_default_workflow() -> None:
    await create_issue("MOCK", "Workflow", "Task")

    transitions = (await get_transitions("MOCK-1"))["transitions"]
    assert transitions[0]["id"] == "1"

    issue = await transition_issue("MOCK-1", "1")
    assert issue["fields"]["status"]["name"] == "In Progress"

    with pytest.raises(ValueError, match="not available"):
        await transition_issue("MOCK-1", "1")


@pytest.mark.asyncio
async def test_empty_statuses_and_workflow_inherit_defaults() -> None:
    state = await export_state()
    state["statuses"] = {}
    state["workflow"] = {}
    state.pop("defaultStatusValue", None)

    await import_state(state)

    exported = await export_state()
    assert exported["defaultStatusValue"] == "To Do"
    assert exported["statuses"]["10001"]["name"] == "To Do"
    assert "To Do" in exported["workflow"]
    created = await create_issue("MOCK", "Default status after empty maps", "Task")
    assert (await get_issue(created["key"], fields="status"))["fields"]["status"]["name"] == "To Do"


@pytest.mark.asyncio
async def test_empty_default_status_value_inherits_default() -> None:
    state = await export_state()
    state["defaultStatusValue"] = ""

    await import_state(state)

    assert (await export_state())["defaultStatusValue"] == "To Do"


@pytest.mark.asyncio
async def test_create_issue_uses_configured_default_status_value() -> None:
    state = await export_state()
    state["defaultStatusValue"] = "Backlog"
    state["statuses"] = {
        "20001": {
            "id": "20001",
            "name": "Backlog",
            "description": "Ready for triage",
            "statusCategory": {"id": 2, "key": "new", "name": "To Do", "colorName": "blue-gray"},
        },
        "20002": {
            "id": "20002",
            "name": "Selected",
            "description": "Ready to start",
            "statusCategory": {"id": 4, "key": "indeterminate", "name": "In Progress", "colorName": "yellow"},
        },
    }
    state["workflow"] = {"Backlog": [{"id": "21", "name": "Select for Work", "to": "Selected"}], "Selected": []}
    await import_state(state)

    created = await create_issue("MOCK", "Starts in custom default", "Task")

    assert (await get_issue(created["key"], fields="status"))["fields"]["status"]["name"] == "Backlog"
    transitions = (await get_transitions(created["key"]))["transitions"]
    assert transitions == [
        {
            "id": "21",
            "name": "Select for Work",
            "to": {
                "id": "20002",
                "name": "Selected",
                "description": "Ready to start",
                "iconUrl": None,
                "statusCategory": {
                    "id": 4,
                    "key": "indeterminate",
                    "name": "In Progress",
                    "colorName": "yellow",
                },
            },
            "hasScreen": False,
            "isGlobal": False,
            "isInitial": False,
            "isAvailable": True,
            "isConditional": False,
            "isLooped": False,
        }
    ]


@pytest.mark.asyncio
async def test_default_status_value_must_reference_status_and_workflow() -> None:
    state = await export_state()
    state["defaultStatusValue"] = "Backlog"

    with pytest.raises(ValueError, match="defaultStatusValue 'Backlog' does not reference a configured status"):
        await import_state(state)

    state["statuses"]["20001"] = {
        "id": "20001",
        "name": "Backlog",
        "description": "Ready for triage",
        "statusCategory": {"id": 2, "key": "new", "name": "To Do", "colorName": "blue-gray"},
    }
    with pytest.raises(ValueError, match="defaultStatusValue 'Backlog' does not have a workflow entry"):
        await import_state(state)


@pytest.mark.asyncio
async def test_admin_can_create_custom_status_and_workflow_transition() -> None:
    state = await export_state()
    state["is_admin"] = True
    await import_state(state)
    await create_issue("MOCK", "Blocked work", "Task")

    status = await create_status("10005", "Blocked", "indeterminate", description="Work is blocked")
    transition = await upsert_workflow_transition("To Do", "8", "Mark Blocked", "Blocked")

    assert status["name"] == "Blocked"
    assert transition == {"id": "8", "name": "Mark Blocked", "to": "Blocked"}
    assert any(item["id"] == "8" for item in (await get_transitions("MOCK-1"))["transitions"])
    assert (await transition_issue("MOCK-1", "8"))["fields"]["status"]["name"] == "Blocked"


@pytest.mark.asyncio
async def test_workflow_transition_upsert_canonicalizes_status_names() -> None:
    state = await export_state()
    state["is_admin"] = True
    await import_state(state)
    await create_issue("MOCK", "Blocked work", "Task")
    await create_status("10005", "Blocked", "indeterminate", description="Work is blocked")

    transition = await upsert_workflow_transition("to do", "8", "Mark Blocked", "blocked")

    assert transition == {"id": "8", "name": "Mark Blocked", "to": "Blocked"}
    exported = await export_state()
    assert "to do" not in exported["workflow"]
    assert exported["workflow"]["To Do"][-1] == {"id": "8", "name": "Mark Blocked", "to": "Blocked"}
    assert any(item["id"] == "8" for item in (await get_transitions("MOCK-1"))["transitions"])
    assert (await transition_issue("MOCK-1", "8"))["fields"]["status"]["name"] == "Blocked"


@pytest.mark.asyncio
async def test_non_admin_cannot_configure_statuses_or_workflows() -> None:
    with pytest.raises(PermissionError, match="Admin privileges"):
        await create_status("10005", "Blocked", "indeterminate")

    with pytest.raises(PermissionError, match="Admin privileges"):
        await upsert_workflow_transition("To Do", "8", "Mark Blocked", "Blocked")


@pytest.mark.asyncio
async def test_comments_watchers_and_attachments() -> None:
    await create_issue("MOCK", "Collaboration", "Task")

    comment = await add_comment("MOCK-1", "Looks good")
    assert comment["body"]["content"][0]["content"][0]["text"] == "Looks good"
    assert (await get_issue("MOCK-1", fields="comment"))["fields"]["comment"]["total"] == 1
    await add_comment("MOCK-1", "Second")
    assert "comment" not in (await export_state())["issues"]["MOCK-1"]["fields"]
    assert (await get_issue("MOCK-1", fields="comment"))["fields"]["comment"]["total"] == 2

    state = await export_state()
    state["is_admin"] = True
    await import_state(state)
    await create_user("alice", "Alice")

    assert (await add_watcher("MOCK-1", "alice"))["message"] == "Added watcher alice to MOCK-1"
    assert (await add_watcher("MOCK-1", "alice"))["message"] == "User alice is already watching MOCK-1"
    assert (await get_watchers("MOCK-1"))["watchCount"] == 1
    assert (await remove_watcher("MOCK-1", "alice"))["message"] == "Removed watcher alice from MOCK-1"

    payload = base64.b64encode(b"hello").decode()
    attachment = await add_attachment("MOCK-1", "note.txt", payload)
    assert attachment["mimeType"] == "text/plain"
    attachments = await get_attachments("MOCK-1")
    assert attachments["total"] == 1
    assert "content" not in attachments["attachments"][0]
