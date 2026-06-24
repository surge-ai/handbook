# Google Calendar Capabilities

A mock calendar service supporting multiple calendars, recurring events, attendees with RSVP tracking, and availability checking.

## What the agent can do

**Manage events.** Create, read, update, and delete calendar events. Events can be timed (with specific start/end times) or all-day. Events support descriptions, locations, attendees, reminders, and recurrence rules.

**Recurring events.** Create events that repeat on a schedule — daily, weekly (with specific days like Tuesday/Thursday), or monthly. Recurrence supports end conditions (after N occurrences, or until a specific date). When listing events, recurring events are automatically expanded into individual instances within the query range.

**Multiple calendars.** Create named calendars (e.g., "Personal", "Work", "Team Meetings") and manage events across them. List all calendars with event counts. When listing events without specifying a calendar, results come from all calendars. Each calendar is isolated — events in one don't appear in another unless explicitly queried.

**Event search.** Search events across one or all calendars by keyword, quoted phrase, location, description, attendee email/name, creator, organizer, or calendar name. Search can be combined with time ranges, attendee filters, RSVP response-status filters, creator filters, and organizer filters. Recurring events are expanded within the requested range; without a range, search expands recurring events within a one-year default window from the series start.

**Attendees and RSVPs.** Invite people to events by email. Track RSVP responses (accepted, declined, tentative, needs action). Update individual attendee responses.

**Availability checking.** Check whether a time range is free or busy. Returns busy periods and available free slots of at least a specified duration. Can filter by specific attendees — for example, "find a free hour that works for both Alice and Bob." Declined events are excluded from busy calculations. Works across recurring events too.

**Reminders.** Set reminders on events (e.g., popup 15 minutes before, email 1 hour before). Reminders are stored but not actually triggered in the mock.

## Coverage gaps

- No "edit this and all future events" for recurring series (can only edit the template)
- No event colors or categories
- No shared calendar permissions (all calendars are fully accessible)
- No time zone conversion tools
- No meeting room or resource booking
- Search uses `query` rather than the real Google Calendar `q` parameter, and does not implement `eventTypes`
- Reminders are stored but don't fire

## Toolsets

10 tools total. Toolsets map to `WORLDBENCH_TOOL_SETS` values (prefixed form — e.g., `google_calendar_events`).

| Toolset | Tools | Description |
|---------|-------|-------------|
| `all` / `google_calendar_all` | 10 | Everything |
| `read` / `google_calendar_read` | 5 | Read-only: get, list/search events, list calendars, check availability |
| `write` / `google_calendar_write` | 5 | Write: create/update/delete event, create calendar, respond to event |
| `google_calendar_events` | 6 | Event CRUD plus list/search events |
| `google_calendar_calendars` | 2 | Calendar management: create, list calendars |
| `google_calendar_scheduling` | 2 | Availability + RSVP: check availability, respond to event |
| `google_calendar_core` | 6 | Baseline event management (legacy Toolathlon subset plus search) |
| `google_calendar_toolathlon_legacy` | 5 | Legacy Toolathlon tool subset (pre-integration) |
| `google_calendar_state` | 2 | `export_state`, `import_state` for fixture seeding and grading |
