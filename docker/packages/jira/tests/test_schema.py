from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest
from jira_mock.models import (
    JiraAttachment,
    JiraComment,
    JiraField,
    JiraIssue,
    JiraPriority,
    JiraSprint,
    JiraState,
    JiraStatusCategory,
    JiraWorklog,
)
from pydantic import ValidationError

REPO_ROOT = Path(__file__).resolve().parents[3]


def _issue_with_dates(created: str, updated: str) -> dict:
    return {
        "id": "10001",
        "key": "MOCK-1",
        "self": "https://api.atlassian.com/ex/jira/mock/rest/api/3/issue/MOCK-1",
        "fields": {
            "summary": "Schema check",
            "description": None,
            "issuetype": {"id": "10001", "name": "Task", "subtask": False},
            "project": {"id": "10001", "key": "MOCK", "name": "Mock Project"},
            "status": {"id": "10001", "name": "To Do"},
            "created": created,
            "updated": updated,
        },
    }


@pytest.mark.parametrize(
    "timestamp",
    [
        "2026-05-08T14:30:00Z",
        "2026-05-08T14:30:00.123Z",
        "2026-05-08T14:30:00-04:00",
        "2026-05-08T14:30:00.123-0400",
    ],
)
def test_jira_datetime_accepts_rest_api_timestamp_shapes(timestamp: str) -> None:
    issue = JiraIssue.model_validate(_issue_with_dates(timestamp, timestamp))
    assert issue.fields.created == timestamp


def test_jira_datetime_rejects_naive_timestamp() -> None:
    with pytest.raises(ValidationError):
        JiraIssue.model_validate(_issue_with_dates("2026-05-08T14:30:00", "2026-05-08T14:30:00Z"))


def test_issue_type_and_priority_names_allow_custom_values() -> None:
    data = _issue_with_dates("2026-05-08T14:30:00Z", "2026-05-08T14:30:00Z")
    data["fields"]["issuetype"]["name"] = "Customer Escalation"
    data["fields"]["priority"] = {"id": "7", "name": "Launch Blocker"}

    issue = JiraIssue.model_validate(data)

    assert issue.fields.issuetype.name == "Customer Escalation"
    assert isinstance(issue.fields.priority, JiraPriority)
    assert issue.fields.priority.name == "Launch Blocker"


def test_issue_type_and_priority_names_reject_empty_values() -> None:
    data = _issue_with_dates("2026-05-08T14:30:00Z", "2026-05-08T14:30:00Z")
    data["fields"]["issuetype"]["name"] = " "

    with pytest.raises(ValidationError):
        JiraIssue.model_validate(data)

    data = _issue_with_dates("2026-05-08T14:30:00Z", "2026-05-08T14:30:00Z")
    data["fields"]["priority"] = {"id": "7", "name": ""}

    with pytest.raises(ValidationError):
        JiraIssue.model_validate(data)


def test_jira_user_account_type_is_constrained() -> None:
    JiraState.model_validate(
        {
            "currentUserAccountId": "app-user",
            "users": {"app-user": {"accountId": "app-user", "accountType": "app", "displayName": "App User"}},
        }
    )

    with pytest.raises(ValidationError):
        JiraState.model_validate(
            {
                "currentUserAccountId": "custom-user",
                "users": {
                    "custom-user": {
                        "accountId": "custom-user",
                        "accountType": "external",
                        "displayName": "Custom User",
                    }
                },
            }
        )


def test_stable_jira_enums_are_constrained() -> None:
    JiraStatusCategory.model_validate({"id": 2, "key": "new", "name": "To Do", "colorName": "blue-gray"})
    JiraSprint.model_validate(
        {
            "id": 1,
            "self": "https://api.atlassian.com/ex/jira/mock/rest/agile/1.0/sprint/1",
            "state": "future",
            "name": "Sprint 1",
            "originBoardId": 1000,
        }
    )

    with pytest.raises(ValidationError):
        JiraStatusCategory.model_validate({"id": 2, "key": "todo", "name": "To Do", "colorName": "blue-gray"})

    with pytest.raises(ValidationError):
        JiraSprint.model_validate(
            {
                "id": 1,
                "self": "https://api.atlassian.com/ex/jira/mock/rest/agile/1.0/sprint/1",
                "state": "completed",
                "name": "Sprint 1",
                "originBoardId": 1000,
            }
        )


