"""FastMCP tool surface for the Google Calendar mock."""

import functools
import inspect

from fastmcp import FastMCP
from pydantic import EmailStr

from .models import (
    AttendeeResponseStatus,
    AvailabilityDurationMinutes,
    BirthdayProperties,
    CalendarAccountId,
    CalendarAccountsState,
    CalendarId,
    CalendarPerson,
    CalendarState,
    CalendarSummary,
    CalendarTimeZone,
    EventAttendee,
    EventClearField,
    EventDateTime,
    EventId,
    EventReminders,
    EventSource,
    EventSummary,
    EventTransparency,
    EventType,
    ExtendedProperties,
    FocusTimeProperties,
    GoogleCalendarState,
    ListEventsMaxResults,
    OutOfOfficeProperties,
    RecurrenceRuleString,
    Rfc3339OffsetDateTimeString,
    SearchEventsMaxResults,
    SearchEventsOrderBy,
    SearchEventsResponse,
    WorkingLocationProperties,
)
from .state import list_accounts as list_calendar_accounts
from .state import set_active_account, state_from_json, state_to_json, write_snapshots
from .tools import availability, calendars, events, search

mcp = FastMCP("google_calendar")


def _snapshot_on_write(fn):
    """Write configured state snapshots after successful write tools.

    The wrapper is async so the registered tool is a coroutine: FastMCP then
    validates and runs it on the event loop instead of a worker threadpool,
    avoiding the pydantic-core concurrency panic. See ``async_tool_guard``.
    """

    @functools.wraps(fn)
    async def wrapper(*args, **kwargs):
        result = fn(*args, **kwargs)
        if inspect.isawaitable(result):
            result = await result
        write_snapshots()
        return result

    return wrapper


def _with_account(fn):
    """Decorator: extract account_id from tool calls and select that account."""

    @functools.wraps(fn)
    async def wrapper(*args, **kwargs):
        account_id = kwargs.pop("account_id", None)
        if account_id is not None:
            set_active_account(account_id)
        result = fn(*args, **kwargs)
        if inspect.isawaitable(result):
            result = await result
        return result

    return wrapper


@mcp.tool()
async def export_state() -> GoogleCalendarState:
    """Export the full google_calendar state as JSON.

    Round-trips with import_state.
    """
    state = state_to_json()
    if "accounts" in state:
        return CalendarAccountsState.model_validate(state)
    return CalendarState.model_validate(state)


@mcp.tool()
@_snapshot_on_write
def import_state(state: GoogleCalendarState) -> dict:
    """Replace the google_calendar state with the provided JSON.

    For synthetic-data injection and test setup. Round-trips with export_state.
    """
    state_from_json(state)
    return {"status": "success"}


@mcp.tool()
async def list_accounts() -> dict:
    """List available isolated Google Calendar accounts.

    Multi-account worlds store each account as a fully separate calendar state.
    Use the returned ``account_id`` with other tools to select the account to
    read or mutate. Single-account worlds expose their only configured account.
    """
    accounts = list_calendar_accounts()
    return {"status": "success", "accounts": accounts, "count": len(accounts)}


