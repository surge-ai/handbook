"""Search, listing, and recurrence helpers for the Google Calendar mock."""

import re
from datetime import UTC, datetime, timedelta
from typing import cast

from pydantic import EmailStr

from google_calendar.models import (
    DEFAULT_RECURRING_SEARCH_DAYS,
    AttendeeResponseStatus,
    CalendarId,
    ListEventsMaxResults,
    Rfc3339OffsetDateTimeString,
    SearchEventsMaxResults,
    SearchEventsOrderBy,
    SearchEventsResponse,
    parse_rfc3339_datetime,
)
from google_calendar.state import get_calendar_events, load_data

_QUERY_TOKEN_RE = re.compile(r'"([^"]+)"|\S+')

_DAY_MAP = {"MO": 0, "TU": 1, "WE": 2, "TH": 3, "FR": 4, "SA": 5, "SU": 6}


def _parse_rrule(rrule_str: str) -> dict:
    """Parse an RRULE string into a dict of parameters."""
    params: dict = {}
    rule = rrule_str.replace("RRULE:", "")
    for part in rule.split(";"):
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        params[key] = value
    return params


def _expand_recurring_event(
    event: dict,
    range_start: datetime,
    range_end: datetime,
) -> list[dict]:
    """Expand a recurring event into instances within a time range.

    Supports: FREQ=DAILY|WEEKLY|MONTHLY, INTERVAL, COUNT, UNTIL, BYDAY.
    Returns a list of synthetic event dicts (copies with adjusted start/end).
    """
    recurrence = event.get("recurrence", [])
    if not recurrence:
        return []

    # Find the RRULE
    rrule_str = None
    for r in recurrence:
        if r.startswith("RRULE:"):
            rrule_str = r
            break
    if not rrule_str:
        return []

    params = _parse_rrule(rrule_str)
    freq = params.get("FREQ", "").upper()
    interval = int(params.get("INTERVAL", "1"))
    count = int(params.get("COUNT", "0")) or None
    until_str = params.get("UNTIL")
    byday = params.get("BYDAY", "").split(",") if params.get("BYDAY") else None

    # Parse event start/end to get duration
    event_start = _parse_event_start(event)
    event_end = _parse_event_end(event)
    if event_start is None or event_end is None:
        return []
    duration = event_end - event_start

    # Parse UNTIL. Normalize to match event_start's tz-awareness so comparisons
    # don't raise when RRULE UNTIL is specified without an offset.
    until_dt = None
    if until_str:
        import contextlib

        try:
            until_dt = datetime.fromisoformat(until_str)
        except ValueError:
            with contextlib.suppress(ValueError):
                until_dt = datetime.strptime(until_str, "%Y%m%dT%H%M%SZ").replace(tzinfo=event_start.tzinfo)
        if until_dt is not None and until_dt.tzinfo is None and event_start.tzinfo is not None:
            until_dt = until_dt.replace(tzinfo=event_start.tzinfo)

    # Normalize timezone for comparison
    if event_start.tzinfo is not None and range_start.tzinfo is None:
        range_start = range_start.replace(tzinfo=event_start.tzinfo)
        range_end = range_end.replace(tzinfo=event_start.tzinfo)
    elif event_start.tzinfo is None and range_start.tzinfo is not None:
        event_start = event_start.replace(tzinfo=range_start.tzinfo)

    # Generate instances
    instances = []
    current = event_start
    instance_count = 0
    max_iterations = 1000  # safety limit

    for _ in range(max_iterations):
        if until_dt and current > until_dt:
            break
        if count and instance_count >= count:
            break
        if current >= range_end:
            break

        current_end = current + duration

        # Check if this instance matches BYDAY (for WEEKLY)
        day_match = True
        if byday and freq == "WEEKLY":
            day_abbr = ["MO", "TU", "WE", "TH", "FR", "SA", "SU"][current.weekday()]
            day_match = day_abbr in byday

        if day_match:
            instance_count += 1
            # Only include if it overlaps the query range
            if current_end > range_start and current < range_end:
                instance = dict(event)
                instance["id"] = f"{event['id']}_{instance_count}"
                instance["recurringEventId"] = event["id"]
                if "dateTime" in event.get("start", {}):
                    instance["start"] = {"dateTime": current.isoformat()}
                    instance["end"] = {"dateTime": current_end.isoformat()}
                    if time_zone := event.get("start", {}).get("timeZone"):
                        instance["start"]["timeZone"] = time_zone
                    if time_zone := event.get("end", {}).get("timeZone"):
                        instance["end"]["timeZone"] = time_zone
                else:
                    instance["start"] = {"date": current.strftime("%Y-%m-%d")}
                    instance["end"] = {"date": current_end.strftime("%Y-%m-%d")}
                # Don't include recurrence on instances
                instance.pop("recurrence", None)
                instances.append(instance)

        # Advance to next occurrence
        if freq == "DAILY":
            current += timedelta(days=interval)
        elif freq == "WEEKLY":
            if byday:
                # Advance one day at a time for BYDAY matching
                current += timedelta(days=1)
            else:
                current += timedelta(weeks=interval)
        elif freq == "MONTHLY":
            month = current.month + interval
            year = current.year + (month - 1) // 12
            month = ((month - 1) % 12) + 1
            try:
                current = current.replace(year=year, month=month)
            except ValueError:
                break  # e.g., Jan 31 → Feb 31 doesn't exist
        else:
            break  # Unsupported frequency

    return instances


