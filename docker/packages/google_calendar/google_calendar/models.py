"""Pydantic models and typed aliases for the Google Calendar mock."""

import re
from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Annotated, Any, TypedDict, cast
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, EmailStr, Field, field_validator, model_validator

_RFC3339_DATETIME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?$")
_RFC3339_OFFSET_DATETIME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
DEFAULT_RECURRING_SEARCH_DAYS = 365
DEFAULT_CALENDAR_TIME_ZONE = "UTC"
# ---------------------------------------------------------------------------
# Pydantic Types for Event Ingestion
# ---------------------------------------------------------------------------

NonEmptyStateString = Annotated[str, Field(min_length=1)]
Rfc3339DateTimeString = Annotated[str, Field(pattern=_RFC3339_DATETIME_RE.pattern)]
Rfc3339OffsetDateTimeString = Annotated[str, Field(pattern=_RFC3339_OFFSET_DATETIME_RE.pattern)]
CalendarDateString = Annotated[str, Field(pattern=_DATE_RE.pattern)]
EventId = NonEmptyStateString
CalendarId = NonEmptyStateString
CalendarAccountId = NonEmptyStateString
CalendarTimeZone = Annotated[str, Field(description="IANA time zone name, e.g. America/New_York")]
EventSummary = NonEmptyStateString
CalendarSummary = NonEmptyStateString
EventQuery = NonEmptyStateString
RecurrenceRuleString = Annotated[str, Field(pattern=r"^(?:RRULE|EXRULE|RDATE|EXDATE):")]
PeopleResourceName = Annotated[
    str,
    Field(
        pattern=r"^people/c[0-9]+$",
        description='Google People API resource name used by Calendar birthdays, e.g. "people/c12345".',
    ),
]
ListEventsMaxResults = Annotated[int, Field(ge=0)]
AvailabilityDurationMinutes = Annotated[int, Field(ge=1)]


def _validate_time_zone(value: str) -> ZoneInfo:
    try:
        return ZoneInfo(value)
    except ZoneInfoNotFoundError as e:
        raise ValueError("timeZone must be a valid IANA time zone") from e


def parse_rfc3339_datetime(value: str, time_zone: str | None = None) -> datetime:
    if not _RFC3339_DATETIME_RE.fullmatch(value):
        raise ValueError("dateTime must be an RFC 3339 date-time")
    normalized = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as e:
        raise ValueError("dateTime must be a valid RFC 3339 date-time") from e
    if time_zone is not None:
        zone = _validate_time_zone(time_zone)
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=zone)
        expected_offset = zone.utcoffset(parsed.replace(tzinfo=None))
        if expected_offset != parsed.utcoffset():
            raise ValueError("dateTime offset must match timeZone")
        return parsed.astimezone(zone)
    return parsed


def _parse_calendar_date(value: str) -> date:
    if not _DATE_RE.fullmatch(value):
        raise ValueError("date must use YYYY-MM-DD format")
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as e:
        raise ValueError("date must be a valid calendar date") from e


class EventStatus(StrEnum):
    """Event status — mirrors Google Calendar API values."""

    CONFIRMED = "confirmed"
    TENTATIVE = "tentative"
    CANCELLED = "cancelled"


class EventVisibility(StrEnum):
    """Event visibility — mirrors Google Calendar API values."""

    DEFAULT = "default"
    PUBLIC = "public"
    PRIVATE = "private"
    CONFIDENTIAL = "confidential"


class EventTransparency(StrEnum):
    """Whether an event blocks availability in free/busy calculations."""

    OPAQUE = "opaque"
    TRANSPARENT = "transparent"


class EventType(StrEnum):
    """Event type values supported by Google Calendar."""

    DEFAULT = "default"
    OUT_OF_OFFICE = "outOfOffice"
    FOCUS_TIME = "focusTime"
    FROM_GMAIL = "fromGmail"
    WORKING_LOCATION = "workingLocation"
    BIRTHDAY = "birthday"


