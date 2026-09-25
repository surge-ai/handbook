"""Small HTTP viewer for the Jira mock."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import uvicorn
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, Response
from starlette.routing import Route

from jira_mock.state import get_state


class ProxyAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        if request.url.path.startswith("/mcp"):
            return await call_next(request)
        proxy_token = os.environ.get("MCP_PROXY_TOKEN", "")
        if proxy_token and request.headers.get("x-proxy-token") != proxy_token:
            return JSONResponse({"error": "Forbidden: invalid proxy token"}, status_code=403)
        return await call_next(request)


def _dump(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json", by_alias=True, exclude_none=True)
    if isinstance(value, list):
        return [_dump(item) for item in value]
    if isinstance(value, dict):
        return {key: _dump(item) for key, item in value.items()}
    return value


async def projects(_request: Request) -> JSONResponse:
    state = get_state()
    projects_data = [
        {
            "key": project.key,
            "name": project.name,
            "description": project.description,
            "issueCount": len([key for key in state.issues if key.startswith(f"{project.key}-")]),
        }
        for project in state.projects.values()
    ]
    return JSONResponse({"projects": projects_data})


def _extract_text(doc: Any) -> str:
    if doc is None:
        return ""
    if isinstance(doc, str):
        return doc
    data = _dump(doc)
    if not isinstance(data, dict):
        return ""
    content = data.get("content")
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        block_content = block.get("content")
        if not isinstance(block_content, list):
            continue
        parts.append("".join(str(inline.get("text", "")) for inline in block_content if isinstance(inline, dict)))
    return "\n".join(parts)


def _format_issue_summary(issue: Any) -> dict[str, Any]:
    return {
        "key": issue.key,
        "summary": issue.fields.summary,
        "status": issue.fields.status.name,
        "statusCategory": issue.fields.status.statusCategory.key if issue.fields.status.statusCategory else "undefined",
        "priority": issue.fields.priority.name if issue.fields.priority else "None",
        "type": issue.fields.issuetype.name,
        "assignee": issue.fields.assignee.displayName if issue.fields.assignee else "Unassigned",
        "reporter": issue.fields.reporter.displayName if issue.fields.reporter else "",
        "created": issue.fields.created,
        "updated": issue.fields.updated,
        "labels": issue.fields.labels or [],
        "project": issue.fields.project.key,
        "attachmentCount": len(issue.fields.attachment or []),
    }


def _format_issue_detail(issue: Any) -> dict[str, Any]:
    return {
        **_format_issue_summary(issue),
        "description": _extract_text(issue.fields.description),
        "components": [component.name for component in issue.fields.components or []],
        "links": [
            {
                "type": link.type.name,
                "direction": "inward" if link.inwardIssue else "outward",
                "key": (link.inwardIssue or link.outwardIssue).key if (link.inwardIssue or link.outwardIssue) else None,
                "summary": (link.inwardIssue or link.outwardIssue).fields.summary
                if (link.inwardIssue or link.outwardIssue) and (link.inwardIssue or link.outwardIssue).fields
                else None,
            }
            for link in issue.fields.issuelinks or []
        ],
        # Attachment metadata only -- the stored base64 ``content`` is never
        # exposed by the viewer (parity with the get_attachments tool).
        "attachments": [
            {
                "id": attachment.id,
                "filename": attachment.filename,
                "size": attachment.size,
                "mimeType": attachment.mimeType,
                "created": attachment.created,
                "author": attachment.author.displayName if attachment.author else "",
            }
            for attachment in issue.fields.attachment or []
        ],
    }


def _issue_extra_field(issue: Any, key: str) -> Any:
    return (issue.fields.model_extra or {}).get(key)


async def issues(request: Request) -> JSONResponse:
    issues_list = list(get_state().issues.values())
    if project := request.query_params.get("project"):
        issues_list = [issue for issue in issues_list if issue.key.startswith(f"{project}-")]
    if status := request.query_params.get("status"):
        issues_list = [issue for issue in issues_list if issue.fields.status.name.lower() == status.lower()]
    if assignee := request.query_params.get("assignee"):
        issues_list = [
            issue
            for issue in issues_list
            if issue.fields.assignee and assignee.lower() in issue.fields.assignee.displayName.lower()
        ]
    if issue_type := request.query_params.get("type"):
        issues_list = [issue for issue in issues_list if issue.fields.issuetype.name.lower() == issue_type.lower()]
    issues_list.sort(key=lambda issue: issue.fields.updated, reverse=True)
    mapped = [_format_issue_summary(issue) for issue in issues_list]
    return JSONResponse({"issues": mapped, "total": len(mapped)})


async def issue_detail(request: Request) -> JSONResponse:
    state = get_state()
    issue = state.issues.get(request.path_params["key"])
    if issue is None:
        return JSONResponse({"error": "Issue not found"}, status_code=404)
    comments = [
        {
            "id": comment.id,
            "author": comment.author.displayName,
            "body": _extract_text(comment.body),
            "created": comment.created,
        }
        for comment in state.comments.get(request.path_params["key"], [])
    ]
    return JSONResponse({"issue": _format_issue_detail(issue), "comments": comments})


async def sprints(_request: Request) -> JSONResponse:
    state = get_state()
    sprints_data = [
        {
            "id": sprint.id,
            "name": sprint.name,
            "state": sprint.state,
            "startDate": sprint.startDate,
            "endDate": sprint.endDate,
            "goal": sprint.goal,
            "issueCount": len(
                [
                    issue
                    for issue in state.issues.values()
                    if _issue_extra_field(issue, "customfield_10002") == sprint.id
                    or (_dump(_issue_extra_field(issue, "sprint") or {}).get("id") == sprint.id)
                ]
            ),
        }
        for sprint in state.sprints.values()
    ]
    return JSONResponse({"sprints": sprints_data})


async def statuses(_request: Request) -> JSONResponse:
    statuses_data = sorted({issue.fields.status.name for issue in get_state().issues.values()})
    return JSONResponse({"statuses": statuses_data})


async def index(_request: Request) -> HTMLResponse:
    return HTMLResponse(VIEWER_HTML)


VIEWER_HTML = (Path(__file__).parent / "viewer.html").read_text(encoding="utf-8")


def create_app() -> Starlette:
    routes = [
        Route("/", index),
        Route("/api/projects", projects),
        Route("/api/issues", issues),
        Route("/api/issues/{key}", issue_detail),
        Route("/api/sprints", sprints),
        Route("/api/statuses", statuses),
    ]
    return Starlette(routes=routes, middleware=[Middleware(ProxyAuthMiddleware)])


def run_http_server(_mcp, port: int) -> None:
    if hasattr(_mcp, "streamable_http_app"):
        fastmcp_asgi = _mcp.streamable_http_app()
    else:
        fastmcp_asgi = _mcp.http_app(transport="streamable-http", path="/mcp")

    viewer = create_app()

    async def combined_app(scope, receive, send):
        if scope["type"] == "lifespan":
            await fastmcp_asgi(scope, receive, send)
            return
        if scope.get("path", "").startswith("/mcp"):
            await fastmcp_asgi(scope, receive, send)
        else:
            await viewer(scope, receive, send)

    uvicorn.run(combined_app, host="127.0.0.1", port=port, log_level="warning")