def _parse_dt(dt_obj: dict) -> datetime | None:
    """Parse a dateTime or date field from an event start/end object.

    Always returns a timezone-aware datetime so comparisons against offset-aware
    query bounds don't raise. Offset-less dateTime strings use the event's
    timeZone when present. All-day events (date-only) are treated as UTC.
    Tolerant of data where dateTime is explicitly null (common in all-day
    events that also carry a date field).
    """

    dt = dt_obj.get("dateTime")
    if isinstance(dt, str):
        time_zone = dt_obj.get("timeZone")
        if not isinstance(time_zone, str):
            time_zone = None
        try:
            parsed = parse_rfc3339_datetime(dt, time_zone)
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


def _parse_event_start(event: dict) -> datetime | None:
    """Parse event start time from either dateTime or date field."""
    return _parse_dt(event.get("start", {}))


def _parse_event_end(event: dict) -> datetime | None:
    """Parse event end time from either dateTime or date field."""
    end = _parse_dt(event.get("end", {}))
    start_obj = event.get("start", {})
    end_obj = event.get("end", {})
    if (
        end is not None
        and isinstance(start_obj, dict)
        and isinstance(end_obj, dict)
        and "dateTime" not in start_obj
        and "dateTime" not in end_obj
        and isinstance(start_obj.get("date"), str)
        and isinstance(end_obj.get("date"), str)
    ):
        start = _parse_dt(start_obj)
        if start is not None and end <= start:
            return start + timedelta(days=1)
    return end


def _get_event_sort_key(event: dict) -> str:
    """Get a sortable string key for event start time.

    dateTime takes precedence, then date. Tolerates events where dateTime is
    explicitly null by falling through to date.
    """
    start = event.get("start", {})
    dt = start.get("dateTime")
    if isinstance(dt, str):
        return dt
    d = start.get("date")
    if isinstance(d, str):
        return d
    return ""


def _parse_search_tokens(query: str) -> list[str]:
    """Parse a query into lower-case word tokens and quoted phrase tokens."""
    tokens = []
    for match in _QUERY_TOKEN_RE.finditer(query):
        token = (match.group(1) or match.group(0)).strip().lower()
        if token:
            tokens.append(token)
    return tokens


def _search_query_warnings(query: str) -> list[str]:
    """Warn when users put unsupported operator syntax in the free-text query."""
    warnings: list[str] = []
    seen: set[str] = set()
    for match in re.finditer(r"\b([A-Za-z_]+):\S+", query):
        operator = match.group(1)
        if operator.lower() in {"http", "https", "mailto"}:
            continue
        message = (
            f"Unsupported Calendar query operator '{operator}:'; use search_events parameters "
            "such as timeMin, timeMax, attendee_email, response_status, organizer_email, or creator_email."
        )
        if message not in seen:
            warnings.append(message)
            seen.add(message)
    return warnings


def _event_search_haystack(event: dict, calendar_id: str, calendar_summary: str | None = None) -> str:
    """Build searchable event text, using newlines so phrases don't span fields."""
    parts = [
        event.get("summary", ""),
        event.get("description", ""),
        event.get("location", ""),
        calendar_id,
        calendar_summary or "",
    ]
    for person_field in ("organizer", "creator"):
        person = event.get(person_field)
        if isinstance(person, dict):
            parts.extend([person.get("email", ""), person.get("displayName", "")])
        elif person:
            parts.append(person)
    for attendee in event.get("attendees", []) or []:
        parts.extend(
            [
                attendee.get("email", ""),
                attendee.get("displayName", ""),
                attendee.get("comment", ""),
            ]
        )
    return "\n".join(str(part) for part in parts if part).lower()


def _extract_person_emails(value: object) -> list[str]:
    if isinstance(value, dict):
        person = cast("dict[str, object]", value)
        email = person.get("email")
        return [str(email).lower()] if email else []
    if isinstance(value, str):
        return [value.lower()]
    return []