def test_adf_node_types_are_constrained() -> None:
    data = _issue_with_dates("2026-05-08T14:30:00Z", "2026-05-08T14:30:00Z")
    data["fields"]["description"] = {
        "type": "doc",
        "version": 1,
        "content": [{"type": "unknownBlock", "content": [{"type": "text", "text": "hello"}]}],
    }

    with pytest.raises(ValidationError):
        JiraIssue.model_validate(data)


def test_state_keys_must_match_entity_ids() -> None:
    issue = _issue_with_dates("2026-05-08T14:30:00Z", "2026-05-08T14:30:00Z")
    state = {"issues": {"OTHER-1": issue}}

    with pytest.raises(ValidationError, match="issues key"):
        JiraState.model_validate(state)

    state = {"projects": {"OTHER": {"id": "10001", "key": "MOCK", "name": "Mock Project"}}}

    with pytest.raises(ValidationError, match="projects key"):
        JiraState.model_validate(state)

    state = {"users": {"other-user": {"accountId": "user-1", "displayName": "User 1"}}}

    with pytest.raises(ValidationError, match="users key"):
        JiraState.model_validate(state)

    state = {
        "projects": {"MOCK": {"id": "10001", "key": "MOCK", "name": "Mock Project"}},
        "boards": {"2": {"id": 1, "name": "Mock Board", "type": "scrum", "projectKey": "MOCK"}},
    }

    with pytest.raises(ValidationError, match="boards key"):
        JiraState.model_validate(state)

    state = {
        "sprints": {
            "2": {
                "id": 1,
                "self": "https://api.atlassian.com/ex/jira/mock/rest/agile/1.0/sprint/1",
                "state": "future",
                "name": "Sprint 1",
                "originBoardId": 1000,
            }
        }
    }

    with pytest.raises(ValidationError, match="sprints key"):
        JiraState.model_validate(state)


def test_comments_and_worklogs_must_reference_existing_issue_keys() -> None:
    comment = {
        "id": "1",
        "author": {"accountId": "commenter-001", "displayName": "User commenter-001"},
        "body": {"type": "doc", "version": 1, "content": []},
        "created": "2026-05-08T14:30:00Z",
        "updated": "2026-05-08T14:30:00Z",
    }
    worklog = {
        "id": "1",
        "author": {"accountId": "worker-001", "displayName": "User worker-001"},
        "updateAuthor": {"accountId": "worker-001", "displayName": "User worker-001"},
        "created": "2026-05-08T14:30:00Z",
        "updated": "2026-05-08T14:30:00Z",
        "started": "2026-05-08T14:30:00Z",
        "timeSpent": "1h",
        "timeSpentSeconds": 3600,
    }

    with pytest.raises(ValidationError, match="comments key"):
        JiraState.model_validate({"comments": {"MOCK-1": [comment]}})

    with pytest.raises(ValidationError, match="worklogs key"):
        JiraState.model_validate({"worklogs": {"MOCK-1": [worklog]}})


def test_issue_relationships_must_reference_existing_issue_keys() -> None:
    issue = _issue_with_dates("2026-05-08T14:30:00Z", "2026-05-08T14:30:00Z")
    state = {
        "issues": {"MOCK-1": issue},
        "projects": {"MOCK": {"id": "10001", "key": "MOCK", "name": "Mock Project"}},
    }

    parent_issue = {
        **issue,
        "fields": {
            **issue["fields"],
            "parent": {"id": "10002", "key": "MOCK-2"},
        },
    }
    with pytest.raises(ValidationError, match="parent references missing issue"):
        JiraState.model_validate({**state, "issues": {"MOCK-1": parent_issue}})

    linked_issue = {
        **issue,
        "fields": {
            **issue["fields"],
            "issuelinks": [
                {
                    "id": "1",
                    "type": {"id": "10001", "name": "Blocks", "inward": "is blocked by", "outward": "blocks"},
                    "inwardIssue": {"id": "10002", "key": "MOCK-2"},
                }
            ],
        },
    }
    with pytest.raises(ValidationError, match="inward link references missing issue"):
        JiraState.model_validate({**state, "issues": {"MOCK-1": linked_issue}})


