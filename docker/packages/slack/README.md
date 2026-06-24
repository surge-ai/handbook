# Slack Mock MCP Server

A Python mock Slack MCP server for testing and development. This server provides offline Slack-like tools backed by JSON state and Pydantic validation.

## Tools

The server exposes tools for:

- channels and direct messages
- messages and thread replies
- search
- reactions and pins
- users, profiles, presence, and status
- file upload/listing
- state import/export for fixture setup and grading

## Usage

### Development

```bash
uv run --package slack-mock python -m slack_mock
```

### Tests

```bash
uv run --package slack-mock pytest packages/slack/tests
```

### MCP Configuration

`packages/slack/mcp.json` runs the server through `uv run --package slack-mock python -m slack_mock`.

## Notes

This is a fully offline mock server. No actual Slack API calls are made.
