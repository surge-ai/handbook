from __future__ import annotations

import json
from copy import deepcopy

import pytest
from jira_mock.server import (
    add_attachment,
    add_comment,
    add_worklog,
    create_issue,
    create_sprint,
    create_user,
    delete_issue,
    export_state,
    get_current_user,
    get_epic_issues,
    get_issue,
    get_project_issues,
    get_users,
    import_state,
    link_issues,
    search,
    search_fields,
    update_issue,
)
from jira_mock.state import get_state


@pytest.mark.asyncio
async def test_create_get_update_and_delete_issue() -> None:
    created = await create_issue(
        "MOCK", "Initial summary", "Task", description="Initial body", components="API, Frontend"
    )
    assert created["key"] == "MOCK-1"

    issue = await get_issue("MOCK-1", fields="summary,description,components")
    assert issue["fields"]["summary"] == "Initial summary"
    assert issue["fields"]["description"]["content"][0]["content"][0]["text"] == "Initial body"
    assert [component["name"] for component in issue["fields"]["components"]] == ["API", "Frontend"]

    updated = await update_issue("MOCK-1", json.dumps({"summary": "Updated summary", "labels": ["ops", "urgent"]}))
    assert updated["fields"]["summary"] == "Updated summary"
    assert updated["fields"]["labels"] == ["ops", "urgent"]

    assert (await delete_issue("MOCK-1")) == {"message": "Issue MOCK-1 deleted successfully"}
    with pytest.raises(ValueError, match="Issue MOCK-1 not found"):
        await get_issue("MOCK-1")


@pytest.mark.asyncio
async def test_create_issue_rejects_malformed_additional_fields_and_rolls_back() -> None:
    with pytest.raises(ValueError, match="Invalid JSON in `additional_fields` parameter"):
        await create_issue("MOCK", "Bad additional fields", "Task", additional_fields="{not json")

    assert (await search("project = MOCK", limit=10))["total"] == 0


@pytest.mark.asyncio
async def test_create_issue_uses_max_existing_issue_key_suffix() -> None:
    await create_issue("MOCK", "First", "Task")
    state = await export_state()
    base_issue = state["issues"]["MOCK-1"]

    for suffix in (3, 5):
        issue = deepcopy(base_issue)
        issue["id"] = str(10000 + suffix)
        issue["key"] = f"MOCK-{suffix}"
        issue["self"] = f"https://api.atlassian.com/ex/jira/mock/rest/api/3/issue/MOCK-{suffix}"
        issue["fields"]["summary"] = f"Sparse issue {suffix}"
        state["issues"][issue["key"]] = issue

    await import_state(state)

    created = await create_issue("MOCK", "After sparse keys", "Task")
    assert created["key"] == "MOCK-6"
    assert (await get_issue("MOCK-5"))["fields"]["summary"] == "Sparse issue 5"