def test_issue_projects_must_match_state_projects() -> None:
    issue = _issue_with_dates("2026-05-08T14:30:00Z", "2026-05-08T14:30:00Z")

    with pytest.raises(ValidationError, match="project references missing project"):
        JiraState.model_validate({"issues": {"MOCK-1": issue}})

    with pytest.raises(ValidationError, match="project id"):
        JiraState.model_validate(
            {
                "issues": {"MOCK-1": issue},
                "projects": {"MOCK": {"id": "99999", "key": "MOCK", "name": "Mock Project"}},
            }
        )

    issue_with_wrong_project = deepcopy(issue)
    issue_with_wrong_project["fields"]["project"] = {"id": "10002", "key": "TEST", "name": "Test Project"}
    with pytest.raises(ValidationError, match="key prefix"):
        JiraState.model_validate(
            {
                "issues": {"MOCK-1": issue_with_wrong_project},
                "projects": {"TEST": {"id": "10002", "key": "TEST", "name": "Test Project"}},
            }
        )


def test_issue_statuses_must_reference_configured_statuses() -> None:
    issue = _issue_with_dates("2026-05-08T14:30:00Z", "2026-05-08T14:30:00Z")
    issue["fields"]["status"] = {"id": "99999", "name": "to do"}
    statuses = {
        "10001": {
            "id": "10001",
            "name": "To Do",
            "statusCategory": {"id": 2, "key": "new", "name": "To Do", "colorName": "blue-gray"},
        }
    }
    state = JiraState.model_validate(
        {
            "issues": {"MOCK-1": issue},
            "projects": {"MOCK": {"id": "10001", "key": "MOCK", "name": "Mock Project"}},
            "statuses": statuses,
            "workflow": {"To Do": []},
        }
    )

    assert state.issues["MOCK-1"].fields.status.id == "10001"
    assert state.issues["MOCK-1"].fields.status.name == "To Do"
    assert state.issues["MOCK-1"].fields.status.statusCategory is not None
    assert state.issues["MOCK-1"].fields.status.statusCategory.key == "new"

    issue_with_missing_status = deepcopy(issue)
    issue_with_missing_status["fields"]["status"] = {"id": "10005", "name": "Blocked"}
    with pytest.raises(ValidationError, match="issue 'MOCK-1' status references missing status 'Blocked'"):
        JiraState.model_validate(
            {
                "issues": {"MOCK-1": issue_with_missing_status},
                "projects": {"MOCK": {"id": "10001", "key": "MOCK", "name": "Mock Project"}},
                "statuses": statuses,
                "workflow": {"To Do": []},
            }
        )


def test_sample_bundle_jira_state_is_loadable() -> None:
    state = json.loads((REPO_ROOT / "fixtures/sample_bundle/services/jira.json").read_text())

    JiraState.model_validate(state)


