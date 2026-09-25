"""Generic HTTP wrapper for FastMCP services.

Provides a simple way to run any FastMCP app in HTTP mode with:
- MCP StreamableHTTP transport at /mcp
- Proxy token authentication
- A simple placeholder viewer page at /

Services with richer data viewers can implement their own viewer
module instead of using this generic wrapper.
"""

from __future__ import annotations

import os

import uvicorn
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import HTMLResponse, Response
from starlette.routing import Route


class ProxyTokenMiddleware(BaseHTTPMiddleware):
    """Reject non-MCP requests lacking the correct X-Proxy-Token header."""

    async def dispatch(self, request: Request, call_next):
        if request.url.path.startswith("/mcp"):
            return await call_next(request)
        token = os.environ.get("MCP_PROXY_TOKEN", "")
        if token and request.headers.get("x-proxy-token") != token:
            return Response("Forbidden: invalid proxy token", status_code=403)
        return await call_next(request)


def run_fastmcp_http(mcp_app, port: int, viewer_html: str | None = None) -> None:
    """Run a FastMCP app as an HTTP server with optional viewer.

    Args:
        mcp_app: A FastMCP instance.
        port: TCP port to listen on (127.0.0.1 only).
        viewer_html: Optional HTML string for the viewer page at /.
            If None, a simple placeholder page is shown.
    """
    fastmcp_asgi = mcp_app.http_app(
        transport="streamable-http",
        path="/mcp",
    )

    html = viewer_html or _placeholder_html(mcp_app.name)

    async def index(request: Request) -> HTMLResponse:
        return HTMLResponse(html)

    viewer = Starlette(
        routes=[Route("/", index)],
        middleware=[Middleware(ProxyTokenMiddleware)],
    )

    async def combined_app(scope, receive, send):
        path = scope.get("path", "")
        if path.startswith("/mcp"):
            await fastmcp_asgi(scope, receive, send)
        else:
            await viewer(scope, receive, send)

    uvicorn.run(
        combined_app,
        host="127.0.0.1",
        port=port,
        log_level="warning",
    )


def _placeholder_html(name: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{name}</title>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
         display: flex; align-items: center; justify-content: center;
         height: 100vh; margin: 0; background: #f8fafc; color: #475569; }}
  .card {{ text-align: center; padding: 40px; }}
  h1 {{ font-size: 24px; color: #1e293b; margin-bottom: 8px; }}
  p {{ font-size: 14px; }}
</style>
</head>
<body>
  <div class="card">
    <h1>{name}</h1>
    <p>MCP service running — viewer not yet implemented.</p>
  </div>
</body>
</html>"""