def _event_matches_attendee(event: dict, attendee_email: str | None) -> bool:
    if not attendee_email:
        return True
    needle = attendee_email.lower()
    for attendee in event.get("attendees", []) or []:
        email = str(attendee.get("email", "")).lower()
        display_name = str(attendee.get("displayName", "")).lower()
        if needle in email or needle in display_name:
            return True
    return False


def _event_matches_response_status(
    event: dict, response_status: AttendeeResponseStatus | None, attendee_email: str | None
) -> bool:
    if response_status is None:
        return True
    needle = response_status.value.lower()
    attendee_needle = attendee_email.lower() if attendee_email else None
    for attendee in event.get("attendees", []) or []:
        if attendee_needle:
            email = str(attendee.get("email", "")).lower()
            display_name = str(attendee.get("displayName", "")).lower()
            if attendee_needle not in email and attendee_needle not in display_name:
                continue
        if str(attendee.get("responseStatus", "")).lower() == needle:
            return True
    return False


def _event_matches_creator(event: dict, creator_email: str | None) -> bool:
    if not creator_email:
        return True
    needle = creator_email.lower()
    return any(needle in email for email in _extract_person_emails(event.get("creator")))


def _event_matches_organizer(event: dict, organizer_email: str | None) -> bool:
    if not organizer_email:
        return True
    needle = organizer_email.lower()
    if any(needle in email for email in _extract_person_emails(event.get("organizer"))):
        return True
    for attendee in event.get("attendees", []) or []:
        if attendee.get("organizer") and needle in str(attendee.get("email", "")).lower():
            return True
    return False


def _normalize_range_bound(value: str | None) -> datetime | None:
    if value is None:
        return None
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _event_overlaps_range(event: dict, time_min: datetime | None, time_max: datetime | None) -> bool:
    event_start = _parse_event_start(event)
    event_end = _parse_event_end(event)
    if event_start is None or event_end is None:
        return False

    if time_min is not None:
        if event_end.tzinfo is None and time_min.tzinfo is not None:
            event_end = event_end.replace(tzinfo=time_min.tzinfo)
        elif event_end.tzinfo is not None and time_min.tzinfo is None:
            event_end = event_end.replace(tzinfo=None)
        if event_end <= time_min:
            return False

    if time_max is not None:
        if event_start.tzinfo is None and time_max.tzinfo is not None:
            event_start = event_start.replace(tzinfo=time_max.tzinfo)
        elif event_start.tzinfo is not None and time_max.tzinfo is None:
            event_start = event_start.replace(tzinfo=None)
        if event_start >= time_max:
            return False

    return True


def _recurring_search_range(
    event: dict, time_min: datetime | None, time_max: datetime | None
) -> tuple[datetime, datetime] | None:
    """Return bounded expansion range for recurring search results."""
    event_start = _parse_event_start(event)
    if event_start is None:
        return None

    range_start = time_min or event_start
    range_end = time_max or event_start + timedelta(days=DEFAULT_RECURRING_SEARCH_DAYS)
    if range_end <= range_start:
        return None
    return range_start, range_end


def _iter_calendar_events(data: dict, calendar_id: str | None = None) -> list[tuple[str, str, dict]]:
    """Return (calendar_id, calendar_summary, event) tuples for requested calendars."""
    if calendar_id:
        calendar_summary = (
            "Primary"
            if calendar_id == "primary"
            else data.get("calendars", {}).get(calendar_id, {}).get("summary", calendar_id)
        )
        return [(calendar_id, calendar_summary, event) for event in get_calendar_events(data, calendar_id).values()]

    events = [("primary", "Primary", event) for event in data.get("events", {}).values()]
    for cid, cal_data in data.get("calendars", {}).items():
        calendar_summary = cal_data.get("summary", cid)
        events.extend((cid, calendar_summary, event) for event in cal_data.get("events", {}).values())
    return events


