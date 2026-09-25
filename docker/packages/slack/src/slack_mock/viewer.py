"""Slack viewer and HTTP MCP host."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import uvicorn
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, Response
from starlette.routing import Route


class ProxyTokenMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.url.path.startswith("/mcp"):
            return await call_next(request)
        token = os.environ.get("MCP_PROXY_TOKEN", "")
        if token and request.headers.get("x-proxy-token") != token:
            return Response("Forbidden: invalid proxy token", status_code=403)
        return await call_next(request)


def _state_json() -> dict[str, Any]:
    from slack_mock.state import state_to_json

    return state_to_json()


def _topic_value(topic: Any) -> str:
    if isinstance(topic, dict):
        return str(topic.get("value") or "")
    return str(topic or "")


def _user_display_name(user: dict[str, Any] | None, fallback: str | None = None) -> str:
    if user is None:
        return fallback or "Unknown"
    profile = user.get("profile", {})
    return (
        profile.get("display_name")
        or profile.get("real_name")
        or user.get("real_name")
        or user.get("name")
        or fallback
        or "Unknown"
    )


def _format_ts(ts: str) -> str:
    try:
        dt = datetime.fromtimestamp(float(ts), tz=UTC)
    except (TypeError, ValueError, OverflowError):
        return str(ts)
    return dt.strftime("%I:%M %p").lstrip("0")


def _format_message(
    message: dict[str, Any],
    state: dict[str, Any],
    reply_count: int | None = None,
) -> dict[str, Any]:
    user_id = message.get("user") or ""
    user = state.get("users", {}).get(user_id)
    # Trust a caller-derived count over the stored value: hand-authored seed
    # states can omit reply_count even when replies reference the parent.
    if reply_count is None:
        reply_count = message.get("reply_count") or 0
    return {
        "ts": message.get("ts", ""),
        "text": message.get("text", ""),
        "user_id": user_id,
        "user_name": _user_display_name(user, user_id),
        "thread_ts": message.get("thread_ts"),
        "reply_count": reply_count,
        "reactions": [
            {
                "name": reaction.get("name", ""),
                "count": reaction.get("count", 0),
            }
            for reaction in message.get("reactions", []) or []
        ],
        "has_thread": bool(reply_count),
        "time": _format_ts(str(message.get("ts", ""))),
        # File metadata only -- the stored content_base64 is never surfaced.
        "files": [
            {
                "name": file.get("name") or file.get("title", ""),
                "mimetype": file.get("mimetype", ""),
                "size": file.get("size", 0),
            }
            for file in message.get("files", []) or []
        ],
    }


async def api_channels(request: Request) -> JSONResponse:
    state = _state_json()
    messages = state.get("messages", {})
    channels = []
    for channel in state.get("channels", {}).values():
        channels.append(
            {
                "id": channel["id"],
                "name": channel["name"],
                "topic": _topic_value(channel.get("topic")),
                "purpose": _topic_value(channel.get("purpose")),
                "is_private": channel.get("is_private", False),
                "is_archived": channel.get("is_archived", False),
                "num_members": channel.get("num_members") or 0,
                "messageCount": len(messages.get(channel["id"], [])),
            }
        )
    channels.sort(key=lambda channel: channel.get("name", ""))
    return JSONResponse({"channels": channels})


async def api_channel_messages(request: Request) -> JSONResponse:
    channel_id = request.path_params["channel_id"]
    state = _state_json()
    channel = state.get("channels", {}).get(channel_id)
    if channel is None:
        return JSONResponse({"error": "Channel not found"}, status_code=404)
    try:
        limit = int(request.query_params.get("limit", "100"))
    except ValueError:
        limit = 100
    channel_messages = state.get("messages", {}).get(channel_id, [])
    # Derive thread sizes from the messages actually present so a parent whose
    # stored reply_count is missing or stale still surfaces a thread link --
    # otherwise its replies (filtered out of the main list below) are unreachable.
    reply_counts: dict[str, int] = {}
    for message in channel_messages:
        thread_ts = message.get("thread_ts")
        if thread_ts and thread_ts != message.get("ts"):
            reply_counts[thread_ts] = reply_counts.get(thread_ts, 0) + 1
    messages = [
        message
        for message in channel_messages
        if not message.get("thread_ts") or message.get("thread_ts") == message.get("ts")
    ]
    messages.sort(key=lambda message: float(message.get("ts", 0)), reverse=True)
    formatted = [
        _format_message(message, state, reply_count=reply_counts.get(message.get("ts", ""), 0)) for message in messages
    ]
    return JSONResponse(
        {
            "channel": {"id": channel["id"], "name": channel["name"]},
            "messages": formatted[:limit],
            "total": len(formatted),
        }
    )


async def api_thread(request: Request) -> JSONResponse:
    channel_id = request.path_params["channel_id"]
    thread_ts = request.path_params["thread_ts"]
    from slack_mock.state import get_thread_replies

    state = _state_json()
    replies = [
        _format_message(message.model_dump(mode="json", by_alias=True, exclude_none=True), state)
        for message in get_thread_replies(channel_id, thread_ts)
    ]
    if not replies:
        return JSONResponse({"error": "Thread not found"}, status_code=404)
    return JSONResponse({"messages": replies})


async def api_users(request: Request) -> JSONResponse:
    state = _state_json()
    users = []
    for user in state.get("users", {}).values():
        profile = user.get("profile", {})
        users.append(
            {
                "id": user["id"],
                "name": user["name"],
                "real_name": user.get("real_name") or profile.get("real_name") or user.get("name", ""),
                "display_name": profile.get("display_name") or profile.get("real_name") or user.get("name", ""),
                "title": profile.get("title", ""),
                "email": profile.get("email", ""),
                "is_bot": user.get("is_bot", False),
                "deleted": user.get("deleted", False),
                "status": profile.get("status_text", ""),
                "status_emoji": profile.get("status_emoji", ""),
            }
        )
    return JSONResponse({"users": users})


async def viewer_html(request: Request) -> HTMLResponse:
    return HTMLResponse(VIEWER_HTML)


VIEWER_HTML = (Path(__file__).parent / "viewer.html").read_text(encoding="utf-8")


def create_slack_viewer_app() -> Starlette:
    routes = [
        Route("/", viewer_html),
        Route("/api/channels", api_channels),
        Route("/api/channels/{channel_id}/messages", api_channel_messages),
        Route("/api/threads/{channel_id}/{thread_ts}", api_thread),
        Route("/api/users", api_users),
    ]
    return Starlette(routes=routes, middleware=[Middleware(ProxyTokenMiddleware)])


def run_http_server(mcp_app, port: int) -> None:
    fastmcp_asgi = mcp_app.http_app(transport="streamable-http", path="/mcp")
    viewer = create_slack_viewer_app()

    async def combined_app(scope, receive, send):
        if scope["type"] == "lifespan":
            await fastmcp_asgi(scope, receive, send)
            return
        path = scope.get("path", "")
        if path.startswith("/mcp"):
            await fastmcp_asgi(scope, receive, send)
        else:
            await viewer(scope, receive, send)

    uvicorn.run(combined_app, host="127.0.0.1", port=port, log_level="warning")