class EventClearField(StrEnum):
    """Optional event fields that update_event can remove explicitly."""

    DESCRIPTION = "description"
    LOCATION = "location"
    STATUS = "status"
    COLOR_ID = "colorId"
    VISIBILITY = "visibility"
    TRANSPARENCY = "transparency"
    RECURRENCE = "recurrence"
    REMINDERS = "reminders"
    ATTENDEES = "attendees"
    CREATOR = "creator"
    ORGANIZER = "organizer"
    EXTENDED_PROPERTIES = "extendedProperties"
    SOURCE = "source"
    OUT_OF_OFFICE_PROPERTIES = "outOfOfficeProperties"
    FOCUS_TIME_PROPERTIES = "focusTimeProperties"
    WORKING_LOCATION_PROPERTIES = "workingLocationProperties"
    BIRTHDAY_PROPERTIES = "birthdayProperties"
    HTML_LINK = "htmlLink"
    HANGOUT_LINK = "hangoutLink"


EVENT_TYPE_PROPERTY_FIELDS = {
    EventType.OUT_OF_OFFICE: "outOfOfficeProperties",
    EventType.FOCUS_TIME: "focusTimeProperties",
    EventType.WORKING_LOCATION: "workingLocationProperties",
    EventType.BIRTHDAY: "birthdayProperties",
}


class AutoDeclineMode(StrEnum):
    """Auto-decline behavior for focus time and out-of-office events."""

    DECLINE_NONE = "declineNone"
    DECLINE_ALL_CONFLICTING_INVITATIONS = "declineAllConflictingInvitations"
    DECLINE_ONLY_NEW_CONFLICTING_INVITATIONS = "declineOnlyNewConflictingInvitations"


class FocusTimeChatStatus(StrEnum):
    """Chat status values for focus time events."""

    AVAILABLE = "available"
    DO_NOT_DISTURB = "doNotDisturb"


class WorkingLocationType(StrEnum):
    """Working location type values supported by Google Calendar."""

    HOME_OFFICE = "homeOffice"
    OFFICE_LOCATION = "officeLocation"
    CUSTOM_LOCATION = "customLocation"


class BirthdayType(StrEnum):
    """Birthday or special-date type values supported by Google Calendar."""

    ANNIVERSARY = "anniversary"
    BIRTHDAY = "birthday"
    CUSTOM = "custom"
    OTHER = "other"
    SELF = "self"


class AttendeeResponseStatus(StrEnum):
    """Attendee RSVP state — mirrors Google Calendar API values."""

    NEEDS_ACTION = "needsAction"
    DECLINED = "declined"
    TENTATIVE = "tentative"
    ACCEPTED = "accepted"


class SearchEventsOrderBy(StrEnum):
    """Sort options for search_events results."""

    START_TIME = "startTime"
    UPDATED = "updated"


class ReminderMethod(StrEnum):
    """Event reminder delivery methods supported by Google Calendar."""

    EMAIL = "email"
    POPUP = "popup"


SearchEventsMaxResults = Annotated[int, Field(ge=0)]
ReminderMinutes = Annotated[int, Field(ge=0, le=40320)]


class EventDateTime(BaseModel):
    """Time specification for an event — mirrors the Google Calendar API shape."""

    model_config = {"extra": "forbid"}

    dateTime: Rfc3339DateTimeString | None = None  # RFC 3339 format for timed events
    date: CalendarDateString | None = None  # YYYY-MM-DD for all-day events
    timeZone: CalendarTimeZone | None = None

    @model_validator(mode="after")
    def validate_date_or_datetime(self) -> "EventDateTime":
        has_datetime = bool(self.dateTime)
        has_date = bool(self.date)
        if has_datetime == has_date:
            raise ValueError("Event time must include exactly one of dateTime or date")

        if self.timeZone is not None:
            _validate_time_zone(self.timeZone)
        if self.dateTime is not None:
            parsed = parse_rfc3339_datetime(self.dateTime, self.timeZone)
            if parsed.tzinfo is None and not self.timeZone:
                raise ValueError("dateTime without an offset requires timeZone")
        if self.date is not None:
            _parse_calendar_date(self.date)

        return self

    def _sort_value(self) -> Any:
        if self.dateTime is not None:
            return parse_rfc3339_datetime(self.dateTime, self.timeZone)
        if self.date is not None:
            return _parse_calendar_date(self.date)
        raise ValueError("Event time must include exactly one of dateTime or date")