def test_workflows_must_reference_configured_statuses() -> None:
    issue = _issue_with_dates("2026-05-08T14:30:00Z", "2026-05-08T14:30:00Z")
    statuses = {
        "10001": {
            "id": "10001",
            "name": "To Do",
            "statusCategory": {"id": 2, "key": "new", "name": "To Do", "colorName": "blue-gray"},
        },
        "10002": {
            "id": "10002",
            "name": "In Progress",
            "statusCategory": {"id": 4, "key": "indeterminate", "name": "In Progress", "colorName": "yellow"},
        },
    }
    state = JiraState.model_validate(
        {
            "defaultStatusValue": "to do",
            "issues": {"MOCK-1": issue},
            "projects": {"MOCK": {"id": "10001", "key": "MOCK", "name": "Mock Project"}},
            "statuses": statuses,
            "workflow": {"to do": [{"id": "1", "name": "Start Progress", "to": "in progress"}]},
        }
    )

    assert state.defaultStatusValue == "To Do"
    assert list(state.workflow) == ["To Do"]
    assert state.workflow["To Do"][0].to == "In Progress"

    duplicate_status_names = deepcopy(statuses)
    duplicate_status_names["10003"] = {
        "id": "10003",
        "name": "to do",
        "statusCategory": {"id": 2, "key": "new", "name": "To Do", "colorName": "blue-gray"},
    }
    with pytest.raises(ValidationError, match="duplicate status name 'to do'"):
        JiraState.model_validate(
            {
                "issues": {"MOCK-1": issue},
                "projects": {"MOCK": {"id": "10001", "key": "MOCK", "name": "Mock Project"}},
                "statuses": duplicate_status_names,
                "workflow": {"To Do": []},
            }
        )

    with pytest.raises(ValidationError, match="duplicate workflow entry for status 'To Do'"):
        JiraState.model_validate(
            {
                "issues": {"MOCK-1": issue},
                "projects": {"MOCK": {"id": "10001", "key": "MOCK", "name": "Mock Project"}},
                "statuses": statuses,
                "workflow": {"To Do": [], "to do": []},
            }
        )

    with pytest.raises(ValidationError, match="workflow key 'Blocked' references missing status 'Blocked'"):
        JiraState.model_validate(
            {
                "issues": {"MOCK-1": issue},
                "projects": {"MOCK": {"id": "10001", "key": "MOCK", "name": "Mock Project"}},
                "statuses": statuses,
                "workflow": {"To Do": [], "Blocked": []},
            }
        )

    with pytest.raises(
        ValidationError, match="workflow transition '1' from 'To Do' references missing status 'Blocked'"
    ):
        JiraState.model_validate(
            {
                "issues": {"MOCK-1": issue},
                "projects": {"MOCK": {"id": "10001", "key": "MOCK", "name": "Mock Project"}},
                "statuses": statuses,
                "workflow": {"To Do": [{"id": "1", "name": "Start Blocked", "to": "Blocked"}]},
            }
        )


def test_issue_user_references_must_match_state_users() -> None:
    issue = _issue_with_dates("2026-05-08T14:30:00Z", "2026-05-08T14:30:00Z")
    issue["fields"]["assignee"] = {"accountId": "user-1", "displayName": "User 1"}

    with pytest.raises(ValidationError, match="assignee references missing user"):
        JiraState.model_validate(
            {
                "users": {},
                "issues": {"MOCK-1": issue},
                "projects": {"MOCK": {"id": "10001", "key": "MOCK", "name": "Mock Project"}},
            }
        )

    JiraState.model_validate(
        {
            "users": {"user-1": {"accountId": "user-1", "displayName": "User 1"}},
            "issues": {"MOCK-1": issue},
            "projects": {"MOCK": {"id": "10001", "key": "MOCK", "name": "Mock Project"}},
        }
    )


def test_legacy_embedded_users_are_migrated_when_users_table_is_missing() -> None:
    issue = _issue_with_dates("2026-05-08T14:30:00Z", "2026-05-08T14:30:00Z")
    issue["fields"]["assignee"] = {"accountId": "user-1", "displayName": "User 1"}

    state = JiraState.model_validate(
        {
            "issues": {"MOCK-1": issue},
            "projects": {"MOCK": {"id": "10001", "key": "MOCK", "name": "Mock Project"}},
        }
    )

    assert state.users["user-1"].displayName == "User 1"


