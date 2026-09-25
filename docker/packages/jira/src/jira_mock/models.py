"""Pydantic models for Jira mock state.

This first Python port intentionally mirrors the TypeScript server's permissive
state shape. The next pass can tighten IDs, enums, dates, and cross-reference
invariants once parity is stable.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Annotated, Any, Literal, Self
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, EmailStr, Field, StringConstraints, field_validator, model_validator

NonEmptyStateString = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
ShortNameString = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)]
NumericIdString = Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^\d+$")]
NonNegativeInt = Annotated[int, Field(ge=0)]
IssueKey = Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^[A-Z][A-Z0-9_]*-\d+$")]
ProjectKey = Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^[A-Z][A-Z0-9_]*$")]
AccountId = NonEmptyStateString
JiraSiteId = NonEmptyStateString
JiraAccountType = Literal["atlassian", "app", "customer"]
JiraTimeZone = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1),
    Field(description="IANA time zone name, for example America/New_York."),
]
IssueTypeName = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=255),
    Field(
        description="Jira issue type name. Common defaults include Task, Bug, Story, Epic, and Sub-task; custom issue types are allowed.",
        examples=["Task", "Bug", "Story", "Epic", "Sub-task"],
    ),
]
PriorityName = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=255),
    Field(
        description="Jira priority name. Common defaults include Highest, High, Medium, Low, and Lowest; custom priorities are allowed.",
        examples=["Highest", "High", "Medium", "Low", "Lowest"],
    ),
]
JiraDateTime = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})$",
    ),
]
JiraTimeSpent = Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^(?:\d+\s*[wdhm]\s*)+$")]
Base64String = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True, pattern=r"^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$"
    ),
]
SprintState = Literal["active", "closed", "future"]
BoardType = Literal["scrum", "kanban"]
StatusCategoryKey = Literal["new", "indeterminate", "done", "undefined"]
AdfBlockType = Literal[
    "paragraph",
    "heading",
    "bulletList",
    "orderedList",
    "listItem",
    "codeBlock",
    "blockquote",
    "rule",
    "table",
]
AdfInlineType = Literal["text", "hardBreak", "mention", "emoji", "inlineCard"]


class FlexibleModel(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True, validate_assignment=True)

    @model_validator(mode="before")
    @classmethod
    def strip_fake_self_url(cls, data: Any) -> Any:
        if isinstance(data, dict) and "self" in data:
            data = {key: value for key, value in data.items() if key != "self"}
        return data


class JiraUser(FlexibleModel):
    accountId: AccountId
    accountType: JiraAccountType = "atlassian"
    emailAddress: EmailStr | None = None
    displayName: NonEmptyStateString
    active: bool = True
    timeZone: JiraTimeZone | None = None
    avatarUrls: dict[str, str] | None = None

    @field_validator("timeZone")
    @classmethod
    def validate_time_zone(cls, value: str | None) -> str | None:
        if value is None:
            return None
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"Unknown IANA time zone: {value}") from exc
        return value


class JiraIssueType(FlexibleModel):
    id: NumericIdString
    name: IssueTypeName
    description: str | None = None
    iconUrl: str | None = None
    subtask: bool = False
    hierarchyLevel: int | None = None


class JiraStatusCategory(FlexibleModel):
    id: int | str
    key: StatusCategoryKey
    name: NonEmptyStateString
    colorName: NonEmptyStateString


class JiraStatus(FlexibleModel):
    id: NumericIdString
    name: NonEmptyStateString
    description: str | None = None
    iconUrl: str | None = None
    statusCategory: JiraStatusCategory | None = None


class JiraPriority(FlexibleModel):
    id: NumericIdString
    name: PriorityName
    description: str | None = None
    iconUrl: str | None = None


class JiraProject(FlexibleModel):
    id: NumericIdString
    key: ProjectKey
    name: NonEmptyStateString
    description: str | None = None
    projectTypeKey: str | None = None
    simplified: bool | None = None
    avatarUrls: dict[str, str] | None = None


class JiraBoard(FlexibleModel):
    id: int
    name: NonEmptyStateString
    type: BoardType = "scrum"
    projectKey: ProjectKey
    filterJql: str | None = None


class JiraComponent(FlexibleModel):
    id: NumericIdString | None = None
    name: ShortNameString
    description: str | None = None
    lead: JiraUser | None = None
    assigneeType: str | None = None
    project: str | None = None


class JiraVersion(FlexibleModel):
    id: NumericIdString | None = None
    name: ShortNameString
    description: str | None = None
    archived: bool | None = None
    released: bool | None = None
    releaseDate: str | None = None


class JiraInlineContent(FlexibleModel):
    type: AdfInlineType
    text: str | None = None
    marks: list[dict[str, Any]] | None = None
    attrs: dict[str, Any] | None = None


class JiraContentBlock(FlexibleModel):
    type: AdfBlockType
    content: list[JiraInlineContent] | None = None
    attrs: dict[str, Any] | None = None


class JiraDocumentContent(FlexibleModel):
    type: Literal["doc"]
    version: Literal[1]
    content: list[JiraContentBlock] = Field(default_factory=list)


class JiraIssueLinkType(FlexibleModel):
    id: NumericIdString
    name: NonEmptyStateString
    inward: NonEmptyStateString
    outward: NonEmptyStateString


class JiraWorkflowTransitionConfig(FlexibleModel):
    id: NumericIdString
    name: ShortNameString
    to: ShortNameString


class JiraLinkedIssueFields(FlexibleModel):
    summary: NonEmptyStateString
    status: JiraStatus
    issuetype: JiraIssueType


class JiraLinkedIssue(FlexibleModel):
    id: NumericIdString
    key: IssueKey
    fields: JiraLinkedIssueFields | None = None


class JiraIssueLink(FlexibleModel):
    id: NonEmptyStateString
    type: JiraIssueLinkType
    inwardIssue: JiraLinkedIssue | None = None
    outwardIssue: JiraLinkedIssue | None = None


class JiraComment(FlexibleModel):
    id: NumericIdString
    author: JiraUser
    body: JiraDocumentContent | str
    created: JiraDateTime
    updated: JiraDateTime
    updateAuthor: JiraUser | None = None


class JiraWorklog(FlexibleModel):
    id: NumericIdString
    author: JiraUser
    updateAuthor: JiraUser
    comment: JiraDocumentContent | str | None = None
    created: JiraDateTime
    updated: JiraDateTime
    started: JiraDateTime
    timeSpent: JiraTimeSpent
    timeSpentSeconds: NonNegativeInt


class JiraAttachment(FlexibleModel):
    id: NumericIdString
    filename: NonEmptyStateString
    author: JiraUser
    created: JiraDateTime
    size: NonNegativeInt
    mimeType: NonEmptyStateString
    content: Base64String
    thumbnail: str | None = None


class JiraCommentPage(FlexibleModel):
    comments: list[JiraComment] = Field(default_factory=list)
    maxResults: NonNegativeInt
    total: NonNegativeInt
    startAt: NonNegativeInt = 0


class JiraWorklogPage(FlexibleModel):
    worklogs: list[JiraWorklog] = Field(default_factory=list)
    maxResults: NonNegativeInt
    total: NonNegativeInt
    startAt: NonNegativeInt = 0


class JiraWatches(FlexibleModel):
    watchCount: NonNegativeInt = 0
    isWatching: bool = False
    watchers: list[JiraUser] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_watch_count_matches_watchers(self) -> Self:
        if self.watchCount != len(self.watchers):
            raise ValueError("watchCount must match number of watchers")
        return self


class JiraTimeTracking(FlexibleModel):
    originalEstimate: JiraTimeSpent | None = None
    remainingEstimate: JiraTimeSpent | None = None
    timeSpent: JiraTimeSpent | None = None
    originalEstimateSeconds: NonNegativeInt | None = None
    remainingEstimateSeconds: NonNegativeInt | None = None
    timeSpentSeconds: NonNegativeInt | None = None


class JiraChangelogItem(FlexibleModel):
    field: NonEmptyStateString
    fieldtype: NonEmptyStateString | None = None
    fieldId: NonEmptyStateString | None = None
    from_: str | None = Field(default=None, alias="from")
    fromString: str | None = None
    to: str | None = None
    toString: str | None = None


class JiraChangelogHistory(FlexibleModel):
    id: NumericIdString
    author: JiraUser | None = None
    created: JiraDateTime
    items: list[JiraChangelogItem] = Field(default_factory=list)


class JiraChangelog(FlexibleModel):
    histories: list[JiraChangelogHistory] = Field(default_factory=list)
    maxResults: NonNegativeInt | None = None
    total: NonNegativeInt | None = None
    startAt: NonNegativeInt | None = None


class JiraTransition(FlexibleModel):
    id: NumericIdString
    name: ShortNameString
    to: JiraStatus
    hasScreen: bool = False
    isGlobal: bool = False
    isInitial: bool = False
    isAvailable: bool = True
    isConditional: bool = False
    isLooped: bool = False


class JiraFieldSchema(FlexibleModel):
    type: NonEmptyStateString
    system: NonEmptyStateString | None = None
    custom: NonEmptyStateString | None = None
    customId: NonNegativeInt | None = None
    items: NonEmptyStateString | None = None


class JiraIssueFields(FlexibleModel):
    summary: NonEmptyStateString
    description: JiraDocumentContent | str | None = None
    issuetype: JiraIssueType
    project: JiraProject
    status: JiraStatus
    priority: JiraPriority | None = None
    assignee: JiraUser | None = None
    reporter: JiraUser | None = None
    creator: JiraUser | None = None
    created: JiraDateTime
    updated: JiraDateTime
    resolutiondate: JiraDateTime | None = None
    labels: list[str] = Field(default_factory=list)
    components: list[JiraComponent] = Field(default_factory=list)
    fixVersions: list[JiraVersion] = Field(default_factory=list)
    versions: list[JiraVersion] = Field(default_factory=list)
    parent: JiraLinkedIssue | None = None
    subtasks: list[JiraLinkedIssue] | None = None
    issuelinks: list[JiraIssueLink] | None = None
    comment: JiraCommentPage | None = None
    worklog: JiraWorklogPage | None = None
    attachment: list[JiraAttachment] | None = None
    watches: JiraWatches | None = None
    timetracking: JiraTimeTracking | None = None


class JiraIssue(FlexibleModel):
    id: NumericIdString
    key: IssueKey
    expand: str | None = None
    fields: JiraIssueFields
    renderedFields: dict[str, Any] | None = None
    changelog: JiraChangelog | None = None
    transitions: list[JiraTransition] | None = None


class JiraSprint(FlexibleModel):
    id: int
    state: SprintState
    name: NonEmptyStateString
    startDate: JiraDateTime | None = None
    endDate: JiraDateTime | None = None
    completeDate: JiraDateTime | None = None
    originBoardId: int
    goal: str | None = None


class JiraField(FlexibleModel):
    id: NonEmptyStateString
    key: NonEmptyStateString
    name: NonEmptyStateString
    custom: bool
    orderable: bool | None = None
    navigable: bool | None = None
    searchable: bool | None = None
    clauseNames: list[str] | None = None
    schema_: JiraFieldSchema | None = Field(default=None, alias="schema")


class JiraCounters(FlexibleModel):
    issueId: int = 10000
    sprintId: int = 1000
    boardId: int = 1001
    commentId: int = 0
    worklogId: int = 0
    attachmentId: int = 0
    issueLinkId: int = 0


class JiraState(FlexibleModel):
    is_admin: bool = False
    defaultStatusValue: ShortNameString = "To Do"
    currentUserAccountId: AccountId | None = None
    users: dict[AccountId, JiraUser] = Field(default_factory=dict)
    issues: dict[str, JiraIssue] = Field(default_factory=dict)
    sprints: dict[str, JiraSprint] = Field(default_factory=dict)
    comments: dict[str, list[JiraComment]] = Field(default_factory=dict)
    worklogs: dict[str, list[JiraWorklog]] = Field(default_factory=dict)
    projects: dict[str, JiraProject] = Field(default_factory=dict)
    boards: dict[str, JiraBoard] = Field(default_factory=dict)
    fields: list[JiraField] = Field(default_factory=list)
    linkTypes: list[JiraIssueLinkType] = Field(default_factory=list)
    statuses: dict[NumericIdString, JiraStatus] = Field(default_factory=dict)
    workflow: dict[ShortNameString, list[JiraWorkflowTransitionConfig]] = Field(default_factory=dict)
    counters: JiraCounters = Field(default_factory=JiraCounters)

    @model_validator(mode="before")
    @classmethod
    def populate_users_from_legacy_embedded_objects(cls, data: Any) -> Any:
        if not isinstance(data, dict) or "users" in data:
            return data

        users: dict[str, Any] = {}

        def add_user(value: Any) -> None:
            if isinstance(value, dict) and isinstance(value.get("accountId"), str):
                users.setdefault(value["accountId"], value)

        for issue in (data.get("issues") or {}).values():
            if not isinstance(issue, dict):
                continue
            fields = issue.get("fields") or {}
            if not isinstance(fields, dict):
                continue
            for field in ("assignee", "reporter", "creator"):
                add_user(fields.get(field))
            for component in fields.get("components") or []:
                if isinstance(component, dict):
                    add_user(component.get("lead"))
            for attachment in fields.get("attachment") or []:
                if isinstance(attachment, dict):
                    add_user(attachment.get("author"))
            watches = fields.get("watches") or {}
            if isinstance(watches, dict):
                for watcher in watches.get("watchers") or []:
                    add_user(watcher)
            changelog = issue.get("changelog") or {}
            if isinstance(changelog, dict):
                for history in changelog.get("histories") or []:
                    if isinstance(history, dict):
                        add_user(history.get("author"))

        for comments in (data.get("comments") or {}).values():
            for comment in comments or []:
                if isinstance(comment, dict):
                    add_user(comment.get("author"))
                    add_user(comment.get("updateAuthor"))

        for worklogs in (data.get("worklogs") or {}).values():
            for worklog in worklogs or []:
                if isinstance(worklog, dict):
                    add_user(worklog.get("author"))
                    add_user(worklog.get("updateAuthor"))

        if users:
            return {**data, "users": users}
        return data

    @model_validator(mode="after")
    def validate_keys_and_issue_references(self) -> Self:
        def require_unique(label: str, pairs: Iterable[tuple[str, str]]) -> None:
            seen: dict[str, str] = {}
            for owner, value in pairs:
                if value in seen:
                    raise ValueError(f"duplicate {label} id {value!r} on {owner!r} and {seen[value]!r}")
                seen[value] = owner

        for key, issue in self.issues.items():
            if key != issue.key:
                raise ValueError(f"issues key {key!r} does not match issue.key {issue.key!r}")

        for key, user in self.users.items():
            if key != user.accountId:
                raise ValueError(f"users key {key!r} does not match user.accountId {user.accountId!r}")

        if self.currentUserAccountId is not None and self.currentUserAccountId not in self.users:
            raise ValueError(f"currentUserAccountId {self.currentUserAccountId!r} does not reference an existing user")

        for key, project in self.projects.items():
            if key != project.key:
                raise ValueError(f"projects key {key!r} does not match project.key {project.key!r}")

        for key, sprint in self.sprints.items():
            if key != str(sprint.id):
                raise ValueError(f"sprints key {key!r} does not match sprint.id {sprint.id!r}")

        for key, board in self.boards.items():
            if key != str(board.id):
                raise ValueError(f"boards key {key!r} does not match board.id {board.id!r}")

        for key, status in self.statuses.items():
            if key != status.id:
                raise ValueError(f"statuses key {key!r} does not match status.id {status.id!r}")

        if self.statuses or self.workflow:
            status_by_name: dict[str, JiraStatus] = {}
            for status in self.statuses.values():
                normalized_name = status.name.lower()
                if normalized_name in status_by_name:
                    raise ValueError(
                        f"duplicate status name {status.name!r} on status ids {status.id!r} and {status_by_name[normalized_name].id!r}"
                    )
                status_by_name[normalized_name] = status

            def require_configured_status(name: str, owner: str) -> JiraStatus:
                status = status_by_name.get(name.lower())
                if status is None:
                    raise ValueError(f"{owner} references missing status {name!r}")
                return status

            default_status = status_by_name.get(self.defaultStatusValue.lower())
            if default_status is None:
                raise ValueError(
                    f"defaultStatusValue {self.defaultStatusValue!r} does not reference a configured status"
                )
            canonical_workflow: dict[ShortNameString, list[JiraWorkflowTransitionConfig]] = {}
            for from_status_name, transitions in self.workflow.items():
                from_status = require_configured_status(from_status_name, f"workflow key {from_status_name!r}")
                if from_status.name in canonical_workflow:
                    raise ValueError(f"duplicate workflow entry for status {from_status.name!r}")
                for transition in transitions:
                    to_status = require_configured_status(
                        transition.to, f"workflow transition {transition.id!r} from {from_status.name!r}"
                    )
                    transition.to = to_status.name
                canonical_workflow[from_status.name] = transitions

            if default_status.name not in canonical_workflow:
                raise ValueError(f"defaultStatusValue {default_status.name!r} does not have a workflow entry")
            object.__setattr__(self, "defaultStatusValue", default_status.name)
            object.__setattr__(self, "workflow", canonical_workflow)

            for issue in self.issues.values():
                status = require_configured_status(issue.fields.status.name, f"issue {issue.key!r} status")
                issue.fields.status = status

        require_unique("project", [(project.key, project.id) for project in self.projects.values()])
        require_unique("issue", [(issue.key, issue.id) for issue in self.issues.values()])
        require_unique("sprint", [(key, str(sprint.id)) for key, sprint in self.sprints.items()])
        require_unique("board", [(board.name, str(board.id)) for board in self.boards.values()])
        require_unique("status", [(status.name, status.id) for status in self.statuses.values()])
        require_unique("field", [(field.key, field.id) for field in self.fields])
        require_unique("linkType", [(link_type.name, link_type.id) for link_type in self.linkTypes])
        require_unique(
            "comment",
            [
                (f"{issue_key}:{comment.id}", comment.id)
                for issue_key, comments in self.comments.items()
                for comment in comments
            ],
        )
        require_unique(
            "worklog",
            [
                (f"{issue_key}:{worklog.id}", worklog.id)
                for issue_key, worklogs in self.worklogs.items()
                for worklog in worklogs
            ],
        )
        require_unique(
            "attachment",
            (
                (f"{issue.key}:{attachment.id}", attachment.id)
                for issue in self.issues.values()
                for attachment in issue.fields.attachment or []
            ),
        )
        require_unique(
            "issueLink",
            (
                (f"{issue.key}:{link.id}", link.id)
                for issue in self.issues.values()
                for link in issue.fields.issuelinks or []
            ),
        )

        def canonical_user(owner: str, user: JiraUser | None) -> JiraUser | None:
            if user is None:
                return None
            canonical = self.users.get(user.accountId)
            if canonical is None:
                raise ValueError(f"{owner} references missing user {user.accountId!r}")
            return canonical

        def canonical_required_user(owner: str, user: JiraUser) -> JiraUser:
            canonical = canonical_user(owner, user)
            if canonical is None:
                raise ValueError(f"{owner} references missing user")
            return canonical

        for key in self.comments:
            if key not in self.issues:
                raise ValueError(f"comments key {key!r} does not reference an existing issue")
            for comment in self.comments[key]:
                comment.author = canonical_required_user(f"comment {comment.id!r} author", comment.author)
                comment.updateAuthor = canonical_user(f"comment {comment.id!r} updateAuthor", comment.updateAuthor)

        for key in self.worklogs:
            if key not in self.issues:
                raise ValueError(f"worklogs key {key!r} does not reference an existing issue")
            for worklog in self.worklogs[key]:
                worklog.author = canonical_required_user(f"worklog {worklog.id!r} author", worklog.author)
                worklog.updateAuthor = canonical_required_user(
                    f"worklog {worklog.id!r} updateAuthor", worklog.updateAuthor
                )

        for key, board in self.boards.items():
            if board.projectKey not in self.projects:
                raise ValueError(f"board {key!r} references missing project {board.projectKey!r}")

        for key, sprint in self.sprints.items():
            board = self.boards.get(str(sprint.originBoardId))
            if board is None:
                raise ValueError(f"sprint {key!r} references missing board {sprint.originBoardId!r}")
            if board.type != "scrum":
                raise ValueError(f"sprint {key!r} references non-scrum board {board.id!r}")

        for key, issue in self.issues.items():
            issue_project_key = issue.fields.project.key
            if key.split("-", 1)[0] != issue_project_key:
                raise ValueError(f"issue {key!r} key prefix does not match project {issue_project_key!r}")

            project = self.projects.get(issue_project_key)
            if project is None:
                raise ValueError(f"issue {key!r} project references missing project {issue_project_key!r}")
            if project.id != issue.fields.project.id:
                raise ValueError(
                    f"issue {key!r} project id {issue.fields.project.id!r} does not match state project id {project.id!r}"
                )
            issue.fields.project = project

            issue.fields.assignee = canonical_user(f"issue {key!r} assignee", issue.fields.assignee)
            issue.fields.reporter = canonical_user(f"issue {key!r} reporter", issue.fields.reporter)
            issue.fields.creator = canonical_user(f"issue {key!r} creator", issue.fields.creator)

            for component in issue.fields.components:
                component.lead = canonical_user(f"issue {key!r} component {component.name!r} lead", component.lead)

            for attachment in issue.fields.attachment or []:
                attachment.author = canonical_required_user(
                    f"issue {key!r} attachment {attachment.id!r} author", attachment.author
                )

            if issue.fields.watches is not None:
                issue.fields.watches.watchers = [
                    canonical_required_user(f"issue {key!r} watcher", watcher)
                    for watcher in issue.fields.watches.watchers
                ]

            if issue.changelog is not None:
                for history in issue.changelog.histories:
                    history.author = canonical_user(
                        f"issue {key!r} changelog history {history.id!r} author", history.author
                    )

            if issue.fields.parent is not None and issue.fields.parent.key not in self.issues:
                raise ValueError(f"issue {key!r} parent references missing issue {issue.fields.parent.key!r}")

            for subtask in issue.fields.subtasks or []:
                if subtask.key not in self.issues:
                    raise ValueError(f"issue {key!r} subtask references missing issue {subtask.key!r}")

            for link in issue.fields.issuelinks or []:
                if link.inwardIssue is not None and link.inwardIssue.key not in self.issues:
                    raise ValueError(f"issue {key!r} inward link references missing issue {link.inwardIssue.key!r}")
                if link.outwardIssue is not None and link.outwardIssue.key not in self.issues:
                    raise ValueError(f"issue {key!r} outward link references missing issue {link.outwardIssue.key!r}")

        return self


class JiraSitesState(FlexibleModel):
    sites: dict[JiraSiteId, JiraState] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _has_sites(self) -> Self:
        if not self.sites:
            raise ValueError("sites must contain at least one Jira site")
        return self


JiraMockState = JiraState | JiraSitesState