class EventAttendee(BaseModel):
    """Attendee on an event — mirrors the Google Calendar API shape."""

    model_config = {"extra": "forbid"}

    email: EmailStr
    displayName: str | None = None
    organizer: bool | None = None
    self: bool | None = None
    resource: bool | None = None
    optional: bool | None = None
    responseStatus: AttendeeResponseStatus | None = None
    comment: str | None = None
    additionalGuests: int | None = Field(default=None, ge=0)


class CalendarPerson(BaseModel):
    """Creator or organizer person object on an event."""

    model_config = {"extra": "forbid"}

    id: str | None = None
    email: EmailStr | None = None
    displayName: str | None = None
    self: bool | None = None


class ExtendedProperties(BaseModel):
    """Arbitrary event metadata supported by Google Calendar."""

    model_config = {"extra": "forbid"}

    private: dict[str, str] | None = None
    shared: dict[str, str] | None = None


class EventSource(BaseModel):
    """Source metadata for an event."""

    model_config = {"extra": "forbid"}

    title: NonEmptyStateString | None = None
    # Add url once worlds have hosted or Drive-backed source links.


class EventReminder(BaseModel):
    """Event reminder override — mirrors Google Calendar API values."""

    model_config = {"extra": "forbid"}

    method: ReminderMethod
    minutes: ReminderMinutes


class EventReminders(BaseModel):
    """Reminder settings for an event."""

    model_config = {"extra": "forbid"}

    useDefault: bool
    overrides: list[EventReminder] | None = Field(default=None, min_length=1, max_length=5)

    @model_validator(mode="after")
    def validate_overrides_match_default_mode(self) -> "EventReminders":
        if self.useDefault and self.overrides is not None:
            raise ValueError("reminders.overrides cannot be set when useDefault is true")
        if not self.useDefault and self.overrides is None:
            raise ValueError("reminders.overrides is required when useDefault is false")
        return self


class OutOfOfficeProperties(BaseModel):
    """Out-of-office event data."""

    model_config = {"extra": "forbid"}

    autoDeclineMode: AutoDeclineMode | None = None
    declineMessage: str | None = None


class FocusTimeProperties(BaseModel):
    """Focus time event data."""

    model_config = {"extra": "forbid"}

    autoDeclineMode: AutoDeclineMode | None = None
    declineMessage: str | None = None
    chatStatus: FocusTimeChatStatus | None = None


class WorkingLocationCustomLocation(BaseModel):
    """Custom working location details."""

    model_config = {"extra": "forbid"}

    label: str | None = None


class WorkingLocationOfficeLocation(BaseModel):
    """Office working location details."""

    model_config = {"extra": "forbid"}

    buildingId: str | None = None
    floorId: str | None = None
    floorSectionId: str | None = None
    deskId: str | None = None
    label: str | None = None


class WorkingLocationHomeOffice(BaseModel):
    """Home office marker for working location events."""

    model_config = {"extra": "forbid"}


class WorkingLocationProperties(BaseModel):
    """Working location event data."""

    model_config = {"extra": "forbid"}

    type: WorkingLocationType
    homeOffice: WorkingLocationHomeOffice | None = None
    customLocation: WorkingLocationCustomLocation | None = None
    officeLocation: WorkingLocationOfficeLocation | None = None

    @model_validator(mode="after")
    def validate_location_matches_type(self) -> "WorkingLocationProperties":
        if self.type == WorkingLocationType.CUSTOM_LOCATION and self.customLocation is None:
            raise ValueError("customLocation is required when working location type is customLocation")
        if self.type == WorkingLocationType.OFFICE_LOCATION and self.officeLocation is None:
            raise ValueError("officeLocation is required when working location type is officeLocation")
        if self.type != WorkingLocationType.CUSTOM_LOCATION and self.customLocation is not None:
            raise ValueError("customLocation can only be set when type is customLocation")
        if self.type != WorkingLocationType.OFFICE_LOCATION and self.officeLocation is not None:
            raise ValueError("officeLocation can only be set when type is officeLocation")
        if self.type != WorkingLocationType.HOME_OFFICE and self.homeOffice is not None:
            raise ValueError("homeOffice can only be set when type is homeOffice")
        return self