@pytest.mark.asyncio
async def test_seeded_state_recovers_numeric_counters() -> None:
    await create_issue("MOCK", "Seed", "Task")
    await create_issue("MOCK", "Seed link target", "Task")
    await link_issues("MOCK-1", "MOCK-2", "Blocks")
    await add_comment("MOCK-1", "seed comment")
    await add_worklog("MOCK-1", "1h", started="2026-05-08T14:30:00Z")
    await add_attachment("MOCK-1", "seed.txt", "aGk=")
    await create_sprint("1000", "Seed sprint", "2026-05-01T00:00:00Z", "2026-05-15T00:00:00Z")

    state = await export_state()
    state["issues"]["MOCK-1"]["id"] = "10050"
    state["issues"]["MOCK-1"]["fields"]["project"]["id"] = "10020"
    state["issues"]["MOCK-2"]["fields"]["project"]["id"] = "10020"
    state["projects"]["MOCK"]["id"] = "10020"
    state["comments"]["MOCK-1"][0]["id"] = "42"
    state["worklogs"]["MOCK-1"][0]["id"] = "24"
    state["issues"]["MOCK-1"]["fields"]["attachment"][0]["id"] = "88"
    state["issues"]["MOCK-1"]["fields"]["issuelinks"][0]["id"] = "78"
    state["issues"]["MOCK-2"]["fields"]["issuelinks"][0]["id"] = "77"

    [sprint] = state["sprints"].values()
    sprint["id"] = 1007
    sprint["self"] = "https://api.atlassian.com/ex/jira/mock/rest/agile/1.0/sprint/1007"
    state["sprints"] = {"1007": sprint}
    state["counters"] = {
        "issueId": 1,
        "sprintId": 1,
        "commentId": 1,
        "worklogId": 1,
        "attachmentId": 1,
        "issueLinkId": 1,
    }

    await import_state(state)

    assert (await create_issue("MOCK", "Next issue id", "Task"))["id"] == "10051"
    assert (await add_comment("MOCK-1", "next comment"))["id"] == "43"
    assert (await add_worklog("MOCK-1", "30m", started="2026-05-08T15:30:00Z"))["id"] == "25"
    assert (await add_attachment("MOCK-1", "next.txt", "b2s="))["id"] == "89"
    assert (await create_sprint("1000", "Next sprint", "2026-06-01T00:00:00Z", "2026-06-15T00:00:00Z"))["id"] == 1008
    assert (await create_issue("ABC", "New project", "Task"))["key"] == "ABC-1"
    assert (await get_issue("ABC-1", fields="project"))["fields"]["project"]["id"] == "10021"
    assert (await link_issues("MOCK-1", "ABC-1", "Blocks"))["id"] == "79"


@pytest.mark.asyncio
async def test_fresh_state_small_id_counters_start_at_one() -> None:
    await create_issue("MOCK", "First", "Task")
    await create_issue("MOCK", "Second", "Task")

    assert (await add_comment("MOCK-1", "first comment"))["id"] == "1"
    assert (await add_worklog("MOCK-1", "1h", started="2026-05-08T14:30:00Z"))["id"] == "1"
    assert (await add_attachment("MOCK-1", "first.txt", "aGk="))["id"] == "1"
    assert (await link_issues("MOCK-1", "MOCK-2", "Blocks"))["id"] == "1"


@pytest.mark.asyncio
async def test_update_issue_rejects_direct_status_change() -> None:
    await create_issue("MOCK", "Needs transition", "Task")

    with pytest.raises(ValueError, match="Cannot change status directly"):
        await update_issue("MOCK-1", json.dumps({"status": "Done"}))


@pytest.mark.asyncio
async def test_update_issue_rejects_malformed_json_only_as_invalid_json() -> None:
    await create_issue("MOCK", "Needs valid json", "Task")

    with pytest.raises(ValueError, match="Invalid JSON"):
        await update_issue("MOCK-1", "{not json")


@pytest.mark.asyncio
async def test_update_issue_rejects_invalid_field_shapes_and_rolls_back() -> None:
    await create_issue("MOCK", "Original summary", "Task")

    with pytest.raises(ValueError):
        await update_issue("MOCK-1", json.dumps({"summary": "Changed", "labels": "not-a-list"}))

    issue = await get_issue("MOCK-1", fields="summary,labels")
    assert issue["fields"]["summary"] == "Original summary"
    assert issue["fields"]["labels"] == []


@pytest.mark.asyncio
async def test_update_issue_rejects_unsupported_fields_and_rolls_back() -> None:
    await create_issue("MOCK", "Original summary", "Task")

    with pytest.raises(ValueError, match="Unsupported update_issue field"):
        await update_issue("MOCK-1", json.dumps({"summary": "Changed", "fixVersions": [{"name": "v1"}]}))

    issue = await get_issue("MOCK-1", fields="summary,fixVersions")
    assert issue["fields"]["summary"] == "Original summary"
    assert issue["fields"]["fixVersions"] == []


