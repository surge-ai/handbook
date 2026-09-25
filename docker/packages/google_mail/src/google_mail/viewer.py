"""Mail viewer — read-only webmail UI and API endpoints.

Serves:
  GET /api/folders        — folder list with counts
  GET /api/emails         — email list (optional ?folder=X&search=X&page=N)
  GET /api/emails/:id     — single email detail
  GET /api/contacts       — contact list
  GET /api/stats          — mailbox stats
  GET /                   — viewer HTML (single-page app)

All non-MCP routes require the X-Proxy-Token header.
"""

from __future__ import annotations

import os
from typing import Any

import uvicorn
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, Response
from starlette.routing import Route

# ---------------------------------------------------------------------------
# Auth middleware
# ---------------------------------------------------------------------------


class ProxyTokenMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # Skip auth for MCP endpoint
        if request.url.path.startswith("/mcp"):
            return await call_next(request)
        token = os.environ.get("MCP_PROXY_TOKEN", "")
        if token and request.headers.get("x-proxy-token") != token:
            return Response("Forbidden: invalid proxy token", status_code=403)
        return await call_next(request)


# ---------------------------------------------------------------------------
# Lazy access to the mailbox service
# ---------------------------------------------------------------------------

_mailbox_ref = None


def set_mailbox(mailbox) -> None:
    """Set the mailbox instance for the viewer to use."""
    global _mailbox_ref
    _mailbox_ref = mailbox


def _get_mailbox(request: Request | None = None):
    """Return the mailbox service, initializing state if needed.

    If the MCP server has not initialized yet, initialize the mailbox eagerly
    from the same state loader used by the server.
    """
    if _mailbox_ref is not None:
        return _mailbox_ref

    from google_mail.state import get_mailboxes, init_state

    init_state()
    mailbox_id = request.query_params.get("mailbox_id") if request is not None else None
    mailboxes = get_mailboxes()
    if mailbox_id:
        if mailbox_id not in mailboxes:
            available = ", ".join(sorted(mailboxes.keys()))
            raise KeyError(f"Mailbox '{mailbox_id}' not found. Available: {available}")
        return mailboxes[mailbox_id]
    if "default" in mailboxes:
        return mailboxes["default"]
    first_id = sorted(mailboxes.keys())[0]
    return mailboxes[first_id]


# ---------------------------------------------------------------------------
# API handlers
# ---------------------------------------------------------------------------


async def api_folders(request: Request) -> JSONResponse:
    try:
        mailbox = _get_mailbox(request)
    except KeyError as exc:
        return JSONResponse({"error": str(exc)}, status_code=404)
    folders = mailbox.get_folders()
    return JSONResponse({"folders": folders})


async def api_emails(request: Request) -> JSONResponse:
    try:
        mailbox = _get_mailbox(request)
    except KeyError as exc:
        return JSONResponse({"error": str(exc)}, status_code=404)
    folder = request.query_params.get("folder")
    search = request.query_params.get("search")
    page = int(request.query_params.get("page", "1"))
    page_size = int(request.query_params.get("page_size", "50"))

    if search:
        emails, total = mailbox.search_emails(query=search, folder=folder, page=page, page_size=page_size)
    else:
        emails, total = mailbox.get_emails(folder=folder, page=page, page_size=page_size)

    return JSONResponse(
        {
            "emails": [_format_summary(e) for e in emails],
            "total": total,
            "page": page,
            "page_size": page_size,
        }
    )


async def api_email_detail(request: Request) -> JSONResponse:
    try:
        mailbox = _get_mailbox(request)
    except KeyError as exc:
        return JSONResponse({"error": str(exc)}, status_code=404)
    email_id = request.path_params["email_id"]
    try:
        email = mailbox.read_email(email_id)
    except Exception:
        return JSONResponse({"error": "Email not found"}, status_code=404)

    return JSONResponse({"email": _format_full(email)})