@mcp.tool()
@_with_account
@_snapshot_on_write
def create_event(
    summary: EventSummary,
    start: EventDateTime,
    end: EventDateTime,
    description: str | None = None,
    location: str | None = None,
    calendar_id: CalendarId = "primary",
    recurrence: list[RecurrenceRuleString] | None = None,
    reminders: EventReminders | None = None,
    attendees: list[EventAttendee] | None = None,
    creator: CalendarPerson | None = None,
    organizer: CalendarPerson | None = None,
    extendedProperties: ExtendedProperties | None = None,
    source: EventSource | None = None,
    transparency: EventTransparency | None = None,
    eventType: EventType = EventType.DEFAULT,
    outOfOfficeProperties: OutOfOfficeProperties | None = None,
    focusTimeProperties: FocusTimeProperties | None = None,
    workingLocationProperties: WorkingLocationProperties | None = None,
    birthdayProperties: BirthdayProperties | None = None,
    account_id: CalendarAccountId | None = None,
) -> dict:
    """
    Creates a new event in Google Calendar.

    Args:
        summary: Event title
        start: Start time object with either 'dateTime' (ISO format, e.g. '2025-12-10T09:00:00-05:00')
               for timed events, or 'date' (YYYY-MM-DD, e.g. '2025-12-10') for all-day events.
               Optionally include 'timeZone' (e.g. 'America/New_York').
        end: End time object with either 'dateTime' or 'date' (same format as start).
             For all-day events, 'date' is exclusive (e.g. end '2025-12-11' means the event ends on 2025-12-10).
        description: Event description
        location: Event location
        calendar_id: Calendar to create the event in (default 'primary')
        recurrence: RRULE strings for recurring events (e.g., ['RRULE:FREQ=WEEKLY;BYDAY=TU;COUNT=10'])
        reminders: Reminder overrides, e.g. {"useDefault": false, "overrides": [{"method": "popup", "minutes": 15}]}
        attendees: List of attendees, e.g. [{"email": "alice@co.com", "displayName": "Alice"}]. responseStatus defaults to 'needsAction'.
        transparency: Whether the event blocks availability. 'opaque' is busy; 'transparent' is free.
        account_id: Calendar account to use in multi-account worlds. Defaults to the active account.

    Returns:
        The created event object with its ID
    """
    return events.create_event(
        summary=summary,
        start=start,
        end=end,
        description=description,
        location=location,
        calendar_id=calendar_id,
        recurrence=recurrence,
        reminders=reminders,
        attendees=attendees,
        creator=creator,
        organizer=organizer,
        extendedProperties=extendedProperties,
        source=source,
        transparency=transparency,
        eventType=eventType,
        outOfOfficeProperties=outOfOfficeProperties,
        focusTimeProperties=focusTimeProperties,
        workingLocationProperties=workingLocationProperties,
        birthdayProperties=birthdayProperties,
    )


@mcp.tool()
@_with_account
def get_event(
    eventId: EventId,
    calendar_id: CalendarId = "primary",
    account_id: CalendarAccountId | None = None,
) -> dict:
    """
    Retrieves details of a specific event.

    Args:
        eventId: ID of the event to retrieve
        calendar_id: Calendar to look in (default 'primary')
        account_id: Calendar account to use in multi-account worlds. Defaults to the active account.

    Returns:
        The event object if found
    """
    return events.get_event(eventId=eventId, calendar_id=calendar_id)


@mcp.tool()
@_with_account
@_snapshot_on_write
def update_event(
    eventId: EventId,
    summary: EventSummary | None = None,
    start: EventDateTime | None = None,
    end: EventDateTime | None = None,
    description: str | None = None,
    location: str | None = None,
    calendar_id: CalendarId = "primary",
    recurrence: list[RecurrenceRuleString] | None = None,
    reminders: EventReminders | None = None,
    attendees: list[EventAttendee] | None = None,
    creator: CalendarPerson | None = None,
    organizer: CalendarPerson | None = None,
    extendedProperties: ExtendedProperties | None = None,
    source: EventSource | None = None,
    transparency: EventTransparency | None = None,
    eventType: EventType | None = None,
    outOfOfficeProperties: OutOfOfficeProperties | None = None,
    focusTimeProperties: FocusTimeProperties | None = None,
    workingLocationProperties: WorkingLocationProperties | None = None,
    birthdayProperties: BirthdayProperties | None = None,
    clear_fields: list[EventClearField] | None = None,
    account_id: CalendarAccountId | None = None,
) -> dict:
    """
    Updates an existing event.

    Args:
        eventId: ID of the event to update
        summary: New event title
        start: New start time object with either 'dateTime' (ISO format) for timed events,
               or 'date' (YYYY-MM-DD) for all-day events. Optionally include 'timeZone'.
        end: New end time object with either 'dateTime' or 'date' (same format as start).
        description: New event description
        location: New event location
        calendar_id: Calendar containing the event (default 'primary')
        recurrence: RRULE strings for recurring events (pass empty list to remove recurrence)
        reminders: Reminder overrides (pass {"useDefault": true} to reset to defaults)
        attendees: Replace attendee list (pass empty list to remove all attendees)
        transparency: Whether the event blocks availability. 'opaque' is busy; 'transparent' is free.
        eventType: Event type to set. Switching types clears properties that only belong to the old type.
        clear_fields: Optional event fields to remove, e.g. ['source', 'transparency'].
        account_id: Calendar account to use in multi-account worlds. Defaults to the active account.

    Returns:
        The updated event object
    """
    return events.update_event(
        eventId=eventId,
        summary=summary,
        start=start,
        end=end,
        description=description,
        location=location,
        calendar_id=calendar_id,
        recurrence=recurrence,
        reminders=reminders,
        attendees=attendees,
        creator=creator,
        organizer=organizer,
        extendedProperties=extendedProperties,
        source=source,
        transparency=transparency,
        eventType=eventType,
        outOfOfficeProperties=outOfOfficeProperties,
        focusTimeProperties=focusTimeProperties,
        workingLocationProperties=workingLocationProperties,
        birthdayProperties=birthdayProperties,
        clear_fields=clear_fields,
    )


