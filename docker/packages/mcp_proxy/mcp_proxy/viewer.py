"""Viewer reverse-proxy server.

Serves a tabbed interface that embeds each MCP service's viewer UI in an
iframe.  All requests to ``/viewer/<service>/...`` are reverse-proxied to
the service's internal HTTP server with the ``X-Proxy-Token`` header injected.
"""

from __future__ import annotations

import httpx
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response
from starlette.routing import Route


def create_viewer_app(
    service_registry: dict[str, int],
    proxy_token: str,
) -> Starlette:
    """Build a Starlette ASGI app that serves the viewer UI and reverse-proxies
    to service HTTP servers."""

    http_client = httpx.AsyncClient(timeout=180.0)

    _COOKIE = "__viewer_service"

    # Server-side state: last selected service.  Used as a fallback when
    # the browser blocks third-party cookies (e.g. when embedded in an
    # iframe).  Safe because this server has a single user.
    _state: dict[str, str] = {}

    async def index(request: Request) -> HTMLResponse:
        return HTMLResponse(_render_shell(service_registry))

    async def set_service(request: Request) -> Response:
        """Set the active service cookie and redirect to /."""
        app = request.query_params.get("app", "")
        if app not in service_registry:
            return Response("Unknown service", status_code=404)
        _state["active_service"] = app
        resp = RedirectResponse("/", status_code=307)
        resp.set_cookie(_COOKIE, app, httponly=True, samesite="lax")
        return resp

    async def proxy(request: Request) -> Response:
        """Proxy requests to the active service (read from cookie).

        Falls back to the shell page if no service cookie is set.
        """
        service_name = request.cookies.get(_COOKIE)

        # When the cookie is blocked (iframe context), fall back to
        # server-side state if the request originated from our own host
        # (i.e. the inner iframe redirect, not a direct navigation).
        if (not service_name or service_name not in service_registry) and _state.get(
            "active_service"
        ) in service_registry:
            service_name = _state["active_service"]

        if not service_name or service_name not in service_registry:
            if request.method == "GET":
                return HTMLResponse(_render_shell(service_registry))
            return Response("No active service", status_code=404)

        # Direct navigation to / (no referer) with a stale cookie → show shell
        if request.url.path == "/" and not request.headers.get("referer"):
            return HTMLResponse(_render_shell(service_registry))

        port = service_registry[service_name]
        path = request.url.path
        query = str(request.url.query)
        url = f"http://127.0.0.1:{port}{path}"
        if query:
            url = f"{url}?{query}"

        headers = dict(request.headers)
        headers["x-proxy-token"] = proxy_token
        headers.pop("host", None)

        body = await request.body()

        try:
            resp = await http_client.request(
                method=request.method,
                url=url,
                headers=headers,
                content=body or None,
            )
        except httpx.ConnectError:
            return Response("Service unavailable", status_code=502)

        excluded = {"transfer-encoding", "connection", "keep-alive"}
        resp_headers = {k: v for k, v in resp.headers.items() if k.lower() not in excluded}

        return Response(
            content=resp.content,
            status_code=resp.status_code,
            headers=resp_headers,
        )

    routes = [
        Route(
            "/__viewer__/set",
            set_service,
            methods=["GET"],
        ),
        Route(
            "/__viewer__",
            index,
        ),
        # Everything else proxies to the active service (or shows shell if no cookie)
        Route(
            "/{path:path}",
            proxy,
            methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"],
        ),
        Route(
            "/",
            proxy,
            methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"],
        ),
    ]

    return Starlette(routes=routes)


# ---------------------------------------------------------------------------
# Shell HTML template
# ---------------------------------------------------------------------------

_DISPLAY_NAMES: dict[str, str] = {
    "jira": "Jira",
    "slack": "Slack",
    "google_mail": "Mail",
    "emails_toolathlon_mock": "Mail",
    "google_calendar": "Calendar",
    "shopify": "Shopify",
    "core": "Core",
    # Legacy compat shim (forwards to core); REMOVE after 2026-06-18.
    "syntara": "Syntara",
    "web": "Web",
}

