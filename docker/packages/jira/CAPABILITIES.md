# Jira Capabilities

A mock Jira project tracker with issues, sprints, workflow transitions, time tracking, watchers, comments, attachments, and issue linking.

## What the agent can do

**Create and manage issues.** Create issues (Stories, Tasks, Bugs, Epics, Sub-tasks), update fields (summary, description, priority, assignee, labels), delete, and search by JQL-style queries (with `~` fuzzy-match operator support). Search supports common equality, `IN`, `!=`, `NOT IN`, `IS EMPTY`, and date comparison filters for core fields including assignee, priority, status, statusCategory, resolution, parent, fixVersion, due, sprint, labels, and components. Browse issues by project or by epic. Link issues together (blocks, relates to, duplicates, clones).

**Workflow transitions.** Move issues through configured workflows. The default mock workflow is To Do → In Progress → In Review → Done (reopen also supported), and admin tools can add custom statuses or workflow transitions. The agent must use proper transitions — cannot jump directly from "To Do" to "Done" unless that transition exists. Query available transitions from the current status. Tests whether the agent understands process flow.

**Sprint management.** Discover seeded Scrum/Kanban boards, create admin-managed boards, create sprints on Scrum boards, view sprints and their issues, update sprint details (name, dates, state). Browse issues assigned to a specific sprint.

**Time tracking.** Log time spent on issues using Jira notation (e.g., "2h 30m", "1d 4h"). Set and update original and remaining time estimates. View all worklogs for an issue with a summary comparing estimated vs. actual time.

**Collaboration.** Add comments, attach files (with automatic MIME type detection), and manage watchers (add, remove, list who is watching an issue).

**User management.** Discover Jira users, inspect the currently authenticated user, and let admins create additional users for seeded worlds. Issue assignees, reporters, creators, watchers, comments, worklogs, and attachments reference state-level users by `accountId`. Tool-authored records use `currentUserAccountId`.

## Coverage gaps

- No board configuration tools beyond admin board creation; boards can also be seeded/imported
- No project creation or configuration
- No permissions or role-based access
- No notifications or @mentions
- No dashboard or reporting tools
- No bulk operations
- Sprint search uses configured fields named `Sprint`, with `customfield_10002` kept as the default mock convention
- No full workflow-screen or workflow-scheme modeling; custom status/transition tools cover the lightweight workflow cases

## Toolsets

34 tools total, including state import/export. `all` / `jira_all` contains the 32 non-state tools. Toolsets map to `WORLDBENCH_TOOL_SETS` values (prefixed form — e.g., `jira_issues`).

| Toolset | Tools | Description |
|---------|-------|-------------|
| `all` / `jira_all` | 32 | Everything |
| `read` / `jira_read` | 15 | All read-only tools |
| `write` / `jira_write` | 17 | All write tools |
| `jira_users` | 3 | Current-user lookup, user discovery, and admin user creation |
| `jira_issues` | 10 | Core issue CRUD: create, get, update, delete, search, project/epic issues, links, search fields |
| `jira_workflow` | 4 | Status transitions and workflow configuration: `create_status`, `get_transitions`, `transition_issue`, `upsert_workflow_transition` |
| `jira_sprints` | 6 | Sprint management: discover/create boards, create/update sprints, get sprints, get sprint issues |
| `jira_time` | 3 | Time tracking: add worklog, get worklogs, update estimate |
| `jira_collaboration` | 6 | Comments, watchers, attachments: add/get attachments, add comment, add/remove/get watchers |
| `jira_admin` | 7 | Admin-only operations: create users/boards, create/update sprint, delete issue, create statuses, configure workflow transitions |
| `jira_core` | 10 | Baseline work tracking: search, get/update issue, add comment, project issues, transitions, create issue, current-user/user lookup |
| `jira_toolathlon_legacy` | 15 | Legacy Toolathlon tool subset (pre-integration) |
| `jira_state` | 2 | `export_state`, `import_state` for fixture seeding and grading |