def list_events(
    timeMin: Rfc3339OffsetDateTimeString,
    timeMax: Rfc3339OffsetDateTimeString,
    maxResults: ListEventsMaxResults | None = None,
    orderBy: SearchEventsOrderBy | None = None,
    calendar_id: CalendarId | None = None,
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

    Returns:
        List of events that overlap the given time range
    """
    data = load_data()

    # Parse time boundaries
    try:
        time_min = datetime.fromisoformat(timeMin)
        time_max = datetime.fromisoformat(timeMax)
    except ValueError as e:
        return {"status": "error", "message": f"Invalid time format: {e}"}

    # Ensure time_min and time_max have consistent timezone awareness
    if time_min.tzinfo is None and time_max.tzinfo is not None:
        time_min = time_min.replace(tzinfo=time_max.tzinfo)
    elif time_min.tzinfo is not None and time_max.tzinfo is None:
        time_max = time_max.replace(tzinfo=time_min.tzinfo)

    # Collect events from requested calendar(s). Event IDs are scoped to a
    # calendar, so all-calendar queries must not merge by event ID.
    all_events: list[dict] = []
    if calendar_id:
        all_events = list(get_calendar_events(data, calendar_id).values())
    else:
        all_events.extend(data.get("events", {}).values())
        for cal_data in data.get("calendars", {}).values():
            all_events.extend(cal_data.get("events", {}).values())

    # Expand recurring events and filter by time range
    filtered_events = []
    skipped_ids = []
    for event in all_events:
        # Expand recurring events into instances
        if event.get("recurrence"):
            instances = _expand_recurring_event(event, time_min, time_max)
            filtered_events.extend(instances)
            continue

        event_start = _parse_event_start(event)
        event_end = _parse_event_end(event)
        if event_start is None or event_end is None:
            skipped_ids.append(event.get("id", "unknown"))
            continue
        # Normalize timezone awareness for comparison
        if event_start.tzinfo is None and time_min.tzinfo is not None:
            event_start = event_start.replace(tzinfo=time_min.tzinfo)
        elif event_start.tzinfo is not None and time_min.tzinfo is None:
            event_start = event_start.replace(tzinfo=None)
        if event_end.tzinfo is None and time_min.tzinfo is not None:
            event_end = event_end.replace(tzinfo=time_min.tzinfo)
        elif event_end.tzinfo is not None and time_min.tzinfo is None:
            event_end = event_end.replace(tzinfo=None)
        # Overlap: event ends after timeMin AND event starts before timeMax
        if event_end > time_min and event_start < time_max:
            filtered_events.append(event)

    # Sort events
    if orderBy == SearchEventsOrderBy.START_TIME:
        filtered_events.sort(key=_get_event_sort_key)
    elif orderBy == SearchEventsOrderBy.UPDATED:
        filtered_events.sort(key=lambda e: e.get("updated", ""), reverse=True)

    # Limit results
    if maxResults is not None:
        filtered_events = filtered_events[:maxResults]

    result = {
        "status": "success",
        "events": filtered_events,
        "count": len(filtered_events),
    }
    if skipped_ids:
        result["skipped_unparseable"] = skipped_ids
    return result


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

    Returns:
        Matching events with calendar_id annotated on each result.
    """
    tokens = _parse_search_tokens(query)
    warnings = _search_query_warnings(query)
    if not any([tokens, attendee_email, response_status, organizer_email, creator_email]):
        return {
            "status": "error",
            "message": "query or at least one filter is required",
            "events": [],
            "count": 0,
            "warnings": warnings,
        }

    try:
        time_min = _normalize_range_bound(timeMin)
        time_max = _normalize_range_bound(timeMax)
    except ValueError as e:
        return {
            "status": "error",
            "message": f"Invalid time format: {e}",
            "events": [],
            "count": 0,
            "warnings": warnings,
        }

    data = load_data()
    matching_events = []
    skipped_ids = []

    for cid, calendar_summary, event in _iter_calendar_events(data, calendar_id):
        candidates = [event]
        if event.get("recurrence"):
            recurring_range = _recurring_search_range(event, time_min, time_max)
            if recurring_range is None:
                skipped_ids.append(event.get("id", "unknown"))
                continue
            candidates = _expand_recurring_event(event, recurring_range[0], recurring_range[1])

        for candidate in candidates:
            if (time_min is not None or time_max is not None) and not _event_overlaps_range(
                candidate, time_min, time_max
            ):
                if _parse_event_start(candidate) is None or _parse_event_end(candidate) is None:
                    skipped_ids.append(candidate.get("id", "unknown"))
                continue
            if not _event_matches_attendee(candidate, attendee_email):
                continue
            if not _event_matches_response_status(candidate, response_status, attendee_email):
                continue
            if not _event_matches_organizer(candidate, organizer_email):
                continue
            if not _event_matches_creator(candidate, creator_email):
                continue
            haystack = _event_search_haystack(candidate, cid, calendar_summary)
            if tokens and not all(token in haystack for token in tokens):
                continue

            result_event = dict(candidate)
            result_event["calendar_id"] = cid
            result_event["calendar_summary"] = calendar_summary
            matching_events.append(result_event)

    if orderBy == SearchEventsOrderBy.START_TIME:
        matching_events.sort(key=_get_event_sort_key)
    elif orderBy == SearchEventsOrderBy.UPDATED:
        matching_events.sort(key=lambda e: e.get("updated", ""), reverse=True)

    if maxResults is not None:
        matching_events = matching_events[:maxResults]

    result: SearchEventsResponse = {"status": "success", "events": matching_events, "count": len(matching_events)}
    result["warnings"] = warnings
    if skipped_ids:
        result["skipped_unparseable"] = skipped_ids
        result["warnings"].append(
            "Some recurring or malformed events were skipped because they could not be expanded or parsed."
        )
    return result
