"""Syntara viewer — read-only filesystem browser for the agent's sandbox workdir.

Serves:
  GET /api/tree?path=<rel>   — directory listing
  GET /api/file?path=<rel>   — file contents (text; binary files flagged)
  GET /                      — viewer HTML (single-page app)

All paths are relative to ``sandbox.WORKDIR`` (what readFile/writeFile see).
Requests are rejected if the resolved path escapes that root.

All non-MCP routes require the X-Proxy-Token header.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import uvicorn
from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, Response, StreamingResponse
from starlette.routing import Route

from core._token import get_proxy_token
from core.tools import sandbox

MAX_FILE_BYTES = 1_000_000
BINARY_SNIFF_BYTES = 4096


class ProxyTokenMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.url.path.startswith("/mcp"):
            return await call_next(request)
        token = get_proxy_token()
        if token and request.headers.get("x-proxy-token") != token:
            return Response("Forbidden: invalid proxy token", status_code=403)
        return await call_next(request)


def _get_root() -> Path:
    root = Path(sandbox.WORKDIR).resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _resolve(rel_path: str) -> Path | None:
    root = _get_root()
    candidate = (root / rel_path.lstrip("/")).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    return candidate


def _looks_binary(data: bytes) -> bool:
    if b"\x00" in data:
        return True
    try:
        data.decode("utf-8")
        return False
    except UnicodeDecodeError:
        return True


# NOTE: the core server runs as root, so every filesystem access below goes
# through the sandbox.agent_* helpers, which perform the read/scandir as the
# unprivileged sandbox user (uid 1000). _resolve() confines paths to WORKDIR,
# but reading as uid 1000 is what actually prevents this (token-gated, but
# proxied) endpoint from being a root read of /app via a symlink/TOCTOU race.


async def api_tree(request: Request) -> JSONResponse:
    rel = request.query_params.get("path", "")
    target = _resolve(rel)
    if target is None:
        return JSONResponse({"error": "path outside sandbox"}, status_code=400)

    try:
        raw_entries = sandbox.agent_list_dir(str(target))
    except sandbox.AgentReadError as e:
        return JSONResponse({"error": str(e)}, status_code=404)

    entries = [
        {"name": e["name"], "type": "dir" if e["is_dir"] else "file", "size": e["size"]}
        for e in sorted(raw_entries, key=lambda e: (not e["is_dir"], e["name"].lower()))
    ]

    root = _get_root()
    return JSONResponse(
        {
            "path": str(target.relative_to(root)) if target != root else "",
            "entries": entries,
        }
    )


async def api_file(request: Request) -> JSONResponse:
    rel = request.query_params.get("path", "")
    target = _resolve(rel)
    if target is None:
        return JSONResponse({"error": "path outside sandbox"}, status_code=400)

    try:
        size, header, raw, _start = sandbox.agent_read_window(
            str(target), offset=0, limit=MAX_FILE_BYTES, sniff=BINARY_SNIFF_BYTES
        )
    except sandbox.AgentReadError as e:
        return JSONResponse({"error": str(e)}, status_code=404)

    if _looks_binary(header):
        return JSONResponse({"path": rel, "size": size, "binary": True, "content": None})

    return JSONResponse(
        {
            "path": rel,
            "size": size,
            "binary": False,
            "truncated": size > MAX_FILE_BYTES,
            "content": raw.decode("utf-8", errors="replace"),
        }
    )


async def api_download(request: Request) -> Response:
    rel = request.query_params.get("path", "")
    target = _resolve(rel)
    if target is None:
        return JSONResponse({"error": "path outside sandbox"}, status_code=400)

    # Stream the file from a uid-1000 reader straight to the client so a large
    # /workdir file is never buffered whole in the (root) server. Prime the
    # first chunk up front (off the event loop) so a missing/denied file
    # surfaces as a 404 rather than a streamed 200 that aborts mid-body.
    chunks = sandbox.agent_stream_file(str(target))
    try:
        first = await run_in_threadpool(lambda: next(chunks, None))
    except sandbox.AgentReadError as e:
        return JSONResponse({"error": str(e)}, status_code=404)

    def body() -> Iterator[bytes]:
        if first is not None:
            yield first
        yield from chunks

    return StreamingResponse(
        body(),
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{target.name}"'},
    )


async def viewer_html(request: Request) -> HTMLResponse:
    return HTMLResponse(VIEWER_HTML)


def create_core_viewer_app() -> Starlette:
    routes = [
        Route("/", viewer_html),
        Route("/api/tree", api_tree),
        Route("/api/file", api_file),
        Route("/api/download", api_download),
    ]
    return Starlette(
        routes=routes,
        middleware=[Middleware(ProxyTokenMiddleware)],
    )


def run_http_server(mcp_app, port: int) -> None:
    """Run combined MCP + viewer HTTP server."""
    fastmcp_asgi = mcp_app.http_app(
        transport="streamable-http",
        path="/mcp",
    )

    viewer = create_core_viewer_app()

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


VIEWER_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Syntara — Sandbox</title>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #0f172a; color: #e2e8f0; display: flex; height: 100vh; overflow: hidden; }

  .sidebar { width: 320px; min-width: 320px; background: #1e293b; border-right: 1px solid #334155; display: flex; flex-direction: column; overflow: hidden; }
  .sidebar-header { padding: 16px; border-bottom: 1px solid #334155; }
  .sidebar-header h1 { font-size: 14px; font-weight: 600; color: #f8fafc; letter-spacing: -0.2px; }
  .sidebar-header .subtitle { font-size: 11px; color: #64748b; margin-top: 2px; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; word-break: break-all; }
  .tree { flex: 1; padding: 8px 4px; overflow-y: auto; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12.5px; }
  .tree-node { user-select: none; }
  .tree-row { display: flex; align-items: center; gap: 4px; padding: 3px 6px; border-radius: 4px; cursor: pointer; color: #cbd5e1; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .tree-row:hover { background: #0f172a; }
  .tree-row.active { background: #1d4ed8; color: #fff; }
  .tree-row .chev { width: 12px; color: #64748b; flex-shrink: 0; font-size: 10px; }
  .tree-row .icon { width: 14px; flex-shrink: 0; opacity: 0.8; }
  .tree-row .name { overflow: hidden; text-overflow: ellipsis; }
  .tree-row.active .chev { color: #cbd5e1; }
  .tree-children { padding-left: 14px; display: none; }
  .tree-children.open { display: block; }

  .main { flex: 1; display: flex; flex-direction: column; overflow: hidden; }
  .file-header { padding: 12px 20px; border-bottom: 1px solid #334155; background: #1e293b; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12px; color: #94a3b8; display: flex; justify-content: space-between; align-items: center; }
  .file-header .path { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; flex: 1; min-width: 0; }
  .file-header .meta { color: #64748b; font-size: 11px; flex-shrink: 0; margin-left: 12px; }
  .file-header .actions { flex-shrink: 0; margin-left: 12px; }
  .download-btn { background: #334155; color: #e2e8f0; border: 1px solid #475569; border-radius: 4px; padding: 4px 10px; font-size: 11px; font-family: inherit; cursor: pointer; text-decoration: none; display: inline-block; }
  .download-btn:hover { background: #475569; color: #fff; }
  .download-btn[disabled], .download-btn.hidden { display: none; }
  .file-body { flex: 1; overflow: auto; padding: 16px 20px; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12.5px; line-height: 1.55; color: #e2e8f0; white-space: pre; tab-size: 4; }
  .placeholder { color: #475569; font-size: 13px; padding: 24px; font-family: -apple-system, BlinkMacSystemFont, sans-serif; white-space: normal; }
  .warn { color: #fb923c; }
</style>
</head>
<body>
  <div class="sidebar">
    <div class="sidebar-header">
      <h1>Sandbox</h1>
      <div class="subtitle" id="root-path">/workdir</div>
    </div>
    <div class="tree" id="tree"><div class="placeholder">Loading…</div></div>
  </div>
  <div class="main">
    <div class="file-header">
      <div class="path" id="file-path">No file selected</div>
      <div class="meta" id="file-meta"></div>
      <div class="actions">
        <a class="download-btn hidden" id="download-btn" href="#" download>⬇ Download</a>
      </div>
    </div>
    <div class="file-body" id="file-body"><div class="placeholder">Select a file from the tree on the left.</div></div>
  </div>

  <script>
    const base = window.location.pathname.replace(/\\/$/, '');
    let activeFile = null;

    async function fetchJSON(path) {
      const r = await fetch(base + path);
      if (!r.ok) throw new Error('HTTP ' + r.status);
      return r.json();
    }

    function esc(s) {
      if (s === null || s === undefined) return '';
      const d = document.createElement('div');
      d.textContent = s;
      return d.innerHTML;
    }

    function formatSize(n) {
      if (n === null || n === undefined) return '';
      if (n < 1024) return n + ' B';
      if (n < 1024 * 1024) return (n / 1024).toFixed(1) + ' KB';
      return (n / (1024 * 1024)).toFixed(1) + ' MB';
    }

    function joinPath(dir, name) {
      if (!dir) return name;
      return dir.replace(/\\/$/, '') + '/' + name;
    }

    async function renderRoot() {
      const treeEl = document.getElementById('tree');
      try {
        const data = await fetchJSON('/api/tree?path=');
        treeEl.innerHTML = '';
        const node = buildNode('', data.entries, 0);
        treeEl.appendChild(node);
      } catch (e) {
        treeEl.innerHTML = '<div class="placeholder">Failed to load: ' + esc(e.message) + '</div>';
      }
    }

    function buildNode(parentPath, entries, depth) {
      const frag = document.createDocumentFragment();
      if (!entries.length) {
        const empty = document.createElement('div');
        empty.className = 'placeholder';
        empty.style.padding = '8px 12px';
        empty.textContent = '(empty)';
        frag.appendChild(empty);
        return frag;
      }
      for (const entry of entries) {
        const fullPath = joinPath(parentPath, entry.name);
        const node = document.createElement('div');
        node.className = 'tree-node';
        const row = document.createElement('div');
        row.className = 'tree-row';
        row.dataset.path = fullPath;
        row.dataset.type = entry.type;
        const chev = document.createElement('span');
        chev.className = 'chev';
        chev.textContent = entry.type === 'dir' ? '▸' : '';
        const icon = document.createElement('span');
        icon.className = 'icon';
        icon.textContent = entry.type === 'dir' ? '📁' : '📄';
        const name = document.createElement('span');
        name.className = 'name';
        name.textContent = entry.name;
        row.appendChild(chev);
        row.appendChild(icon);
        row.appendChild(name);
        node.appendChild(row);

        if (entry.type === 'dir') {
          const children = document.createElement('div');
          children.className = 'tree-children';
          node.appendChild(children);
          row.addEventListener('click', async () => {
            const isOpen = children.classList.contains('open');
            if (isOpen) {
              children.classList.remove('open');
              chev.textContent = '▸';
            } else {
              if (!children.dataset.loaded) {
                children.innerHTML = '<div class="placeholder" style="padding:4px 12px;">…</div>';
                try {
                  const data = await fetchJSON('/api/tree?path=' + encodeURIComponent(fullPath));
                  children.innerHTML = '';
                  children.appendChild(buildNode(fullPath, data.entries, depth + 1));
                  children.dataset.loaded = '1';
                } catch (e) {
                  children.innerHTML = '<div class="placeholder" style="padding:4px 12px;">Error</div>';
                }
              }
              children.classList.add('open');
              chev.textContent = '▾';
            }
          });
        } else {
          row.addEventListener('click', () => openFile(fullPath, row));
        }

        frag.appendChild(node);
      }
      return frag;
    }

    async function openFile(path, rowEl) {
      document.querySelectorAll('.tree-row.active').forEach(el => el.classList.remove('active'));
      if (rowEl) rowEl.classList.add('active');
      activeFile = path;

      const pathEl = document.getElementById('file-path');
      const metaEl = document.getElementById('file-meta');
      const bodyEl = document.getElementById('file-body');
      const dlBtn = document.getElementById('download-btn');
      pathEl.textContent = path;
      metaEl.textContent = '';
      bodyEl.innerHTML = '<div class="placeholder">Loading…</div>';
      dlBtn.href = base + '/api/download?path=' + encodeURIComponent(path);
      dlBtn.classList.remove('hidden');

      try {
        const data = await fetchJSON('/api/file?path=' + encodeURIComponent(path));
        metaEl.textContent = formatSize(data.size) + (data.truncated ? ' (truncated — use Download for full file)' : '');
        if (data.binary) {
          bodyEl.innerHTML = '<div class="placeholder warn">Binary file — not rendered. Use Download to fetch the raw bytes.</div>';
        } else {
          bodyEl.textContent = data.content;
        }
      } catch (e) {
        bodyEl.innerHTML = '<div class="placeholder">Failed to load: ' + esc(e.message) + '</div>';
      }
    }

    renderRoot();
  </script>
</body>
</html>"""