@mcp.tool()
@_with_account
@_snapshot_on_write
def delete_event(
    eventId: EventId,
    calendar_id: CalendarId = "primary",
    account_id: CalendarAccountId | None = None,
) -> dict:
    """
    Deletes an event from the calendar.

    Args:
        eventId: ID of the event to delete
        calendar_id: Calendar containing the event (default 'primary')
        account_id: Calendar account to use in multi-account worlds. Defaults to the active account.

    Returns:
        Confirmation of deletion
    """
    return events.delete_event(eventId=eventId, calendar_id=calendar_id)


@mcp.tool()
@_with_account
@_snapshot_on_write
def respond_to_event(
    eventId: EventId,
    email: EmailStr,
    response: AttendeeResponseStatus,
    calendar_id: CalendarId = "primary",
    account_id: CalendarAccountId | None = None,
) -> dict:
    """
    Update an attendee's RSVP response on an event.

    Args:
        eventId: ID of the event
        email: Email address of the attendee responding
        response: Response status — 'accepted', 'declined', 'tentative', or 'needsAction'
        calendar_id: Calendar containing the event (default 'primary')
        account_id: Calendar account to use in multi-account worlds. Defaults to the active account.

    Returns:
        The updated event with attendee responses
    """
    return events.respond_to_event(eventId=eventId, email=email, response=response, calendar_id=calendar_id)


@mcp.tool()
@_with_account
@_snapshot_on_write
def create_calendar(
    summary: CalendarSummary,
    description: str | None = None,
    timeZone: CalendarTimeZone | None = None,
    account_id: CalendarAccountId | None = None,
) -> dict:
    """
    Creates a new calendar (e.g., 'Personal', 'Team Meetings', 'On-Call').

    Args:
        summary: Calendar name
        description: Calendar description
        timeZone: IANA time zone for the calendar. Defaults to the primary calendar time zone.
        account_id: Calendar account to use in multi-account worlds. Defaults to the active account.

    Returns:
        The created calendar object
    """
    return calendars.create_calendar(summary=summary, description=description, timeZone=timeZone)


@mcp.tool()
@_with_account
def list_calendars(account_id: CalendarAccountId | None = None) -> dict:
    """
    Lists all calendars. Always includes the 'primary' calendar plus any
    additional calendars that have been created.

    Returns:
        List of calendars with their IDs, names, and event counts
    """
    return calendars.list_calendars()