class BirthdayProperties(BaseModel):
    """Birthday or special-date event data."""

    model_config = {"extra": "forbid"}

    type: BirthdayType = BirthdayType.BIRTHDAY
    contact: PeopleResourceName | None = None
    customTypeName: str | None = None

    @model_validator(mode="after")
    def validate_birthday_shape(self) -> "BirthdayProperties":
        if self.type == BirthdayType.SELF and self.contact is not None:
            raise ValueError("self birthday events cannot have contact")
        if self.type in {BirthdayType.ANNIVERSARY, BirthdayType.CUSTOM, BirthdayType.OTHER} and self.contact is None:
            raise ValueError(f"{self.type.value} birthday events require contact")
        if self.type == BirthdayType.CUSTOM and not self.customTypeName:
            raise ValueError("custom birthday events require customTypeName")
        if self.type != BirthdayType.CUSTOM and self.customTypeName is not None:
            raise ValueError("customTypeName can only be set when birthday type is custom")
        return self


class EventInput(BaseModel):
    """Schema for ingesting events from external systems (e.g., CSV, APIs)."""

    model_config = {"extra": "forbid"}

    summary: NonEmptyStateString
    start: EventDateTime
    end: EventDateTime
    description: str | None = None
    location: str | None = None
    status: EventStatus | None = None
    colorId: str | None = None
    visibility: EventVisibility | None = None
    transparency: EventTransparency | None = None
    recurrence: list[RecurrenceRuleString] | None = None
    reminders: EventReminders | None = None
    attendees: list[EventAttendee] | None = None
    creator: CalendarPerson | None = None
    organizer: CalendarPerson | None = None
    extendedProperties: ExtendedProperties | None = None
    source: EventSource | None = None
    eventType: EventType = EventType.DEFAULT
    outOfOfficeProperties: OutOfOfficeProperties | None = None
    focusTimeProperties: FocusTimeProperties | None = None
    workingLocationProperties: WorkingLocationProperties | None = None
    birthdayProperties: BirthdayProperties | None = None
    htmlLink: str | None = None
    hangoutLink: str | None = None

    @field_validator("recurrence", mode="before")
    @classmethod
    def _coerce_recurrence(cls, value: object) -> object:
        # Legacy fixtures sometimes store a single RRULE string rather than a list.
        if isinstance(value, str):
            return [value]
        return value

    @model_validator(mode="before")
    @classmethod
    def default_from_gmail_transparency(cls, value: object) -> object:
        if isinstance(value, dict):
            raw_value = cast(dict[str, object], value)
            if raw_value.get("eventType") != EventType.FROM_GMAIL.value:
                return value
            value = dict(raw_value)
            value.setdefault("transparency", EventTransparency.TRANSPARENT.value)
        return value

    @model_validator(mode="after")
    def validate_start_end_shape_and_order(self) -> "EventInput":
        start_is_datetime = self.start.dateTime is not None
        end_is_datetime = self.end.dateTime is not None
        if start_is_datetime != end_is_datetime:
            raise ValueError("Event start and end must both use dateTime or both use date")

        start_value = self.start._sort_value()
        end_value = self.end._sort_value()
        if isinstance(start_value, datetime) and isinstance(end_value, datetime):
            if start_value.tzinfo is None:
                start_value = start_value.replace(tzinfo=UTC)
            if end_value.tzinfo is None:
                end_value = end_value.replace(tzinfo=UTC)
        if end_value <= start_value:
            raise ValueError("Event end must be after start")

        return self

    @model_validator(mode="after")
    def validate_unique_attendees(self) -> "EventInput":
        if self.attendees is None:
            return self

        seen_emails = set()
        for attendee in self.attendees:
            email = attendee.email.lower()
            if email in seen_emails:
                raise ValueError(f"Duplicate attendee email: {attendee.email}")
            seen_emails.add(email)
        return self

    @model_validator(mode="after")
    def validate_event_type_properties(self) -> "EventInput":
        property_by_type = {
            EventType.OUT_OF_OFFICE: ("outOfOfficeProperties", self.outOfOfficeProperties),
            EventType.FOCUS_TIME: ("focusTimeProperties", self.focusTimeProperties),
            EventType.WORKING_LOCATION: ("workingLocationProperties", self.workingLocationProperties),
            EventType.BIRTHDAY: ("birthdayProperties", self.birthdayProperties),
        }
        for event_type, (field_name, value) in property_by_type.items():
            if self.eventType == event_type and value is None:
                raise ValueError(f"{field_name} is required when eventType is {event_type.value}")
            if self.eventType != event_type and value is not None:
                raise ValueError(f"{field_name} can only be set when eventType is {event_type.value}")
        return self