@pytest.mark.asyncio
async def test_update_issue_can_clear_optional_fields() -> None:
    (
        await create_issue(
            "MOCK",
            "Clearable",
            "Task",
            description="Initial description",
            assignee="user-1",
            additional_fields=json.dumps({"labels": ["triage"], "priority": {"id": "2", "name": "High"}}),
        )
    )

    updated = await update_issue(
        "MOCK-1",
        json.dumps({"description": "", "assignee": None, "priority": None, "labels": []}),
    )

    assert updated["fields"]["description"]["content"][0]["content"][0]["text"] == ""
    assert updated["fields"].get("assignee") is None
    assert updated["fields"].get("priority") is None
    assert updated["fields"]["labels"] == []


@pytest.mark.asyncio
async def test_users_are_state_level_identities() -> None:
    users = await get_users(query="user-1")
    assert users["total"] == 1
    assert users["values"][0]["accountId"] == "user-1"
    assert "self" not in users["values"][0]
    assert (await get_current_user())["accountId"] == "reporter-001"
    assert "self" not in (await get_current_user())

    with pytest.raises(ValueError, match="User missing-user not found"):
        await create_issue("MOCK", "Unknown assignee", "Task", assignee="missing-user")

    state = await export_state()
    state["is_admin"] = True
    await import_state(state)

    created_user = await create_user("pm-001", "Product Manager", "pm@example.com")
    assert created_user["accountId"] == "pm-001"
    assert "self" not in created_user

    await create_issue("MOCK", "Assigned issue", "Task", assignee="pm-001")
    issue = await get_issue("MOCK-1", fields="assignee,reporter,creator")
    assert issue["fields"]["assignee"]["displayName"] == "Product Manager"
    assert issue["fields"]["reporter"]["accountId"] == "reporter-001"
    assert issue["fields"]["creator"]["accountId"] == "reporter-001"


@pytest.mark.asyncio
async def test_imported_users_replace_defaults_and_define_current_user() -> None:
    state = await export_state()
    state["users"] = {
        "alice": {
            "accountId": "alice",
            "displayName": "Alice",
            "emailAddress": "alice@example.com",
            "timeZone": "America/Los_Angeles",
            "active": True,
        }
    }
    state.pop("currentUserAccountId", None)
    await import_state(state)

    assert (await get_users())["total"] == 1
    assert (await get_current_user())["accountId"] == "alice"
    created = await create_issue("MOCK", "Alice-authored issue", "Task")
    issue = await get_issue(created["key"], fields="reporter,creator")
    assert issue["fields"]["reporter"]["accountId"] == "alice"
    assert issue["fields"]["creator"]["accountId"] == "alice"


@pytest.mark.asyncio
async def test_jql_current_user_uses_configured_current_user() -> None:
    state = await export_state()
    state["currentUserAccountId"] = "user-1"
    await import_state(state)

    await create_issue("MOCK", "Mine", "Task", assignee="user-1")
    await create_issue("MOCK", "Unassigned", "Task")

    result = await search("assignee = currentUser()", limit=10)

    assert result["total"] == 1
    assert result["issues"][0]["fields"]["summary"] == "Mine"


@pytest.mark.asyncio
async def test_jql_current_user_matches_nothing_when_current_user_is_missing() -> None:
    await create_issue("MOCK", "Assigned issue", "Task", assignee="user-1")
    get_state().currentUserAccountId = None

    result = await search("assignee = currentUser()", limit=10)

    assert result["total"] == 0
    assert "currentUser() was not applied because no current Jira user is configured." in result["warningMessages"]


@pytest.mark.asyncio
async def test_imported_state_requires_a_current_user() -> None:
    state = await export_state()
    state["users"] = {}
    state.pop("currentUserAccountId", None)

    with pytest.raises(ValueError, match="requires at least one user"):
        await import_state(state)


@pytest.mark.asyncio
async def test_user_email_and_time_zone_are_validated() -> None:
    state = await export_state()
    state["is_admin"] = True
    await import_state(state)

    with pytest.raises(ValueError):
        await create_user("bad-email", "Bad Email", "not-an-email")
    with pytest.raises(ValueError):
        await create_user("bad-zone", "Bad Zone", "zone@example.com", time_zone="New York")


@pytest.mark.asyncio
async def test_non_admin_cannot_create_users() -> None:
    with pytest.raises(PermissionError):
        await create_user("qa-001", "QA User")