@mcp.tool()
@_with_account
def list_events(
    timeMin: Rfc3339OffsetDateTimeString,
    timeMax: Rfc3339OffsetDateTimeString,
    maxResults: ListEventsMaxResults | None = None,
    orderBy: SearchEventsOrderBy | None = None,
    calendar_id: CalendarId | None = None,
    account_id: CalendarAccountId | None = None,
) -> dict:
    """
    Lists events within a specified time range.

    Args:
        timeMin: Lower bound (exclusive) for an event's end time (ISO format).
                 Events that end after this time are included.
        timeMax: Upper bound (exclusive) for an event's start time (ISO format).
                 Events that start before this time are included.
        maxResults: Maximum number of events to return
        orderBy: Sort order ('startTime' or 'updated')
        calendar_id: Calendar to list events from. If omitted, lists events from all calendars.
        account_id: Calendar account to use in multi-account worlds. Defaults to the active account.

    Returns:
        List of events that overlap the given time range
    """
    return search.list_events(
        timeMin=timeMin,
        timeMax=timeMax,
        maxResults=maxResults,
        orderBy=orderBy,
        calendar_id=calendar_id,
    )


@mcp.tool()
@_with_account
def search_events(
    query: str = "",
    timeMin: Rfc3339OffsetDateTimeString | None = None,
    timeMax: Rfc3339OffsetDateTimeString | None = None,
    calendar_id: CalendarId | None = None,
    attendee_email: EmailStr | None = None,
    response_status: AttendeeResponseStatus | None = None,
    organizer_email: EmailStr | None = None,
    creator_email: EmailStr | None = None,
    maxResults: SearchEventsMaxResults | None = None,
    orderBy: SearchEventsOrderBy | None = SearchEventsOrderBy.START_TIME,
    account_id: CalendarAccountId | None = None,
) -> SearchEventsResponse:
    """
    Search calendar events by keyword, phrase, attendee, location, or calendar.

    Args:
        query: Search query. Bare words are ANDed across summary, description,
               location, attendees, and calendar names. Quoted phrases require
               exact adjacency. May be empty when attendee_email is provided.
        timeMin: Optional lower bound (exclusive) for an event's end time.
        timeMax: Optional upper bound (exclusive) for an event's start time.
        calendar_id: Calendar to search. If omitted, searches all calendars.
        attendee_email: Optional attendee email or display-name substring.
        response_status: Optional attendee RSVP filter ('accepted', 'declined',
                         'tentative', or 'needsAction'). When attendee_email is
                         provided, applies to that attendee; otherwise matches
                         any attendee with that status.
        organizer_email: Optional organizer email substring.
        creator_email: Optional creator email substring.
        maxResults: Maximum number of matching events to return. 0 returns no results.
        orderBy: Sort order ('startTime', 'updated', or None).
        account_id: Calendar account to use in multi-account worlds. Defaults to the active account.

    Returns:
        Matching events with calendar_id annotated on each result.
    """
    return search.search_events(
        query=query,
        timeMin=timeMin,
        timeMax=timeMax,
        calendar_id=calendar_id,
        attendee_email=attendee_email,
        response_status=response_status,
        organizer_email=organizer_email,
        creator_email=creator_email,
        maxResults=maxResults,
        orderBy=orderBy,
    )


@mcp.tool()
@_with_account
def check_availability(
    timeMin: Rfc3339OffsetDateTimeString,
    timeMax: Rfc3339OffsetDateTimeString,
    duration_minutes: AvailabilityDurationMinutes = 30,
    calendar_id: CalendarId | None = None,
    attendee_emails: list[EmailStr] | None = None,
    account_id: CalendarAccountId | None = None,
) -> dict:
    """
    Check availability within a time range. Returns busy periods and free slots
    of at least the requested duration.

    When attendee_emails is provided, only events where at least one of those
    people is an attendee (and hasn't declined) are considered busy. This lets
    you find times that work for specific people.

    Args:
        timeMin: Start of the range to check (ISO format)
        timeMax: End of the range to check (ISO format)
        duration_minutes: Minimum free slot duration in minutes (default 30)
        calendar_id: Calendar to check. If omitted, checks all calendars.
        attendee_emails: Only consider events involving these attendees (optional)
        account_id: Calendar account to use in multi-account worlds. Defaults to the active account.

    Returns:
        Busy periods and available free slots within the range
    """
    return availability.check_availability(
        timeMin=timeMin,
        timeMax=timeMax,
        duration_minutes=duration_minutes,
        calendar_id=calendar_id,
        attendee_emails=attendee_emails,
    )
