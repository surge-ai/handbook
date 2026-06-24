"""Event tool implementations for the Google Calendar mock."""

from datetime import UTC, datetime

from pydantic import EmailStr, ValidationError

from google_calendar.models import (
    EVENT_TYPE_PROPERTY_FIELDS,
    AttendeeResponseStatus,
    BirthdayProperties,
    CalendarId,
    CalendarPerson,
    Event,
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
    OutOfOfficeProperties,
    RecurrenceRuleString,
    WorkingLocationProperties,
    dump_model,
    event_attendee_payload,
)
from google_calendar.state import (
    delete_calendar_event,
    generate_event_id,
    get_calendar_events,
    load_data,
    save_data_or_error,
    set_calendar_event,
)


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

    Returns:
        The created event object with its ID
    """
    data = load_data()

    event_id = generate_event_id()
    now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    event: dict = {
        "id": event_id,
        "summary": summary,
        "start": dump_model(start, EventDateTime),
        "end": dump_model(end, EventDateTime),
        "created": now,
        "updated": now,
        "eventType": EventType(eventType).value,
    }

    if description:
        event["description"] = description
    if location:
        event["location"] = location
    if recurrence:
        event["recurrence"] = recurrence
    if reminders:
        event["reminders"] = dump_model(reminders, EventReminders)
    if attendees:
        event["attendees"] = [event_attendee_payload(attendee) for attendee in attendees]
    if creator is not None:
        event["creator"] = dump_model(creator, CalendarPerson)
    if organizer is not None:
        event["organizer"] = dump_model(organizer, CalendarPerson)
    if extendedProperties is not None:
        event["extendedProperties"] = dump_model(extendedProperties, ExtendedProperties)
    if source is not None:
        event["source"] = dump_model(source, EventSource)
    if transparency is not None:
        event["transparency"] = EventTransparency(transparency).value
    if outOfOfficeProperties is not None:
        event["outOfOfficeProperties"] = dump_model(outOfOfficeProperties, OutOfOfficeProperties)
    if focusTimeProperties is not None:
        event["focusTimeProperties"] = dump_model(focusTimeProperties, FocusTimeProperties)
    if workingLocationProperties is not None:
        event["workingLocationProperties"] = dump_model(workingLocationProperties, WorkingLocationProperties)
    if birthdayProperties is not None:
        event["birthdayProperties"] = dump_model(birthdayProperties, BirthdayProperties)

    try:
        event = Event.model_validate(event).model_dump(exclude_none=True)
    except ValidationError as e:
        return {"status": "error", "message": str(e)}

    if not set_calendar_event(data, calendar_id, event_id, event):
        return {"status": "error", "message": f"Calendar '{calendar_id}' not found"}

    if error := save_data_or_error(data):
        return error

    return {"status": "success", "event": event}


def get_event(eventId: EventId, calendar_id: CalendarId = "primary") -> dict:
    """
    Retrieves details of a specific event.

    Args:
        eventId: ID of the event to retrieve
        calendar_id: Calendar to look in (default 'primary')

    Returns:
        The event object if found
    """
    data = load_data()
    events = get_calendar_events(data, calendar_id)

    if eventId not in events:
        return {"status": "error", "message": f"Event with ID '{eventId}' not found in calendar '{calendar_id}'"}

    return {"status": "success", "event": events[eventId]}


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

    Returns:
        The updated event object
    """
    data = load_data()
    events = get_calendar_events(data, calendar_id)

    if eventId not in events:
        return {"status": "error", "message": f"Event with ID '{eventId}' not found in calendar '{calendar_id}'"}

    event = dict(events[eventId])

    if clear_fields:
        fields_to_clear = {field.value for field in clear_fields}
        for field in fields_to_clear:
            event.pop(field, None)

    if summary is not None:
        event["summary"] = summary
    if start is not None:
        event["start"] = dump_model(start, EventDateTime)
    if end is not None:
        event["end"] = dump_model(end, EventDateTime)
    if description is not None:
        event["description"] = description
    if location is not None:
        event["location"] = location
    if recurrence is not None:
        if recurrence:
            event["recurrence"] = recurrence
        else:
            event.pop("recurrence", None)
    if reminders is not None:
        event["reminders"] = dump_model(reminders, EventReminders)
    if attendees is not None:
        event["attendees"] = [event_attendee_payload(attendee) for attendee in attendees]
    if creator is not None:
        event["creator"] = dump_model(creator, CalendarPerson)
    if organizer is not None:
        event["organizer"] = dump_model(organizer, CalendarPerson)
    if extendedProperties is not None:
        event["extendedProperties"] = dump_model(extendedProperties, ExtendedProperties)
    if source is not None:
        event["source"] = dump_model(source, EventSource)
    if transparency is not None:
        event["transparency"] = EventTransparency(transparency).value
    if eventType is not None:
        event_type = EventType(eventType)
        event["eventType"] = event_type.value
        for property_event_type, property_field in EVENT_TYPE_PROPERTY_FIELDS.items():
            if property_event_type != event_type:
                event.pop(property_field, None)
    if outOfOfficeProperties is not None:
        event["outOfOfficeProperties"] = dump_model(outOfOfficeProperties, OutOfOfficeProperties)
    if focusTimeProperties is not None:
        event["focusTimeProperties"] = dump_model(focusTimeProperties, FocusTimeProperties)
    if workingLocationProperties is not None:
        event["workingLocationProperties"] = dump_model(workingLocationProperties, WorkingLocationProperties)
    if birthdayProperties is not None:
        event["birthdayProperties"] = dump_model(birthdayProperties, BirthdayProperties)

    event["updated"] = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    try:
        event = Event.model_validate(event).model_dump(exclude_none=True)
    except ValidationError as e:
        return {"status": "error", "message": str(e)}
    events[eventId] = event
    if error := save_data_or_error(data):
        return error

    return {"status": "success", "event": event}


def delete_event(eventId: EventId, calendar_id: CalendarId = "primary") -> dict:
    """
    Deletes an event from the calendar.

    Args:
        eventId: ID of the event to delete
        calendar_id: Calendar containing the event (default 'primary')

    Returns:
        Confirmation of deletion
    """
    data = load_data()

    deleted_event = delete_calendar_event(data, calendar_id, eventId)
    if deleted_event is None:
        return {"status": "error", "message": f"Event with ID '{eventId}' not found in calendar '{calendar_id}'"}

    if error := save_data_or_error(data):
        return error

    return {"status": "success", "message": f"Event '{deleted_event['summary']}' deleted"}


def respond_to_event(
    eventId: EventId,
    email: EmailStr,
    response: AttendeeResponseStatus,
    calendar_id: CalendarId = "primary",
) -> dict:
    """
    Update an attendee's RSVP response on an event.

    Args:
        eventId: ID of the event
        email: Email address of the attendee responding
        response: Response status — 'accepted', 'declined', 'tentative', or 'needsAction'
        calendar_id: Calendar containing the event (default 'primary')

    Returns:
        The updated event with attendee responses
    """
    data = load_data()
    events = get_calendar_events(data, calendar_id)

    if eventId not in events:
        return {"status": "error", "message": f"Event with ID '{eventId}' not found in calendar '{calendar_id}'"}

    event = events[eventId]
    attendees = event.get("attendees", [])

    found = False
    for attendee in attendees:
        if attendee.get("email", "").lower() == email.lower():
            attendee["responseStatus"] = response.value
            found = True
            break

    if not found:
        return {"status": "error", "message": f"Attendee '{email}' not found on this event"}

    event["updated"] = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    if error := save_data_or_error(data):
        return error

    return {"status": "success", "event": event}