async def api_contacts(request: Request) -> JSONResponse:
    try:
        mailbox = _get_mailbox(request)
    except KeyError as exc:
        return JSONResponse({"error": str(exc)}, status_code=404)
    contacts = mailbox.get_contacts()
    groups = mailbox.get_groups()
    return JSONResponse(
        {
            "contacts": [{"email": c.email, "name": c.name} for c in contacts],
            "groups": [{"email": g.email, "name": g.name, "members": g.members} for g in groups],
        }
    )


async def api_stats(request: Request) -> JSONResponse:
    try:
        mailbox = _get_mailbox(request)
    except KeyError as exc:
        return JSONResponse({"error": str(exc)}, status_code=404)
    stats = mailbox.get_mailbox_stats()
    return JSONResponse(stats)


async def viewer_html(request: Request) -> HTMLResponse:
    return HTMLResponse(VIEWER_HTML)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _format_summary(email) -> dict[str, Any]:
    return {
        "email_id": email.email_id,
        "folder": email.folder,
        "subject": email.subject,
        "from_addr": email.from_addr,
        "to_addr": email.to_addr,
        "date": email.date.isoformat(),
        "is_read": email.is_read,
        "is_important": email.is_important,
        "has_attachments": len(email.attachments) > 0,
    }


def _format_full(email) -> dict[str, Any]:
    d = _format_summary(email)
    d["cc_addr"] = email.cc_addr
    d["bcc_addr"] = email.bcc_addr
    d["body_text"] = email.body_text
    d["body_html"] = email.body_html
    d["attachments"] = [
        {"filename": a.filename, "content_type": a.content_type, "size": a.size} for a in email.attachments
    ]
    return d


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------


def create_mail_viewer_app():
    routes = [
        Route("/", viewer_html),
        Route("/api/folders", api_folders),
        Route("/api/emails", api_emails),
        Route("/api/emails/{email_id}", api_email_detail),
        Route("/api/contacts", api_contacts),
        Route("/api/stats", api_stats),
    ]
    return Starlette(
        routes=routes,
        middleware=[Middleware(ProxyTokenMiddleware)],
    )


def run_http_server(mcp_app, port: int) -> None:
    """Run combined MCP + viewer HTTP server."""

    # Get the ASGI app from FastMCP
    # mcp.server.fastmcp.FastMCP uses streamable_http_app(); fastmcp.FastMCP uses http_app()
    if hasattr(mcp_app, "streamable_http_app"):
        fastmcp_asgi = mcp_app.streamable_http_app()
    else:
        fastmcp_asgi = mcp_app.http_app(
            transport="streamable-http",
            path="/mcp",
        )

    viewer = create_mail_viewer_app()

    # Combined app: route /mcp to FastMCP, everything else to viewer
    async def combined_app(scope, receive, send):
        if scope["type"] == "lifespan":
            await fastmcp_asgi(scope, receive, send)
            return
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


# ---------------------------------------------------------------------------
# Viewer HTML
# ---------------------------------------------------------------------------