_ICONS: dict[str, str] = {
    "jira": "M12 2L2 12l10 10 10-10L12 2zm0 3.27L19.73 12 12 19.73 4.27 12 12 5.27z",
    "slack": "M6 15a2 2 0 01-2 2 2 2 0 01-2-2 2 2 0 012-2h2v2zm1 0a2 2 0 012-2 2 2 0 012 2v5a2 2 0 01-2 2 2 2 0 01-2-2v-5zm2-8a2 2 0 01-2-2 2 2 0 012-2 2 2 0 012 2v2H9zm0 1a2 2 0 012 2 2 2 0 01-2 2H4a2 2 0 01-2-2 2 2 0 012-2h5zm8 2a2 2 0 012-2 2 2 0 012 2 2 2 0 01-2 2h-2v-2zm-1 0a2 2 0 01-2 2 2 2 0 01-2-2V5a2 2 0 012-2 2 2 0 012 2v5zm-2 8a2 2 0 012 2 2 2 0 01-2 2 2 2 0 01-2-2v-2h2zm0-1a2 2 0 01-2-2 2 2 0 012-2h5a2 2 0 012 2 2 2 0 01-2 2h-5z",
    "google_mail": "M20 4H4c-1.1 0-2 .9-2 2v12c0 1.1.9 2 2 2h16c1.1 0 2-.9 2-2V6c0-1.1-.9-2-2-2zm0 4l-8 5-8-5V6l8 5 8-5v2z",
    "emails_toolathlon_mock": "M20 4H4c-1.1 0-2 .9-2 2v12c0 1.1.9 2 2 2h16c1.1 0 2-.9 2-2V6c0-1.1-.9-2-2-2zm0 4l-8 5-8-5V6l8 5 8-5v2z",
    "google_calendar": "M19 3h-1V1h-2v2H8V1H6v2H5c-1.1 0-2 .9-2 2v14c0 1.1.9 2 2 2h14c1.1 0 2-.9 2-2V5c0-1.1-.9-2-2-2zm0 16H5V8h14v11z",
    "shopify": "M15.34 2.61a.49.49 0 00-.45-.36.49.49 0 00-.44.2s-.84 1.06-1.06 1.33a3.58 3.58 0 00-1.63-.84l-.26-1.52a.49.49 0 00-.35-.4.49.49 0 00-.5.13L9.27 2.53a.73.73 0 00-.16.5l.05.53C7.4 4.34 6.3 6.05 6.3 8.12a5.7 5.7 0 005.7 5.7 5.7 5.7 0 005.7-5.7c0-2.24-1.3-4.18-3.36-5.51z",
    "core": "M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm-2 15l-5-5 1.41-1.41L10 14.17l7.59-7.59L19 8l-9 9z",
    # Legacy compat shim (forwards to core); REMOVE after 2026-06-18.
    "syntara": "M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm-2 15l-5-5 1.41-1.41L10 14.17l7.59-7.59L19 8l-9 9z",
    "web": "M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm-1 17.93c-3.94-.49-7-3.85-7-7.93 0-.62.08-1.21.21-1.79L9 15v1c0 1.1.9 2 2 2v1.93zm6.9-2.54c-.26-.81-1-1.39-1.9-1.39h-1v-3c0-.55-.45-1-1-1H8v-2h2c.55 0 1-.45 1-1V7h2c1.1 0 2-.9 2-2v-.41c2.93 1.19 5 4.06 5 7.41 0 2.08-.8 3.97-2.1 5.39z",
}


def _render_shell(registry: dict[str, int]) -> str:
    """Render the main tabbed shell HTML."""
    service_names = sorted(registry.keys())
    if not service_names:
        return "<html><body><h1>No services available</h1></body></html>"

    nav_items = []
    for name in service_names:
        display = _DISPLAY_NAMES.get(name, name.replace("_", " ").title())
        icon_path = _ICONS.get(name, "M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2z")
        nav_items.append(
            f'<button class="nav-item" data-service="{name}" onclick="selectService(\'{name}\')">'
            f'<svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor"><path d="{icon_path}"/></svg>'
            f"<span>{display}</span>"
            f"</button>"
        )

    nav_html = "\n".join(nav_items)
    first_service = service_names[0]

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Services</title>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; display: flex; height: 100vh; background: #0f172a; color: #e2e8f0; }}

  /* Sidebar */
  .sidebar {{
    width: 220px;
    min-width: 220px;
    background: #1e293b;
    border-right: 1px solid #334155;
    display: flex;
    flex-direction: column;
    padding: 0;
  }}
  .sidebar-header {{
    padding: 20px 16px 16px;
    border-bottom: 1px solid #334155;
  }}
  .sidebar-header h1 {{
    font-size: 15px;
    font-weight: 600;
    color: #f1f5f9;
    letter-spacing: -0.01em;
  }}
  .sidebar-header p {{
    font-size: 11px;
    color: #64748b;
    margin-top: 4px;
  }}
  .nav {{
    flex: 1;
    padding: 8px;
    overflow-y: auto;
  }}
  .nav-item {{
    display: flex;
    align-items: center;
    gap: 10px;
    width: 100%;
    padding: 10px 12px;
    border: none;
    border-radius: 8px;
    background: transparent;
    color: #94a3b8;
    font-size: 13px;
    font-weight: 500;
    cursor: pointer;
    transition: all 0.15s ease;
    text-align: left;
    margin-bottom: 2px;
  }}
  .nav-item:hover {{
    background: #334155;
    color: #e2e8f0;
  }}
  .nav-item.active {{
    background: #3b82f6;
    color: #fff;
  }}
  .nav-item svg {{
    flex-shrink: 0;
    opacity: 0.8;
  }}
  .nav-item.active svg {{
    opacity: 1;
  }}

  /* Main content */
  .main {{
    flex: 1;
    display: flex;
    flex-direction: column;
    min-width: 0;
  }}
  .main iframe {{
    flex: 1;
    border: none;
    width: 100%;
    height: 100%;
    background: #fff;
  }}
</style>
</head>
<body>
  <div class="sidebar">
    <div class="sidebar-header">
      <h1>Services</h1>
      <p>{len(service_names)} services</p>
    </div>
    <div class="nav">
      {nav_html}
    </div>
  </div>
  <div class="main">
    <iframe id="viewer-frame" src="/__viewer__/set?app={first_service}" sandbox="allow-scripts allow-same-origin allow-forms"></iframe>
  </div>
  <script>
    function selectService(name) {{
      document.getElementById('viewer-frame').src = '/__viewer__/set?app=' + name;
      document.querySelectorAll('.nav-item').forEach(el => {{
        el.classList.toggle('active', el.dataset.service === name);
      }});
    }}
    // Activate first service on load
    document.querySelector('.nav-item[data-service="{first_service}"]')?.classList.add('active');
  </script>
</body>
</html>"""