@pytest.mark.asyncio
async def test_update_issue_rejects_empty_summary() -> None:
    await create_issue("MOCK", "Original summary", "Task")

    with pytest.raises(ValueError):
        await update_issue("MOCK-1", json.dumps({"summary": ""}))

    assert (await get_issue("MOCK-1"))["fields"]["summary"] == "Original summary"


@pytest.mark.asyncio
async def test_search_filters_text_project_labels_and_order() -> None:
    first_mock = await create_issue("MOCK", "Fix checkout error", "Bug", description="Payment screen timeout")
    await create_issue("TEST", "Write release plan", "Task", additional_fields=json.dumps({"labels": ["planning"]}))
    second_mock = await create_issue(
        "MOCK", "Checkout polish", "Task", additional_fields=json.dumps({"labels": ["frontend"]})
    )
    state = await export_state()
    base_issue = deepcopy(state["issues"][first_mock["key"]])
    for suffix in range(3, 11):
        issue = deepcopy(base_issue)
        issue["id"] = str(20000 + suffix)
        issue["key"] = f"MOCK-{suffix}"
        issue["fields"]["summary"] = f"Natural sort issue {suffix}"
        state["issues"][issue["key"]] = issue
    await import_state(state)

    assert (await search('project = MOCK AND summary ~ "checkout"', limit=10))["total"] == 2
    assert (await search("labels = planning", limit=10))["issues"][0]["key"] == "TEST-1"
    by_key = await search("key = MOCK-1", limit=10)
    assert by_key["warningMessages"] == []
    assert by_key["total"] == 1
    assert by_key["issues"][0]["key"] == "MOCK-1"
    by_issuekey = await search("issuekey in (MOCK-1, TEST-1)", limit=10)
    assert [issue["key"] for issue in by_issuekey["issues"]] == ["MOCK-1", "TEST-1"]
    ordered = (await search("project in (MOCK, TEST) order by key desc", limit=20))["issues"]
    assert [issue["key"] for issue in ordered] == [
        "TEST-1",
        "MOCK-10",
        "MOCK-9",
        "MOCK-8",
        "MOCK-7",
        "MOCK-6",
        "MOCK-5",
        "MOCK-4",
        "MOCK-3",
        second_mock["key"],
        first_mock["key"],
    ]


@pytest.mark.asyncio
async def test_issue_tools_honor_fields_filter() -> None:
    await create_issue("MOCK", "Filtered", "Task", description="Hidden unless requested")

    result = (await search("project = MOCK", fields="summary", limit=10))["issues"][0]
    assert result["fields"] == {"summary": "Filtered"}

    issue = await get_issue("MOCK-1", fields="summary,description")
    assert set(issue["fields"]) == {"summary", "description"}
    assert issue["fields"]["description"]["content"][0]["content"][0]["text"] == "Hidden unless requested"

    full_issue = await get_issue("MOCK-1", fields="*all")
    assert "status" in full_issue["fields"]
    assert "project" in full_issue["fields"]


@pytest.mark.asyncio
async def test_search_limit_warnings_are_compatible_with_ts_behavior() -> None:
    await create_issue("MOCK", "One", "Task")
    result = await search("project = MOCK", limit=-1, startAt=-2)
    assert result["issues"] == []
    assert "limit must be non-negative; using 0." in result["warningMessages"]
    assert "startAt must be non-negative; using 0." in result["warningMessages"]


@pytest.mark.asyncio
async def test_project_and_epic_issue_helpers() -> None:
    epic = await create_issue("MOCK", "Epic", "Epic")
    child = await create_issue("MOCK", "Story", "Story", additional_fields=json.dumps({"parent": epic["key"]}))

    assert (await get_project_issues("MOCK"))["total"] == 2
    epic_issues = await get_epic_issues(epic["key"])
    assert epic_issues["total"] == 1
    assert epic_issues["issues"][0]["key"] == child["key"]