VIEWER_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Mail</title>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #f8fafc; color: #1e293b; display: flex; height: 100vh; }

  /* Folder sidebar */
  .folders { width: 200px; min-width: 200px; background: #fff; border-right: 1px solid #e2e8f0; display: flex; flex-direction: column; }
  .folders-header { padding: 16px; border-bottom: 1px solid #e2e8f0; }
  .folders-header h2 { font-size: 15px; font-weight: 600; }
  .folder-list { flex: 1; padding: 8px; overflow-y: auto; }
  .folder-item { display: flex; align-items: center; justify-content: space-between; padding: 8px 12px; border-radius: 6px; cursor: pointer; font-size: 13px; color: #475569; margin-bottom: 2px; }
  .folder-item:hover { background: #f1f5f9; }
  .folder-item.active { background: #eff6ff; color: #2563eb; font-weight: 600; }
  .folder-item .count { font-size: 11px; color: #94a3b8; }
  .folder-item.active .count { color: #60a5fa; }

  /* Email list */
  .email-list-pane { width: 360px; min-width: 360px; border-right: 1px solid #e2e8f0; display: flex; flex-direction: column; background: #fff; }
  .list-header { padding: 10px 16px; border-bottom: 1px solid #e2e8f0; }
  .list-header input { width: 100%; padding: 8px 12px; border: 1px solid #d1d5db; border-radius: 8px; font-size: 13px; }
  .list-header input:focus { outline: none; border-color: #3b82f6; box-shadow: 0 0 0 2px rgba(59,130,246,0.15); }
  .email-list { flex: 1; overflow-y: auto; }
  .email-item { padding: 12px 16px; border-bottom: 1px solid #f1f5f9; cursor: pointer; }
  .email-item:hover { background: #f8fafc; }
  .email-item.active { background: #eff6ff; }
  .email-item.unread { border-left: 3px solid #3b82f6; }
  .email-item .from { font-size: 13px; font-weight: 600; color: #1e293b; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .email-item.unread .from { color: #0f172a; }
  .email-item .subject { font-size: 13px; color: #475569; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; margin-top: 2px; }
  .email-item .preview { font-size: 12px; color: #94a3b8; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; margin-top: 2px; }
  .email-item .meta { display: flex; justify-content: space-between; align-items: center; margin-top: 4px; }
  .email-item .date { font-size: 11px; color: #94a3b8; }
  .email-item .badges { display: flex; gap: 4px; }
  .badge-important { color: #f59e0b; font-size: 12px; }
  .badge-attachment { color: #64748b; font-size: 12px; }

  /* Detail pane */
  .detail-pane { flex: 1; display: flex; flex-direction: column; background: #fff; overflow-y: auto; }
  .detail-header { padding: 20px 24px; border-bottom: 1px solid #e2e8f0; }
  .detail-header h2 { font-size: 18px; font-weight: 600; line-height: 1.4; }
  .detail-meta { margin-top: 12px; }
  .detail-meta .row { display: flex; gap: 8px; font-size: 13px; margin-bottom: 4px; }
  .detail-meta .label { color: #64748b; min-width: 40px; }
  .detail-meta .value { color: #1e293b; }
  .detail-body { padding: 24px; flex: 1; }
  .detail-body .body-text { font-size: 14px; line-height: 1.7; white-space: pre-wrap; color: #334155; }
  .detail-attachments { padding: 0 24px 24px; }
  .detail-attachments h4 { font-size: 12px; font-weight: 600; color: #64748b; text-transform: uppercase; margin-bottom: 8px; }
  .attachment-item { display: inline-flex; align-items: center; gap: 6px; padding: 6px 12px; background: #f1f5f9; border-radius: 6px; font-size: 12px; margin-right: 8px; margin-bottom: 4px; }

  .empty { text-align: center; padding: 60px; color: #94a3b8; font-size: 14px; }
  .detail-empty { display: flex; align-items: center; justify-content: center; flex: 1; color: #94a3b8; font-size: 14px; }
</style>
</head>
<body>
  <div class="folders">
    <div class="folders-header"><h2>Mail</h2></div>
    <div id="folder-list" class="folder-list"></div>
  </div>
  <div class="email-list-pane">
    <div class="list-header">
      <input type="text" id="search-input" placeholder="Search emails..." oninput="onSearch()">
    </div>
    <div id="email-list" class="email-list">
      <div class="empty">Loading...</div>
    </div>
  </div>
  <div id="detail-pane" class="detail-pane">
    <div class="detail-empty">Select an email to read</div>
  </div>

  <script>
    let currentFolder = null;
    let emails = [];
    let searchTimeout = null;
    const base = window.location.pathname.replace(/\\/$/, '');

    async function fetchJSON(path) {
      const r = await fetch(base + path);
      return r.json();
    }

    function esc(s) { if (!s) return ''; const d = document.createElement('div'); d.textContent = s; return d.innerHTML; }

    async function init() {
      const data = await fetchJSON('/api/folders');
      const el = document.getElementById('folder-list');
      el.innerHTML = data.folders.map(f =>
        '<div class="folder-item" data-folder="' + esc(f.name) + '" onclick="selectFolder(\\'' + esc(f.name) + '\\')">' +
        '<span>' + folderIcon(f.name) + ' ' + esc(f.name) + '</span>' +
        '<span class="count">' + (f.unread || '') + '</span>' +
        '</div>'
      ).join('');
      if (data.folders.length) selectFolder('INBOX');
    }

    function folderIcon(name) {
      const icons = { INBOX: '📥', Sent: '📤', Drafts: '📝', Trash: '🗑️' };
      return icons[name] || '📁';
    }

    async function selectFolder(folder) {
      currentFolder = folder;
      document.querySelectorAll('.folder-item').forEach(el => {
        el.classList.toggle('active', el.dataset.folder === folder);
      });
      await loadEmails();
    }

    async function loadEmails() {
      const search = document.getElementById('search-input').value;
      let q = '/api/emails?page_size=100';
      if (currentFolder) q += '&folder=' + encodeURIComponent(currentFolder);
      if (search) q += '&search=' + encodeURIComponent(search);
      const data = await fetchJSON(q);
      emails = data.emails;
      renderList();
    }

    function renderList() {
      const el = document.getElementById('email-list');
      if (!emails.length) {
        el.innerHTML = '<div class="empty">No emails</div>';
        return;
      }
      el.innerHTML = emails.map(e => {
        const unread = !e.is_read ? ' unread' : '';
        return '<div class="email-item' + unread + '" onclick="showEmail(\\'' + e.email_id + '\\')">' +
          '<div class="from">' + esc(e.from_addr) + '</div>' +
          '<div class="subject">' + esc(e.subject || '(no subject)') + '</div>' +
          '<div class="meta">' +
            '<span class="date">' + formatDate(e.date) + '</span>' +
            '<span class="badges">' +
              (e.is_important ? '<span class="badge-important">★</span>' : '') +
              (e.has_attachments ? '<span class="badge-attachment">📎</span>' : '') +
            '</span>' +
          '</div>' +
        '</div>';
      }).join('');
    }

    async function showEmail(id) {
      const data = await fetchJSON('/api/emails/' + id);
      const e = data.email;
      document.querySelectorAll('.email-item').forEach(el => el.classList.remove('active'));
      // Mark active - find by looking at onclick
      document.querySelectorAll('.email-item').forEach(el => {
        if (el.getAttribute('onclick')?.includes(id)) el.classList.add('active');
      });

      let html = '<div class="detail-header"><h2>' + esc(e.subject || '(no subject)') + '</h2>';
      html += '<div class="detail-meta">';
      html += '<div class="row"><span class="label">From</span><span class="value">' + esc(e.from_addr) + '</span></div>';
      html += '<div class="row"><span class="label">To</span><span class="value">' + esc(e.to_addr) + '</span></div>';
      if (e.cc_addr) html += '<div class="row"><span class="label">CC</span><span class="value">' + esc(e.cc_addr) + '</span></div>';
      html += '<div class="row"><span class="label">Date</span><span class="value">' + formatDate(e.date) + '</span></div>';
      html += '</div></div>';

      html += '<div class="detail-body"><div class="body-text">' + esc(e.body_text) + '</div></div>';

      if (e.attachments && e.attachments.length) {
        html += '<div class="detail-attachments"><h4>Attachments</h4>';
        e.attachments.forEach(a => {
          html += '<span class="attachment-item">📎 ' + esc(a.filename) + ' (' + formatSize(a.size) + ')</span>';
        });
        html += '</div>';
      }

      document.getElementById('detail-pane').innerHTML = html;
    }

    function onSearch() {
      clearTimeout(searchTimeout);
      searchTimeout = setTimeout(loadEmails, 300);
    }

    function formatDate(d) {
      if (!d) return '';
      try {
        const dt = new Date(d);
        const now = new Date();
        if (dt.toDateString() === now.toDateString()) {
          return dt.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' });
        }
        return dt.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
      } catch { return d; }
    }

    function formatSize(bytes) {
      if (!bytes) return '0 B';
      if (bytes < 1024) return bytes + ' B';
      if (bytes < 1048576) return (bytes / 1024).toFixed(1) + ' KB';
      return (bytes / 1048576).toFixed(1) + ' MB';
    }

    init();
  </script>
</body>
</html>"""
