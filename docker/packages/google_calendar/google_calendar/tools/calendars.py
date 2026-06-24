"""Calendar metadata tool implementations for the Google Calendar mock."""

from google_calendar.models import DEFAULT_CALENDAR_TIME_ZONE, Calendar, CalendarSummary, CalendarTimeZone
from google_calendar.state import get_all_calendars, load_data, save_data_or_error


def create_calendar(
    summary: CalendarSummary,
    description: str | None = None,
    timeZone: CalendarTimeZone | None = None,
) -> dict:
    """
    Creates a new calendar (e.g., 'Personal', 'Team Meetings', 'On-Call').

    Args:
        summary: Calendar name
        description: Calendar description
        timeZone: IANA time zone for the calendar. Defaults to the primary calendar time zone.

    Returns:
        The created calendar object
    """
    data = load_data()

    # Generate calendar ID from summary
    cal_id = summary.lower().replace(" ", "-")
    cal_id = "".join(c for c in cal_id if c.isalnum() or c == "-")
    cal_id = cal_id.strip("-")

    if "calendars" not in data:
        data["calendars"] = {}

    if not cal_id:
        return {
            "status": "error",
            "message": "Calendar summary must contain at least one letter or number",
        }

    if cal_id in data["calendars"] or cal_id == "primary":
        return {"status": "error", "message": f"Calendar '{cal_id}' already exists"}

    calendar = Calendar.model_validate(
        {
            "summary": summary,
            "description": description or "",
            "timeZone": timeZone or data.get("timeZone", DEFAULT_CALENDAR_TIME_ZONE),
            "events": {},
        }
    ).model_dump(mode="json")

    data["calendars"][cal_id] = calendar
    if error := save_data_or_error(data):
        return error

    return {
        "status": "success",
        "calendar": {
            "id": cal_id,
            "summary": calendar["summary"],
            "description": calendar["description"],
            "timeZone": calendar["timeZone"],
            "primary": False,
            "eventCount": 0,
        },
    }


def list_calendars() -> dict:
    """
    Lists all calendars. Always includes the 'primary' calendar plus any
    additional calendars that have been created.

    Returns:
        List of calendars with their IDs, names, and event counts
    """
    data = load_data()
    calendars = get_all_calendars(data)
    return {"status": "success", "calendars": calendars, "count": len(calendars)}
