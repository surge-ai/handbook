# Jira Mock MCP Server

Python mock Jira MCP server for offline Syntara tasks. It stores all service state in JSON and validates state with Pydantic models before writes.

## Run

```bash
uv run --package jira-mock python -m jira_mock
```

## Test

```bash
uv run pytest packages/jira/tests
```

## Available Tool Areas

- Issues: search, get, create, update, delete, project/epic issue listing, and issue linking.
- Workflow: list transitions, transition issues, create custom statuses, and define workflow transitions.
- Sprints: discover/create boards, create/update sprints on Scrum boards, list sprints, and inspect sprint issues.
- Time tracking: add worklogs, list worklogs, and update estimates.
- Collaboration: comments, watchers, and attachments.
- Users: inspect the current user, discover users, and admin-create additional users for assignment/watchers.
- State: `export_state` and `import_state` for fixture seeding and grading.

## Environment Variables

| Variable | Description |
|----------|-------------|
| `PORT` | HTTP server port. |
| `MCP_PROXY_TOKEN` | Optional proxy auth token for non-MCP viewer routes. |
| `AGENT_WORKSPACE` | Workspace root used for attachment path resolution. |
| `BUNDLEDIR` | Bundle directory used to locate seeded service state. |
| `BUNDLE_OUTPUT_DIR` | Snapshot output directory. |
| `OUTPUTDIR` | Legacy snapshot output directory fallback. |