def test_embedded_users_and_projects_are_canonicalized_from_state() -> None:
    issue = _issue_with_dates("2026-05-08T14:30:00Z", "2026-05-08T14:30:00Z")
    issue["fields"]["project"]["name"] = "Drifted Project"
    issue["fields"]["assignee"] = {"accountId": "user-1", "displayName": "Drifted User"}

    state = JiraState.model_validate(
        {
            "users": {"user-1": {"accountId": "user-1", "displayName": "Canonical User"}},
            "issues": {"MOCK-1": issue},
            "projects": {"MOCK": {"id": "10001", "key": "MOCK", "name": "Canonical Project"}},
        }
    )

    assert state.issues["MOCK-1"].fields.project.name == "Canonical Project"
    assert state.issues["MOCK-1"].fields.assignee is not None
    assert state.issues["MOCK-1"].fields.assignee.displayName == "Canonical User"


def test_boards_and_sprints_must_reference_existing_entities() -> None:
    with pytest.raises(ValidationError, match="board '1000' references missing project"):
        JiraState.model_validate({"boards": {"1000": {"id": 1000, "name": "Mock Board", "projectKey": "MOCK"}}})

    with pytest.raises(ValidationError, match="sprint '1' references missing board"):
        JiraState.model_validate(
            {
                "sprints": {
                    "1": {
                        "id": 1,
                        "self": "https://api.atlassian.com/ex/jira/mock/rest/agile/1.0/sprint/1",
                        "state": "future",
                        "name": "Sprint 1",
                        "originBoardId": 1000,
                    }
                }
            }
        )

    with pytest.raises(ValidationError, match="sprint '1' references non-scrum board"):
        JiraState.model_validate(
            {
                "projects": {"MOCK": {"id": "10001", "key": "MOCK", "name": "Mock Project"}},
                "boards": {"1000": {"id": 1000, "name": "Mock Kanban Board", "type": "kanban", "projectKey": "MOCK"}},
                "sprints": {
                    "1": {
                        "id": 1,
                        "self": "https://api.atlassian.com/ex/jira/mock/rest/agile/1.0/sprint/1",
                        "state": "future",
                        "name": "Sprint 1",
                        "originBoardId": 1000,
                    }
                },
            }
        )


def test_state_entity_ids_must_be_unique() -> None:
    issue_1 = _issue_with_dates("2026-05-08T14:30:00Z", "2026-05-08T14:30:00Z")
    issue_2 = deepcopy(issue_1)
    issue_2["key"] = "MOCK-2"

    with pytest.raises(ValidationError, match="duplicate issue id"):
        JiraState.model_validate(
            {
                "issues": {"MOCK-1": issue_1, "MOCK-2": issue_2},
                "projects": {"MOCK": {"id": "10001", "key": "MOCK", "name": "Mock Project"}},
            }
        )

    with pytest.raises(ValidationError, match="duplicate project id"):
        JiraState.model_validate(
            {
                "projects": {
                    "MOCK": {"id": "10001", "key": "MOCK", "name": "Mock Project"},
                    "TEST": {"id": "10001", "key": "TEST", "name": "Test Project"},
                }
            }
        )


def test_issue_project_and_numeric_ids_are_constrained() -> None:
    valid_issue = _issue_with_dates("2026-05-08T14:30:00Z", "2026-05-08T14:30:00Z")

    invalid_issue = {**valid_issue, "key": "mock-1"}
    with pytest.raises(ValidationError):
        JiraIssue.model_validate(invalid_issue)

    invalid_issue = {**valid_issue, "id": "abc"}
    with pytest.raises(ValidationError):
        JiraIssue.model_validate(invalid_issue)

    invalid_issue = _issue_with_dates("2026-05-08T14:30:00Z", "2026-05-08T14:30:00Z")
    invalid_issue["fields"]["project"]["key"] = "mock"
    with pytest.raises(ValidationError):
        JiraIssue.model_validate(invalid_issue)

    with pytest.raises(ValidationError):
        JiraComment.model_validate(
            {
                "id": "",
                "author": {"accountId": "commenter-001", "displayName": "User commenter-001"},
                "body": {"type": "doc", "version": 1, "content": []},
                "created": "2026-05-08T14:30:00Z",
                "updated": "2026-05-08T14:30:00Z",
            }
        )

    with pytest.raises(ValidationError):
        JiraWorklog.model_validate(
            {
                "id": "worklog-1",
                "author": {"accountId": "worker-001", "displayName": "User worker-001"},
                "updateAuthor": {"accountId": "worker-001", "displayName": "User worker-001"},
                "created": "2026-05-08T14:30:00Z",
                "updated": "2026-05-08T14:30:00Z",
                "started": "2026-05-08T14:30:00Z",
                "timeSpent": "1h",
                "timeSpentSeconds": 3600,
            }
        )

    with pytest.raises(ValidationError):
        JiraAttachment.model_validate(
            {
                "id": "file-1",
                "filename": "note.txt",
                "author": {"accountId": "uploader-001", "displayName": "User uploader-001"},
                "created": "2026-05-08T14:30:00Z",
                "size": 4,
                "mimeType": "text/plain",
                "content": "aGVsbG8=",
            }
        )


