"""Calendar viewer — read-only calendar UI and API endpoints.

Serves:
  GET /api/events        — event list (optional ?date=YYYY-MM-DD for week filter)
  GET /api/events/:id    — single event detail
  GET /api/stats         — calendar stats
  GET /                  — viewer HTML (single-page app)

All non-MCP routes require the X-Proxy-Token header.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
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
# Data access
# ---------------------------------------------------------------------------


def _get_events() -> dict[str, Any]:
    """Load and return all calendar events keyed uniquely for viewer lookups."""
    from .state import get_default_account_id, load_data

    data = load_data(account_id=get_default_account_id())
    events: dict[str, Any] = {}
    for event_id, event in data.get("events", {}).items():
        events[event_id] = {**event, "calendar_id": "primary", "lookup_id": event_id}

    for calendar_id, calendar in data.get("calendars", {}).items():
        for event_id, event in calendar.get("events", {}).items():
            lookup_id = f"{calendar_id}:{event_id}"
            events[lookup_id] = {**event, "calendar_id": calendar_id, "lookup_id": lookup_id}

    return events


def _parse_event_dt(dt_obj: dict) -> datetime | None:
    """Parse a dateTime or date field from an event start/end object. Always
    returns a timezone-aware datetime; all-day dates are treated as UTC."""
    if not dt_obj:
        return None

    dt = dt_obj.get("dateTime")
    if isinstance(dt, str):
        try:
            parsed = datetime.fromisoformat(dt)
        except ValueError:
            parsed = None
        if parsed is not None:
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)

    d = dt_obj.get("date")
    if isinstance(d, str):
        try:
            return datetime.strptime(d, "%Y-%m-%d").replace(tzinfo=UTC)
        except ValueError:
            return None
    return None


def _event_sort_key(event: dict) -> str:
    start = event.get("start", {})
    dt = start.get("dateTime")
    if isinstance(dt, str):
        return dt
    d = start.get("date")
    if isinstance(d, str):
        return d
    return ""


def _format_event(event: dict) -> dict[str, Any]:
    """Normalize an event for the API response."""
    start = event.get("start", {})
    end = event.get("end", {})
    return {
        "id": event.get("id", ""),
        "lookup_id": event.get("lookup_id", event.get("id", "")),
        "summary": event.get("summary", "(No title)"),
        "start": start,
        "end": end,
        "description": event.get("description"),
        "location": event.get("location"),
        "created": event.get("created"),
        "updated": event.get("updated"),
        "calendar_id": event.get("calendar_id", "primary"),
        "all_day": "date" in start and "dateTime" not in start,
    }


# ---------------------------------------------------------------------------
# API handlers
# ---------------------------------------------------------------------------


async def api_events(request: Request) -> JSONResponse:
    events_dict = _get_events()
    events = list(events_dict.values())

    date_param = request.query_params.get("date")
    if date_param:
        # Filter to the week containing the given date
        try:
            anchor = datetime.strptime(date_param, "%Y-%m-%d")
        except ValueError:
            return JSONResponse({"error": "Invalid date format, use YYYY-MM-DD"}, status_code=400)
        week_start = anchor - timedelta(days=anchor.weekday())  # Monday
        week_end = week_start + timedelta(days=7)

        filtered = []
        for ev in events:
            start_dt = _parse_event_dt(ev.get("start", {}))
            if start_dt is None:
                continue
            # Strip timezone for naive comparison
            start_naive = start_dt.replace(tzinfo=None)
            if week_start <= start_naive < week_end:
                filtered.append(ev)
        events = filtered

    events.sort(key=_event_sort_key)
    return JSONResponse({"events": [_format_event(e) for e in events]})


async def api_event_detail(request: Request) -> JSONResponse:
    event_id = request.path_params["event_id"]
    events_dict = _get_events()
    event = events_dict.get(event_id)
    if event is None:
        return JSONResponse({"error": "Event not found"}, status_code=404)
    return JSONResponse({"event": _format_event(event)})


async def api_stats(request: Request) -> JSONResponse:
    events_dict = _get_events()
    total = len(events_dict)
    now = datetime.now(UTC)
    upcoming = sum(
        1
        for ev in events_dict.values()
        if (_parse_event_dt(ev.get("start", {})) or datetime.min.replace(tzinfo=UTC)) >= now
    )
    return JSONResponse({"total_events": total, "upcoming_events": upcoming})


async def viewer_html(request: Request) -> HTMLResponse:
    return HTMLResponse(VIEWER_HTML)


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------


def create_calendar_viewer_app():
    routes = [
        Route("/", viewer_html),
        Route("/api/events", api_events),
        Route("/api/events/{event_id}", api_event_detail),
        Route("/api/stats", api_stats),
    ]
    return Starlette(
        routes=routes,
        middleware=[Middleware(ProxyTokenMiddleware)],
    )


def run_http_server(mcp_app, port: int) -> None:
    """Run combined MCP + viewer HTTP server."""
    # Get the ASGI app from FastMCP
    fastmcp_asgi = mcp_app.http_app(
        transport="streamable-http",
        path="/mcp",
    )

    viewer = create_calendar_viewer_app()

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
<title>Calendar</title>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #f8fafc; color: #1e293b; height: 100vh; display: flex; flex-direction: column; overflow: hidden; }

  /* Header / toolbar */
  .toolbar { display: flex; align-items: center; gap: 16px; padding: 10px 20px; background: #fff; border-bottom: 1px solid #e2e8f0; flex-shrink: 0; }
  .toolbar h1 { font-size: 20px; font-weight: 600; color: #1e293b; margin-right: auto; }
  .nav-btn { background: none; border: 1px solid #d1d5db; border-radius: 6px; padding: 6px 12px; font-size: 13px; cursor: pointer; color: #374151; }
  .nav-btn:hover { background: #f1f5f9; }
  .today-btn { background: #2563eb; color: #fff; border-color: #2563eb; }
  .today-btn:hover { background: #1d4ed8; }
  .week-label { font-size: 14px; font-weight: 600; color: #374151; min-width: 200px; text-align: center; }
  .view-toggle { display: flex; border: 1px solid #d1d5db; border-radius: 6px; overflow: hidden; }
  .view-btn { background: none; border: none; padding: 6px 14px; font-size: 13px; cursor: pointer; color: #374151; }
  .view-btn.active { background: #2563eb; color: #fff; }

  /* Week grid */
  .week-view { flex: 1; display: flex; flex-direction: column; overflow: hidden; }
  .week-header { display: grid; grid-template-columns: 56px repeat(7, 1fr); border-bottom: 1px solid #e2e8f0; background: #fff; flex-shrink: 0; padding-right: var(--calendar-scrollbar-width, 0px); }
  .week-header .time-col { }
  .day-header { padding: 8px 4px; text-align: center; border-left: 1px solid #e2e8f0; }
  .day-header .day-name { font-size: 11px; color: #64748b; text-transform: uppercase; font-weight: 600; }
  .day-header .day-num { font-size: 22px; font-weight: 400; color: #374151; line-height: 1.2; margin-top: 2px; }
  .day-header.today .day-num { background: #2563eb; color: #fff; border-radius: 50%; width: 36px; height: 36px; display: flex; align-items: center; justify-content: center; margin: 2px auto 0; }

  .week-body { flex: 1; overflow-y: auto; position: relative; }
  .week-scroll { display: grid; grid-template-columns: 56px repeat(7, 1fr); min-height: 1440px; /* 60px per hour * 24h */ }
  .time-gutter { position: relative; }
  .time-label { position: absolute; right: 8px; font-size: 10px; color: #94a3b8; transform: translateY(-50%); }
  .day-col { border-left: 1px solid #e2e8f0; position: relative; }
  .hour-line { position: absolute; left: 0; right: 0; border-top: 1px solid #f1f5f9; }
  .hour-line.half { border-top-style: dotted; }

  /* Events */
  .cal-event { position: absolute; left: 2px; right: 2px; border-radius: 4px; padding: 2px 6px; font-size: 11px; font-weight: 500; cursor: pointer; overflow: hidden; z-index: 1; border-left: 3px solid transparent; }
  .cal-event:hover { opacity: 0.85; }
  .cal-event .evt-time { font-size: 10px; opacity: 0.8; }
  .cal-event .evt-title { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .all-day-row { display: grid; grid-template-columns: 56px repeat(7, 1fr); background: #fff; border-bottom: 1px solid #e2e8f0; min-height: 28px; padding-right: var(--calendar-scrollbar-width, 0px); }
  .all-day-col { border-left: 1px solid #e2e8f0; padding: 2px; }
  .all-day-event { border-radius: 3px; padding: 1px 6px; font-size: 11px; font-weight: 500; cursor: pointer; margin-bottom: 1px; overflow: hidden; white-space: nowrap; text-overflow: ellipsis; }

  /* Current time line */
  .now-line { position: absolute; left: 0; right: 0; z-index: 2; pointer-events: none; }
  .now-line::before { content: ''; display: block; height: 2px; background: #ef4444; }
  .now-dot { position: absolute; left: -5px; top: -4px; width: 10px; height: 10px; border-radius: 50%; background: #ef4444; }

  /* List view */
  .list-view { flex: 1; overflow-y: auto; padding: 16px 24px; display: none; }
  .list-day-group { margin-bottom: 24px; }
  .list-day-label { font-size: 13px; font-weight: 600; color: #64748b; text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 8px; padding-bottom: 4px; border-bottom: 1px solid #e2e8f0; }
  .list-event { display: flex; gap: 16px; padding: 10px 12px; background: #fff; border-radius: 8px; margin-bottom: 6px; cursor: pointer; border: 1px solid #e2e8f0; }
  .list-event:hover { border-color: #93c5fd; background: #eff6ff; }
  .list-event .evt-color-dot { width: 10px; height: 10px; border-radius: 50%; flex-shrink: 0; margin-top: 3px; }
  .list-event .evt-info { flex: 1; min-width: 0; }
  .list-event .evt-title { font-size: 14px; font-weight: 500; color: #1e293b; }
  .list-event .evt-meta { font-size: 12px; color: #64748b; margin-top: 2px; }
  .list-empty { text-align: center; padding: 60px; color: #94a3b8; font-size: 14px; }

  /* Modal */
  .modal-overlay { position: fixed; inset: 0; background: rgba(0,0,0,0.3); z-index: 100; display: none; align-items: center; justify-content: center; }
  .modal-overlay.open { display: flex; }
  .modal { background: #fff; border-radius: 12px; width: 420px; max-width: 95vw; box-shadow: 0 20px 60px rgba(0,0,0,0.15); overflow: hidden; }
  .modal-header { padding: 20px 20px 16px; display: flex; align-items: flex-start; gap: 12px; }
  .modal-color { width: 14px; height: 14px; border-radius: 3px; flex-shrink: 0; margin-top: 3px; }
  .modal-title { font-size: 18px; font-weight: 600; flex: 1; }
  .modal-close { background: none; border: none; font-size: 18px; cursor: pointer; color: #94a3b8; padding: 0 4px; }
  .modal-body { padding: 0 20px 20px; }
  .modal-row { display: flex; gap: 10px; font-size: 13px; margin-bottom: 10px; align-items: flex-start; }
  .modal-icon { font-size: 15px; flex-shrink: 0; width: 20px; }
  .modal-text { color: #374151; flex: 1; }
  .modal-desc { color: #475569; line-height: 1.6; white-space: pre-wrap; margin-top: 4px; }

  .no-events-msg { display: flex; align-items: center; justify-content: center; height: 200px; color: #94a3b8; font-size: 14px; }
</style>
</head>
<body>
  <div class="toolbar">
    <h1>Calendar</h1>
    <button class="nav-btn" onclick="prevWeek()">&#8249;</button>
    <div class="week-label" id="week-label"></div>
    <button class="nav-btn" onclick="nextWeek()">&#8250;</button>
    <button class="nav-btn today-btn" onclick="goToday()">Today</button>
    <div class="view-toggle">
      <button class="view-btn active" id="btn-week" onclick="setView('week')">Week</button>
      <button class="view-btn" id="btn-list" onclick="setView('list')">List</button>
    </div>
  </div>

  <div class="week-view" id="week-view">
    <div class="all-day-row" id="all-day-row">
      <div style="font-size:10px;color:#94a3b8;padding:8px 4px;text-align:right;align-self:center">all-day</div>
    </div>
    <div class="week-header" id="week-header"></div>
    <div class="week-body">
      <div class="week-scroll" id="week-scroll"></div>
    </div>
  </div>

  <div class="list-view" id="list-view">
    <div id="list-content"></div>
  </div>

  <div class="modal-overlay" id="modal-overlay" onclick="closeModal(event)">
    <div class="modal" id="modal"></div>
  </div>

  <script>
    const HOURS = 24;
    const PX_PER_HOUR = 60;
    const DAYS = ['Sun','Mon','Tue','Wed','Thu','Fri','Sat'];
    const MONTHS = ['January','February','March','April','May','June','July','August','September','October','November','December'];
    const COLORS = [
      { bg: '#dbeafe', fg: '#1d4ed8', border: '#3b82f6' },
      { bg: '#dcfce7', fg: '#166534', border: '#22c55e' },
      { bg: '#fef3c7', fg: '#92400e', border: '#f59e0b' },
      { bg: '#fce7f3', fg: '#9d174d', border: '#ec4899' },
      { bg: '#ede9fe', fg: '#5b21b6', border: '#8b5cf6' },
      { bg: '#ffedd5', fg: '#9a3412', border: '#f97316' },
      { bg: '#cffafe', fg: '#164e63', border: '#06b6d4' },
      { bg: '#f0fdf4', fg: '#14532d', border: '#16a34a' },
    ];

    let currentWeekStart = getWeekStart(new Date());
    let allEvents = [];
    let colorMap = {};
    let colorIdx = 0;
    let currentView = 'week';

    function syncScrollbarGutter() {
      const body = document.querySelector('.week-body');
      if (!body) return;
      const width = Math.max(body.offsetWidth - body.clientWidth, 0);
      document.documentElement.style.setProperty('--calendar-scrollbar-width', width + 'px');
    }

    function getWeekStart(date) {
      const d = new Date(date);
      const day = d.getDay(); // 0=Sun
      // Use Monday as week start
      const diff = (day === 0) ? -6 : 1 - day;
      d.setDate(d.getDate() + diff);
      d.setHours(0, 0, 0, 0);
      return d;
    }

    function getColor(eventId) {
      if (!colorMap[eventId]) {
        colorMap[eventId] = COLORS[colorIdx % COLORS.length];
        colorIdx++;
      }
      return colorMap[eventId];
    }

    function esc(s) {
      if (!s) return '';
      const d = document.createElement('div');
      d.textContent = s;
      return d.innerHTML;
    }

    function parseEventDt(dtObj) {
      if (!dtObj) return null;
      if (dtObj.dateTime) {
        try { return new Date(dtObj.dateTime); } catch { return null; }
      }
      if (dtObj.date) {
        // Parse as local date to avoid timezone shift
        const [y, m, d] = dtObj.date.split('-').map(Number);
        return new Date(y, m - 1, d);
      }
      return null;
    }

    function formatTime(date) {
      if (!date) return '';
      return date.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit', hour12: true });
    }

    function formatDateLong(date) {
      return DAYS[date.getDay()] + ', ' + MONTHS[date.getMonth()] + ' ' + date.getDate() + ', ' + date.getFullYear();
    }

    function isSameDay(a, b) {
      return a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
    }

    const base = window.location.pathname.replace(/\\/$/, '');

    async function fetchEvents() {
      const dateStr = currentWeekStart.toISOString().slice(0, 10);
      const r = await fetch(base + '/api/events?date=' + dateStr);
      const data = await r.json();
      allEvents = data.events || [];
    }

    function getWeekDays() {
      const days = [];
      for (let i = 0; i < 7; i++) {
        const d = new Date(currentWeekStart);
        d.setDate(d.getDate() + i);
        days.push(d);
      }
      return days;
    }

    function updateWeekLabel() {
      const days = getWeekDays();
      const start = days[0];
      const end = days[6];
      let label;
      if (start.getMonth() === end.getMonth()) {
        label = MONTHS[start.getMonth()] + ' ' + start.getDate() + ' – ' + end.getDate() + ', ' + start.getFullYear();
      } else {
        label = MONTHS[start.getMonth()] + ' ' + start.getDate() + ' – ' + MONTHS[end.getMonth()] + ' ' + end.getDate() + ', ' + end.getFullYear();
      }
      document.getElementById('week-label').textContent = label;
    }

    function renderWeekHeader() {
      const days = getWeekDays();
      const today = new Date();
      const header = document.getElementById('week-header');
      let html = '<div class="time-col"></div>';
      days.forEach(d => {
        const isToday = isSameDay(d, today);
        html += '<div class="day-header' + (isToday ? ' today' : '') + '">' +
          '<div class="day-name">' + DAYS[d.getDay()] + '</div>' +
          '<div class="day-num">' + d.getDate() + '</div>' +
          '</div>';
      });
      header.innerHTML = html;
    }

    function renderWeekGrid() {
      const days = getWeekDays();
      const scroll = document.getElementById('week-scroll');

      // Build time gutter
      let gutterHtml = '<div class="time-gutter">';
      for (let h = 0; h < HOURS; h++) {
        const top = h * PX_PER_HOUR;
        const label = h === 0 ? '' : (h < 12 ? h + ' AM' : h === 12 ? '12 PM' : (h - 12) + ' PM');
        gutterHtml += '<div class="time-label" style="top:' + top + 'px">' + label + '</div>';
        // hour line marker
        gutterHtml += '<div class="hour-line" style="top:' + top + 'px;left:0;width:56px"></div>';
      }
      gutterHtml += '</div>';

      // Build day columns
      let daysHtml = '';
      days.forEach((day, idx) => {
        daysHtml += '<div class="day-col" id="day-col-' + idx + '">';
        // Hour lines
        for (let h = 0; h < HOURS; h++) {
          daysHtml += '<div class="hour-line" style="top:' + (h * PX_PER_HOUR) + 'px"></div>';
          daysHtml += '<div class="hour-line half" style="top:' + (h * PX_PER_HOUR + 30) + 'px"></div>';
        }
        // Events for this day
        const dayEvents = allEvents.filter(ev => {
          if (ev.all_day) return false;
          const start = parseEventDt(ev.start);
          return start && isSameDay(start, day);
        });
        dayEvents.forEach(ev => {
          const start = parseEventDt(ev.start);
          const end = parseEventDt(ev.end);
          if (!start) return;
          const startMins = start.getHours() * 60 + start.getMinutes();
          const endMins = end ? (end.getHours() * 60 + end.getMinutes()) : startMins + 30;
          const top = (startMins / 60) * PX_PER_HOUR;
          const height = Math.max(((endMins - startMins) / 60) * PX_PER_HOUR, 20);
          const lookupId = ev.lookup_id || ev.id;
          const color = getColor(lookupId);
          daysHtml += '<div class="cal-event" style="top:' + top + 'px;height:' + height + 'px;background:' + color.bg + ';color:' + color.fg + ';border-left-color:' + color.border + '" onclick="showEvent(\\'' + esc(lookupId) + '\\')">' +
            '<div class="evt-time">' + formatTime(start) + '</div>' +
            '<div class="evt-title">' + esc(ev.summary) + '</div>' +
            '</div>';
        });
        daysHtml += '</div>';
      });

      scroll.innerHTML = gutterHtml + daysHtml;

      // All-day events
      renderAllDay(days);

      // Current time indicator
      renderNowLine(days);
    }

    function renderAllDay(days) {
      const allDayRow = document.getElementById('all-day-row');
      // reset to first placeholder
      allDayRow.innerHTML = '<div style="font-size:10px;color:#94a3b8;padding:8px 4px;text-align:right;align-self:center">all-day</div>';
      days.forEach((day, idx) => {
        const col = document.createElement('div');
        col.className = 'all-day-col';
        const dayEvents = allEvents.filter(ev => {
          if (!ev.all_day) return false;
          const start = parseEventDt(ev.start);
          return start && isSameDay(start, day);
        });
        dayEvents.forEach(ev => {
          const lookupId = ev.lookup_id || ev.id;
          const color = getColor(lookupId);
          const el = document.createElement('div');
          el.className = 'all-day-event';
          el.style.cssText = 'background:' + color.bg + ';color:' + color.fg;
          el.textContent = ev.summary;
          el.onclick = () => showEvent(lookupId);
          col.appendChild(el);
        });
        allDayRow.appendChild(col);
      });
    }

    function renderNowLine(days) {
      const now = new Date();
      days.forEach((day, idx) => {
        if (!isSameDay(day, now)) return;
        const mins = now.getHours() * 60 + now.getMinutes();
        const top = (mins / 60) * PX_PER_HOUR;
        const col = document.getElementById('day-col-' + idx);
        if (!col) return;
        const line = document.createElement('div');
        line.className = 'now-line';
        line.style.top = top + 'px';
        line.innerHTML = '<span class="now-dot"></span>';
        col.appendChild(line);
      });
    }

    function renderListView() {
      const content = document.getElementById('list-content');
      if (!allEvents.length) {
        content.innerHTML = '<div class="list-empty">No events this week</div>';
        return;
      }

      // Group by day
      const groups = {};
      allEvents.forEach(ev => {
        const start = parseEventDt(ev.start);
        if (!start) return;
        const key = start.toDateString();
        if (!groups[key]) groups[key] = { date: start, events: [] };
        groups[key].events.push(ev);
      });

      const sortedKeys = Object.keys(groups).sort((a, b) => new Date(a) - new Date(b));
      let html = '';
      sortedKeys.forEach(key => {
        const g = groups[key];
        html += '<div class="list-day-group">';
        html += '<div class="list-day-label">' + formatDateLong(g.date) + '</div>';
        g.events.forEach(ev => {
          const start = parseEventDt(ev.start);
          const end = parseEventDt(ev.end);
          const lookupId = ev.lookup_id || ev.id;
          const color = getColor(lookupId);
          const timeStr = ev.all_day ? 'All day' : formatTime(start) + (end ? ' – ' + formatTime(end) : '');
          html += '<div class="list-event" onclick="showEvent(\\'' + esc(lookupId) + '\\')">' +
            '<div class="evt-color-dot" style="background:' + color.border + '"></div>' +
            '<div class="evt-info">' +
              '<div class="evt-title">' + esc(ev.summary) + '</div>' +
              '<div class="evt-meta">' + esc(timeStr) + (ev.location ? ' · ' + esc(ev.location) : '') + '</div>' +
            '</div>' +
            '</div>';
        });
        html += '</div>';
      });
      content.innerHTML = html || '<div class="list-empty">No events this week</div>';
    }

    async function showEvent(id) {
      const r = await fetch(base + '/api/events/' + encodeURIComponent(id));
      const data = await r.json();
      const ev = data.event;
      if (!ev) return;

      const start = parseEventDt(ev.start);
      const end = parseEventDt(ev.end);
      const color = getColor(ev.lookup_id || ev.id);

      let timeStr;
      if (ev.all_day) {
        timeStr = formatDateLong(start);
      } else {
        timeStr = formatDateLong(start) + '<br>' + formatTime(start) + (end ? ' – ' + formatTime(end) : '');
      }

      let html = '<div class="modal-header">' +
        '<div class="modal-color" style="background:' + color.border + '"></div>' +
        '<div class="modal-title">' + esc(ev.summary) + '</div>' +
        '<button class="modal-close" onclick="closeModal()">&#x2715;</button>' +
        '</div>';
      html += '<div class="modal-body">';
      html += '<div class="modal-row"><span class="modal-icon">&#128197;</span><span class="modal-text">' + timeStr + '</span></div>';
      if (ev.location) {
        html += '<div class="modal-row"><span class="modal-icon">&#128205;</span><span class="modal-text">' + esc(ev.location) + '</span></div>';
      }
      if (ev.description) {
        html += '<div class="modal-row"><span class="modal-icon">&#128221;</span><span class="modal-text modal-desc">' + esc(ev.description) + '</span></div>';
      }
      html += '</div>';

      document.getElementById('modal').innerHTML = html;
      document.getElementById('modal-overlay').classList.add('open');
    }

    function closeModal(e) {
      if (e && e.target !== document.getElementById('modal-overlay')) return;
      document.getElementById('modal-overlay').classList.remove('open');
    }

    function setView(view) {
      currentView = view;
      document.getElementById('btn-week').classList.toggle('active', view === 'week');
      document.getElementById('btn-list').classList.toggle('active', view === 'list');
      document.getElementById('week-view').style.display = view === 'week' ? 'flex' : 'none';
      document.getElementById('list-view').style.display = view === 'list' ? 'block' : 'none';
      if (view === 'list') renderListView();
    }

    async function prevWeek() {
      currentWeekStart.setDate(currentWeekStart.getDate() - 7);
      await refresh();
    }

    async function nextWeek() {
      currentWeekStart.setDate(currentWeekStart.getDate() + 7);
      await refresh();
    }

    async function goToday() {
      currentWeekStart = getWeekStart(new Date());
      await refresh();
    }

    async function refresh() {
      syncScrollbarGutter();
      updateWeekLabel();
      await fetchEvents();
      renderWeekHeader();
      renderWeekGrid();
      if (currentView === 'list') renderListView();
      // Scroll to 8am on week view
      scrollToHour(8);
    }

    function scrollToHour(hour) {
      const body = document.querySelector('.week-body');
      if (body) body.scrollTop = hour * PX_PER_HOUR - 20;
    }

    // Keyboard nav
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') document.getElementById('modal-overlay').classList.remove('open');
      if (e.key === 'ArrowLeft' && !e.target.closest('.modal')) prevWeek();
      if (e.key === 'ArrowRight' && !e.target.closest('.modal')) nextWeek();
    });

    // Init
    window.addEventListener('resize', syncScrollbarGutter);
    refresh();
  </script>
</body>
</html>"""
