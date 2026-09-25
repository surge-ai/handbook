"""Availability tool implementation for the Google Calendar mock."""

from datetime import datetime, timedelta

from pydantic import EmailStr

from google_calendar.models import (
    AvailabilityDurationMinutes,
    CalendarId,
    EventTransparency,
    Rfc3339OffsetDateTimeString,
)
from google_calendar.state import get_calendar_events, load_data
from google_calendar.tools.search import _expand_recurring_event, _parse_event_end, _parse_event_start


def check_availability(
    timeMin: Rfc3339OffsetDateTimeString,
    timeMax: Rfc3339OffsetDateTimeString,
    duration_minutes: AvailabilityDurationMinutes = 30,
    calendar_id: CalendarId | None = None,
    attendee_emails: list[EmailStr] | None = None,
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

    Returns:
        Busy periods and available free slots within the range
    """
    data = load_data()

    # Parse boundaries
    try:
        range_start = datetime.fromisoformat(timeMin)
        range_end = datetime.fromisoformat(timeMax)
    except ValueError as e:
        return {"status": "error", "message": f"Invalid time format: {e}"}

    # Normalize timezone awareness
    if range_start.tzinfo is None and range_end.tzinfo is not None:
        range_start = range_start.replace(tzinfo=range_end.tzinfo)
    elif range_start.tzinfo is not None and range_end.tzinfo is None:
        range_end = range_end.replace(tzinfo=range_start.tzinfo)

    # Collect events from requested calendar(s). Event IDs are scoped to a
    # calendar, so all-calendar availability must not merge by event ID.
    all_events: list[dict] = []
    if calendar_id:
        all_events = list(get_calendar_events(data, calendar_id).values())
    else:
        all_events.extend(data.get("events", {}).values())
        for cal_data in data.get("calendars", {}).values():
            all_events.extend(cal_data.get("events", {}).values())

    # Expand recurring events and collect all event instances
    expanded_events: list[dict] = []
    for event in all_events:
        if event.get("recurrence"):
            expanded_events.extend(_expand_recurring_event(event, range_start, range_end))
        else:
            expanded_events.append(event)

    # Filter by attendee emails if specified
    if attendee_emails:
        emails_lower = {e.lower() for e in attendee_emails}
        filtered = []
        for event in expanded_events:
            event_attendees = event.get("attendees", [])
            if not event_attendees:
                # Events without attendees are considered relevant (owner's events)
                filtered.append(event)
                continue
            for a in event_attendees:
                if a.get("email", "").lower() in emails_lower and a.get("responseStatus") != "declined":
                    filtered.append(event)
                    break
        expanded_events = filtered

    # Find busy periods within the range
    busy_periods: list[tuple[datetime, datetime, str]] = []
    for event in expanded_events:
        if event.get("transparency") == EventTransparency.TRANSPARENT.value:
            continue

        event_start = _parse_event_start(event)
        event_end = _parse_event_end(event)
        if event_start is None or event_end is None:
            continue

        # Normalize timezone
        if event_start.tzinfo is None and range_start.tzinfo is not None:
            event_start = event_start.replace(tzinfo=range_start.tzinfo)
        elif event_start.tzinfo is not None and range_start.tzinfo is None:
            event_start = event_start.replace(tzinfo=None)
        if event_end.tzinfo is None and range_start.tzinfo is not None:
            event_end = event_end.replace(tzinfo=range_start.tzinfo)
        elif event_end.tzinfo is not None and range_start.tzinfo is None:
            event_end = event_end.replace(tzinfo=None)

        # Check overlap with requested range
        if event_end > range_start and event_start < range_end:
            # Clamp to range
            clamped_start = max(event_start, range_start)
            clamped_end = min(event_end, range_end)
            busy_periods.append((clamped_start, clamped_end, event.get("summary", "")))

    # Sort busy periods by start time
    busy_periods.sort(key=lambda x: x[0])

    # Merge overlapping busy periods
    merged: list[tuple[datetime, datetime, list[str]]] = []
    for start, end, summary in busy_periods:
        if merged and start <= merged[-1][1]:
            # Overlapping — extend
            merged[-1] = (merged[-1][0], max(merged[-1][1], end), merged[-1][2] + [summary])
        else:
            merged.append((start, end, [summary]))

    # Find free slots
    min_duration = timedelta(minutes=duration_minutes)
    free_slots: list[dict] = []
    cursor = range_start

    for busy_start, busy_end, _summaries in merged:
        gap = busy_start - cursor
        if gap >= min_duration:
            free_slots.append(
                {
                    "start": cursor.isoformat(),
                    "end": busy_start.isoformat(),
                    "duration_minutes": int(gap.total_seconds() / 60),
                }
            )
        cursor = max(cursor, busy_end)

    # Check final gap
    final_gap = range_end - cursor
    if final_gap >= min_duration:
        free_slots.append(
            {
                "start": cursor.isoformat(),
                "end": range_end.isoformat(),
                "duration_minutes": int(final_gap.total_seconds() / 60),
            }
        )

    busy_formatted = [
        {
            "start": s.isoformat(),
            "end": e.isoformat(),
            "events": summaries,
        }
        for s, e, summaries in merged
    ]

    return {
        "status": "success",
        "busy": busy_formatted,
        "free_slots": free_slots,
        "total_busy_minutes": sum(int((e - s).total_seconds() / 60) for s, e, _ in merged),
        "total_free_minutes": sum(slot["duration_minutes"] for slot in free_slots),
    }