@pytest.mark.asyncio
async def test_link_issues_creates_bidirectional_links() -> None:
    await create_issue("MOCK", "Blocked", "Task")
    await create_issue("MOCK", "Blocker", "Task")

    result = await link_issues("MOCK-1", "MOCK-2", "Blocks")
    duplicate = await link_issues("MOCK-1", "MOCK-2", "Blocks")
    reciprocal_duplicate = await link_issues("MOCK-2", "MOCK-1", "is blocked by")

    assert duplicate["id"] == result["id"]
    assert reciprocal_duplicate["id"] == result["id"]
    assert result["type"]["name"] == "Blocks"
    assert (await get_issue("MOCK-1", fields="issuelinks"))["fields"]["issuelinks"][0]["inwardIssue"]["key"] == "MOCK-2"
    assert (await get_issue("MOCK-2", fields="issuelinks"))["fields"]["issuelinks"][0]["outwardIssue"][
        "key"
    ] == "MOCK-1"
    assert len((await get_issue("MOCK-1", fields="issuelinks"))["fields"]["issuelinks"]) == 1
    assert len((await get_issue("MOCK-2", fields="issuelinks"))["fields"]["issuelinks"]) == 1


@pytest.mark.asyncio
async def test_link_issues_rejects_self_links() -> None:
    await create_issue("MOCK", "Circular", "Task")

    with pytest.raises(ValueError, match="Cannot link an issue to itself"):
        await link_issues("MOCK-1", "MOCK-1", "Blocks")

    assert "issuelinks" not in (await get_issue("MOCK-1", fields="issuelinks"))["fields"]


@pytest.mark.asyncio
async def test_delete_issue_removes_dangling_issue_references() -> None:
    parent = await create_issue("MOCK", "Parent", "Epic")
    child = await create_issue("MOCK", "Child", "Story", additional_fields=json.dumps({"parent": parent["key"]}))
    peer = await create_issue("MOCK", "Peer", "Task")
    await link_issues(child["key"], peer["key"], "Blocks")

    assert (await get_issue(child["key"], fields="parent"))["fields"]["parent"]["key"] == parent["key"]
    assert (await get_issue(child["key"], fields="issuelinks"))["fields"]["issuelinks"][0]["inwardIssue"][
        "key"
    ] == peer["key"]

    await delete_issue(parent["key"])
    assert "parent" not in (await get_issue(child["key"], fields="parent"))["fields"]

    await delete_issue(peer["key"])
    assert (await get_issue(child["key"], fields="issuelinks"))["fields"]["issuelinks"] == []


@pytest.mark.asyncio
async def test_api_and_state_dump_use_jira_aliases() -> None:
    await create_issue("MOCK", "Alias issue", "Task")
    state = await export_state()
    state["users"]["reporter-001"]["self"] = (
        "https://api.atlassian.com/ex/jira/mock/rest/api/3/user?accountId=reporter-001"
    )
    state["projects"]["MOCK"]["self"] = "https://api.atlassian.com/ex/jira/mock/rest/api/3/project/MOCK"
    state["issues"]["MOCK-1"]["self"] = "https://api.atlassian.com/ex/jira/mock/rest/api/3/issue/MOCK-1"
    state["fields"].append(
        {
            "id": "customfield_10050",
            "key": "customfield_10050",
            "name": "Alias Field",
            "custom": True,
            "schema": {"type": "number", "customId": 10050},
        }
    )
    state["issues"]["MOCK-1"]["changelog"] = {
        "histories": [
            {
                "id": "1",
                "created": "2026-05-08T14:30:00Z",
                "items": [
                    {"field": "status", "from": "10001", "fromString": "To Do", "to": "10002", "toString": "Done"}
                ],
            }
        ]
    }
    await import_state(state)

    field = (await search_fields("Alias Field"))[0]
    assert field["schema"]["type"] == "number"
    assert "schema_" not in field

    exported_item = (await export_state())["issues"]["MOCK-1"]["changelog"]["histories"][0]["items"][0]
    assert exported_item["from"] == "10001"
    assert "from_" not in exported_item
    assert '"self"' not in json.dumps(await export_state())