class Event(EventInput):
    """Full stored event with system-generated fields."""

    id: NonEmptyStateString
    # Optional so synthetic/legacy snapshots without audit timestamps still round-trip.
    created: Rfc3339DateTimeString | None = None
    updated: Rfc3339DateTimeString | None = None

    @field_validator("created", "updated")
    @classmethod
    def _validate_audit_timestamp(cls, value: str | None) -> str | None:
        if value is not None:
            parsed = parse_rfc3339_datetime(value)
            if parsed.tzinfo is None:
                raise ValueError("audit timestamps must include an offset")
        return value


def dump_model(value: Any, model_type: type[BaseModel]) -> dict[str, Any]:
    if isinstance(value, model_type):
        return value.model_dump(exclude_none=True)
    return model_type.model_validate(value).model_dump(exclude_none=True)


def event_attendee_payload(attendee: EventAttendee | dict[str, Any]) -> dict[str, Any]:
    if not isinstance(attendee, EventAttendee):
        attendee = EventAttendee.model_validate(attendee)
    payload = attendee.model_dump(exclude_none=True)
    payload["displayName"] = payload.get("displayName") or attendee.email
    payload["responseStatus"] = payload.get("responseStatus") or AttendeeResponseStatus.NEEDS_ACTION.value
    payload["organizer"] = payload.get("organizer", False)
    payload["self"] = payload.get("self", False)
    return payload


class Calendar(BaseModel):
    """Stored secondary calendar metadata and events."""

    model_config = {"extra": "forbid"}

    summary: NonEmptyStateString
    description: str = ""
    timeZone: CalendarTimeZone = DEFAULT_CALENDAR_TIME_ZONE
    events: dict[NonEmptyStateString, Event] = Field(default_factory=dict)

    @field_validator("timeZone")
    @classmethod
    def _validate_time_zone(cls, value: str) -> str:
        _validate_time_zone(value)
        return value

    @model_validator(mode="after")
    def validate_events_keyed_by_id(self) -> "Calendar":
        for key, event in self.events.items():
            if key != event.id:
                raise ValueError(f"events key {key!r} does not match event.id {event.id!r}")
        return self


class CalendarState(BaseModel):
    """Full google_calendar state — round-trips with load_data()/save_data()."""

    model_config = {"extra": "forbid"}

    timeZone: CalendarTimeZone = DEFAULT_CALENDAR_TIME_ZONE
    events: dict[NonEmptyStateString, Event] = Field(default_factory=dict)
    calendars: dict[NonEmptyStateString, Calendar] = Field(default_factory=dict)

    @field_validator("timeZone")
    @classmethod
    def _validate_time_zone(cls, value: str) -> str:
        _validate_time_zone(value)
        return value

    @model_validator(mode="after")
    def validate_primary_calendar_is_flat(self) -> "CalendarState":
        if "primary" in self.calendars:
            raise ValueError("Primary calendar events must be stored in top-level events, not calendars['primary']")
        return self

    @model_validator(mode="after")
    def validate_events_keyed_by_id(self) -> "CalendarState":
        for key, event in self.events.items():
            if key != event.id:
                raise ValueError(f"events key {key!r} does not match event.id {event.id!r}")
        return self


class CalendarAccountsState(BaseModel):
    """Multi-account google_calendar state wrapper.

    Each account is fully isolated: calendar IDs, event IDs, calendars, and
    availability/search state are scoped to the selected account.
    """

    model_config = {"extra": "forbid"}

    accounts: dict[NonEmptyStateString, CalendarState] = Field(default_factory=dict)


GoogleCalendarState = CalendarState | CalendarAccountsState


class SearchEventsResponse(TypedDict, total=False):
    status: str
    message: str
    events: list[dict[str, Any]]
    count: int
    warnings: list[str]
    skipped_unparseable: list[str]