def test_issue_nested_objects_are_typed() -> None:
    data = _issue_with_dates("2026-05-08T14:30:00Z", "2026-05-08T14:30:00Z")
    data["fields"]["parent"] = {
        "id": "10000",
        "key": "mock-1",
        "fields": {
            "summary": "Parent",
            "status": {"id": "10001", "name": "To Do"},
            "issuetype": {"id": "10000", "name": "Epic"},
        },
    }

    with pytest.raises(ValidationError):
        JiraIssue.model_validate(data)

    data = _issue_with_dates("2026-05-08T14:30:00Z", "2026-05-08T14:30:00Z")
    data["fields"]["watches"] = {
        "watchCount": 2,
        "watchers": [{"accountId": "alice", "displayName": "Alice"}],
    }

    with pytest.raises(ValidationError, match="watchCount"):
        JiraIssue.model_validate(data)

    data = _issue_with_dates("2026-05-08T14:30:00Z", "2026-05-08T14:30:00Z")
    data["fields"]["timetracking"] = {"timeSpent": "1h", "timeSpentSeconds": -1}

    with pytest.raises(ValidationError):
        JiraIssue.model_validate(data)


def test_issue_expanded_objects_are_typed() -> None:
    data = _issue_with_dates("2026-05-08T14:30:00Z", "2026-05-08T14:30:00Z")
    data["fields"]["comment"] = {
        "comments": [
            {
                "id": "1",
                "author": {"accountId": "commenter-001", "displayName": "User commenter-001"},
                "body": {"not": "adf"},
                "created": "2026-05-08T14:30:00Z",
                "updated": "2026-05-08T14:30:00Z",
            }
        ],
        "maxResults": 1,
        "total": 1,
        "startAt": 0,
    }

    with pytest.raises(ValidationError):
        JiraIssue.model_validate(data)

    data = _issue_with_dates("2026-05-08T14:30:00Z", "2026-05-08T14:30:00Z")
    data["changelog"] = {
        "histories": [
            {
                "id": "history-1",
                "created": "2026-05-08T14:30:00Z",
                "items": [{"field": "status", "fromString": "To Do", "toString": "Done"}],
            }
        ]
    }

    with pytest.raises(ValidationError):
        JiraIssue.model_validate(data)

    data = _issue_with_dates("2026-05-08T14:30:00Z", "2026-05-08T14:30:00Z")
    data["transitions"] = [{"id": "start", "name": "Start Progress", "to": {"id": "10002", "name": "In Progress"}}]

    with pytest.raises(ValidationError):
        JiraIssue.model_validate(data)


def test_field_schema_is_typed() -> None:
    field = JiraField.model_validate(
        {
            "id": "customfield_10001",
            "key": "customfield_10001",
            "name": "Story Points",
            "custom": True,
            "schema": {"type": "number", "customId": 10001},
        }
    )

    assert field.schema_ is not None
    assert field.schema_.type == "number"

    with pytest.raises(ValidationError):
        JiraField.model_validate(
            {
                "id": "customfield_10001",
                "key": "customfield_10001",
                "name": "Story Points",
                "custom": True,
                "schema": {"customId": -1},
            }
        )
