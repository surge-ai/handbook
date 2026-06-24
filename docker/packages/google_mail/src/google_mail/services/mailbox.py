"""Mailbox service for managing mock email operations."""

from __future__ import annotations

import json
import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from google_mail.models import (
    INCOMPLETE_RECIPIENT_FOLDERS,
    SYSTEM_FOLDERS,
    Attachment,
    Contact,
    ContactGroup,
    Email,
    Folder,
    MailboxData,
)

_logger = logging.getLogger(__name__)


def _paginate[T](items: list[T], page: int, page_size: int) -> tuple[list[T], int]:
    """Apply pagination to a list of items."""
    total = len(items)
    start = (page - 1) * page_size
    return items[start : start + page_size], total


def normalize_search_pagination(page: int, page_size: int) -> tuple[int, int, list[str]]:
    """Normalize search pagination inputs and report any changes."""
    warnings: list[str] = []
    normalized_page = page
    normalized_page_size = page_size

    if normalized_page < 1:
        warnings.append("page must be at least 1; using 1.")
        normalized_page = 1
    if normalized_page_size < 0:
        warnings.append("page_size must be non-negative; using 0.")
        normalized_page_size = 0
    elif normalized_page_size > 100:
        warnings.append("page_size exceeds the maximum of 100; using 100.")
        normalized_page_size = 100

    return normalized_page, normalized_page_size, warnings


# ---------------------------------------------------------------------------
# Gmail-style search parser
# ---------------------------------------------------------------------------

_ADDRESS_OPS = {"from", "to", "cc", "bcc"}
_DATE_OPS = {"before", "after", "newer_than", "older_than"}
_TEXT_OPS = {"subject", "filename"}
_STATE_OPS = {"has", "is"}
_FOLDER_OPS = {"in", "label"}
_KNOWN_OPS = _ADDRESS_OPS | _DATE_OPS | _TEXT_OPS | _STATE_OPS | _FOLDER_OPS
_VALID_HAS_VALUES = {"attachment", "attachments"}
_VALID_IS_VALUES = {"unread", "read", "important"}

# Duration suffix → timedelta conversion for newer_than/older_than.
_DURATION_UNITS = {
    "d": timedelta(days=1),
    "m": timedelta(days=30),
    "y": timedelta(days=365),
}

# Raw tokenizer: operator:"quoted values", quoted segments, or non-whitespace runs.
_RAW_TOKEN_RE = re.compile(r'(?P<operator_phrase>-?[A-Za-z_]+:"[^"]*")|"(?P<phrase>[^"]*)"|(?P<token>\S+)')


@dataclass
class _Token:
    """A single search alternative.

    ``field`` identifies an operator (`from`, `to`, `before`, ...) or is
    ``None`` for bare words matched against the full haystack.
    """

    field: str | None
    value: str
    is_phrase: bool = False
    negated: bool = False


@dataclass
class _Clause:
    """AND entry: passes when at least one alternative matches the email."""

    alternatives: list[_Token] = field(default_factory=list)


@dataclass(frozen=True)
class BatchEmailError:
    """A per-email error from a batch email mutation."""

    email_id: str
    error: str

    def to_dict(self) -> dict[str, str]:
        """Return the error in the tool response shape."""
        return {"email_id": self.email_id, "error": self.error}


@dataclass(frozen=True)
class BatchEmailMutationResult:
    """Result from a batch email mutation."""

    succeeded_ids: list[str]
    errors: list[BatchEmailError]


def _parse_date(raw: str) -> datetime | None:
    """Parse Gmail-style date strings (YYYY/MM/DD or YYYY-MM-DD) as UTC dates."""
    for fmt in ("%Y/%m/%d", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, fmt).replace(tzinfo=UTC)
        except ValueError:
            continue
    return None


def _parse_duration(raw: str) -> timedelta | None:
    """Parse durations like '7d', '1m', '2y' into a timedelta."""
    if len(raw) < 2:
        return None
    unit = raw[-1]
    if unit not in _DURATION_UNITS:
        return None
    try:
        amount = int(raw[:-1])
    except ValueError:
        return None
    if amount < 0:
        return None
    return _DURATION_UNITS[unit] * amount


def _parse_search_query(query: str) -> list[_Clause]:
    """Parse a Gmail-style query into a list of AND clauses.

    Supports operators: from/to/cc/bcc, subject, filename, has, is, in, label,
    before/after, newer_than/older_than. `OR` (uppercase) between raw tokens
    folds neighbors into a single OR-clause. `-` prefix negates a token.
    Double-quoted segments are phrase tokens. Anything else is a bare word
    matched on the full haystack.
    """
    raw_items: list[tuple[str, bool]] = []
    for m in _RAW_TOKEN_RE.finditer(query):
        if m.group("phrase") is not None:
            # The regex matched the quoted branch; "phrase" is the inner text.
            raw_items.append((m.group("phrase"), True))
        else:
            raw_items.append((m.group("operator_phrase") or m.group("token"), False))

    # Turn raw items into typed tokens; collect the "OR" markers for later merging.
    tokens: list[_Token | str] = []  # interleaved _Token objects and "OR" markers
    for raw, was_quoted in raw_items:
        if raw == "OR" and not was_quoted:
            tokens.append("OR")
            continue

        negated = False
        text = raw
        if not was_quoted:
            # Strip a single leading `-` as negation. Lone `-` is dropped.
            if text.startswith("-") and len(text) > 1:
                negated = True
                text = text[1:]
            elif text == "-":
                continue

        # Operator? Only if not phrase-sourced (from:"alice" still works because
        # the quote is the value, not the prefix).
        tok = _build_token(text, was_quoted=was_quoted, negated=negated)
        if tok is not None:
            tokens.append(tok)

    # Fold OR markers: merge the previous token with the next into one clause.
    clauses: list[_Clause] = []
    i = 0
    while i < len(tokens):
        item = tokens[i]
        if isinstance(item, str):
            # Dangling OR — skip as a no-op.
            i += 1
            continue

        alternatives: list[_Token] = [item]
        # Look ahead for "OR" <token> pairs.
        while i + 2 < len(tokens) and tokens[i + 1] == "OR":
            next_item = tokens[i + 2]
            if not isinstance(next_item, _Token):
                break
            alternatives.append(next_item)
            i += 2
        clauses.append(_Clause(alternatives=alternatives))
        i += 1
    return clauses


def search_query_warnings(query: str) -> list[str]:
    """Return warnings for Gmail-style syntax this mock will ignore or fail softly."""
    warnings: list[str] = []
    seen: set[str] = set()

    def add(message: str) -> None:
        if message not in seen:
            warnings.append(message)
            seen.add(message)

    if "(" in query or ")" in query:
        add("Parenthesized boolean grouping is not supported; OR only joins adjacent terms.")

    for m in _RAW_TOKEN_RE.finditer(query):
        raw = m.group("operator_phrase") or m.group("token")
        if raw is None:
            continue
        text = raw[1:] if raw.startswith("-") and len(raw) > 1 else raw
        if ":" not in text:
            continue
        prefix, _, value = text.partition(":")
        op = prefix.lower()
        normalized_value = value.strip().strip('"').lower()
        if op not in _KNOWN_OPS:
            add(f"Unsupported Gmail search operator '{prefix}:'; it will be treated as a text token.")
            continue
        if op == "has" and normalized_value not in _VALID_HAS_VALUES:
            add(f"Unsupported has: value '{normalized_value}'. Supported values: attachment.")
        elif op == "is" and normalized_value not in _VALID_IS_VALUES:
            add(f"Unsupported is: value '{normalized_value}'. Supported values: unread, read, important.")
        elif op in {"before", "after"} and _parse_date(normalized_value) is None:
            add(f"Invalid {op}: date '{normalized_value}'. Use YYYY/MM/DD or YYYY-MM-DD.")
        elif op in {"newer_than", "older_than"} and _parse_duration(normalized_value) is None:
            add(f"Invalid {op}: duration '{normalized_value}'. Use a non-negative number followed by d, m, or y.")

    return warnings


def _build_token(text: str, *, was_quoted: bool, negated: bool) -> _Token | None:
    """Promote a raw text fragment into a typed `_Token`.

    Quoted fragments can't carry an operator prefix (the `"` started the value).
    An unquoted fragment with `prefix:value` where `prefix.lower()` is in
    `_KNOWN_OPS` becomes a field-typed token; everything else is a bare-word
    token. Empty values produce an un-matchable marker so the clause silently
    contributes zero hits rather than crashing.
    """
    if was_quoted:
        return _Token(field=None, value=text.lower(), is_phrase=True, negated=negated)

    # Look for prefix:value with a known operator.
    if ":" in text:
        prefix, _, value = text.partition(":")
        op = prefix.lower()
        if op in _KNOWN_OPS:
            # Unwrap a quoted value: from:"alice smith" → value="alice smith"
            is_phrase = False
            if len(value) >= 2 and value[0] == '"' and value[-1] == '"':
                value = value[1:-1]
                is_phrase = True
            return _Token(field=op, value=value.lower(), is_phrase=is_phrase, negated=negated)

    return _Token(field=None, value=text.lower(), is_phrase=False, negated=negated)


def _email_haystack(email: Email) -> str:
    """Full-text haystack joined with newlines so phrase matches don't span fields."""
    return "\n".join(
        [
            email.subject or "",
            email.body_text or "",
            email.from_addr or "",
            email.to_addr or "",
        ]
    ).lower()


def _email_labels(email: Email) -> set[str]:
    return {str(label).strip().lower() for label in email.labels if str(label).strip()}


def _email_date_utc(email: Email) -> datetime:
    """Return email.date as tz-aware UTC (naive dates interpreted as UTC)."""
    d = email.date
    return d.replace(tzinfo=UTC) if d.tzinfo is None else d.astimezone(UTC)


def _match_alternative(tok: _Token, email: Email, haystack: str, now: datetime) -> bool:
    """Evaluate a single OR-alternative against an email (pre-negation)."""
    # Empty operator value → always false (clause silently fails).
    if tok.field is not None and not tok.value:
        return False

    if tok.field is None:
        return tok.value in haystack

    if tok.field == "from":
        return tok.value in (email.from_addr or "").lower()
    if tok.field == "to":
        return tok.value in (email.to_addr or "").lower()
    if tok.field == "cc":
        return tok.value in (email.cc_addr or "").lower()
    if tok.field == "bcc":
        return tok.value in (email.bcc_addr or "").lower()
    if tok.field == "subject":
        return tok.value in (email.subject or "").lower()
    if tok.field == "filename":
        return any(tok.value in attachment.filename.lower() for attachment in email.attachments)
    if tok.field == "has":
        if tok.value in {"attachment", "attachments"}:
            return bool(email.attachments)
        return False
    if tok.field == "is":
        if tok.value == "unread":
            return not email.is_read
        if tok.value == "read":
            return email.is_read
        if tok.value == "important":
            return email.is_important
        return False
    if tok.field == "in":
        return tok.value == (email.folder or "").lower()
    if tok.field == "label":
        return tok.value == (email.folder or "").lower() or tok.value in _email_labels(email)

    # Date operators — bad values produce False, never a crash.
    email_dt = _email_date_utc(email)
    if tok.field == "before":
        target = _parse_date(tok.value)
        return target is not None and email_dt < target
    if tok.field == "after":
        target = _parse_date(tok.value)
        return target is not None and email_dt >= target
    if tok.field == "newer_than":
        delta = _parse_duration(tok.value)
        return delta is not None and email_dt >= now - delta
    if tok.field == "older_than":
        delta = _parse_duration(tok.value)
        return delta is not None and email_dt < now - delta

    # Unknown operator — should be unreachable because unknown prefixes are
    # preserved as bare-word tokens upstream; be defensive anyway.
    return False


def _email_matches(clauses: list[_Clause], email: Email, now: datetime) -> bool:
    """An email matches when every AND clause has at least one passing alternative."""
    haystack = _email_haystack(email)
    for clause in clauses:
        clause_passed = False
        for alt in clause.alternatives:
            matched = _match_alternative(alt, email, haystack, now)
            if alt.negated:
                matched = not matched
            if matched:
                clause_passed = True
                break
        if not clause_passed:
            return False
    return True


class MailboxError(Exception):
    """Base exception for mailbox operations."""


class RecipientNotFoundError(MailboxError):
    """Raised when a recipient is not in the contacts list."""

    def __init__(self, recipient: str) -> None:
        self.recipient = recipient
        super().__init__(f"Recipient not found: {recipient}")


class EmailNotFoundError(MailboxError):
    """Raised when an email is not found."""

    def __init__(self, email_id: str) -> None:
        self.email_id = email_id
        super().__init__(f"Email not found: {email_id}")


class DraftNotFoundError(MailboxError):
    """Raised when a draft is not found."""

    def __init__(self, draft_id: str) -> None:
        self.draft_id = draft_id
        super().__init__(f"Draft not found: {draft_id}")


class FolderNotFoundError(MailboxError):
    """Raised when a folder is not found."""

    def __init__(self, folder_name: str) -> None:
        self.folder_name = folder_name
        super().__init__(f"Folder not found: {folder_name}")


class FolderExistsError(MailboxError):
    """Raised when trying to create a folder that already exists."""

    def __init__(self, folder_name: str) -> None:
        self.folder_name = folder_name
        super().__init__(f"Folder already exists: {folder_name}")


class SystemFolderError(MailboxError):
    """Raised when trying to modify a system folder."""

    def __init__(self, folder_name: str) -> None:
        self.folder_name = folder_name
        super().__init__(f"Cannot modify system folder: {folder_name}")


class ScheduledFolderError(MailboxError):
    """Raised when trying to move unscheduled mail into the Scheduled folder."""

    def __init__(self) -> None:
        super().__init__("Cannot move email into Scheduled; use schedule_email to create scheduled mail")


class RecipientRequiredError(MailboxError):
    """Raised when trying to move incomplete mail into an active folder."""

    def __init__(self, folder_name: str) -> None:
        self.folder_name = folder_name
        super().__init__(f"Cannot move email without recipients to folder: {folder_name}")


class AttachmentNotFoundError(MailboxError):
    """Raised when an attachment is not found."""

    def __init__(self, email_id: str, filename: str) -> None:
        self.email_id = email_id
        self.filename = filename
        super().__init__(f"Attachment '{filename}' not found in email {email_id}")


class ContactNotFoundError(MailboxError):
    """Raised when a contact is not found."""

    def __init__(self, email: str) -> None:
        self.email = email
        super().__init__(f"Contact not found: {email}")


class ContactExistsError(MailboxError):
    """Raised when trying to add a contact that already exists."""

    def __init__(self, email: str) -> None:
        self.email = email
        super().__init__(f"Contact already exists: {email}")


class ContactInUseError(MailboxError):
    """Raised when trying to delete a contact that is still used by a group."""

    def __init__(self, email: str, groups: list[str]) -> None:
        self.email = email
        self.groups = groups
        super().__init__(f"Contact {email} is a member of groups: {', '.join(groups)}")


class GroupNotFoundError(MailboxError):
    """Raised when a group is not found."""

    def __init__(self, email: str) -> None:
        self.email = email
        super().__init__(f"Group not found: {email}")


class GroupExistsError(MailboxError):
    """Raised when trying to add a group that already exists."""

    def __init__(self, email: str) -> None:
        self.email = email
        super().__init__(f"Group already exists: {email}")


class MailboxService:
    """Service for managing mock mailbox operations."""

    def __init__(self, data_path: Path) -> None:
        """Initialize the mailbox service.

        Args:
            data_path: Path to the JSON data file.
        """
        self._data_path = data_path
        self._data: MailboxData | None = None

    @property
    def data(self) -> MailboxData:
        """Get the mailbox data, raising if not loaded."""
        if self._data is None:
            raise MailboxError("Mailbox data not loaded")
        return self._data

    def load(self) -> None:
        """Load mailbox data from the JSON file."""
        _logger.info("Loading mailbox data from %s", self._data_path)
        with open(self._data_path) as f:
            raw = json.load(f)
        self.from_json(raw, persist=False)

    def to_json(self) -> dict[str, Any]:
        """Return the mailbox state as a JSON-native dict. Round-trips with from_json."""
        return self.data.model_dump(mode="json")

    def from_json(self, data: dict[str, Any], persist: bool = True) -> None:
        """Full-replace the mailbox state from a JSON-native dict."""
        self._data = MailboxData.model_validate(data)
        _logger.info(
            "Loaded mailbox for %s with %d emails",
            self._data.mailbox.email,
            len(self._data.emails),
        )
        if persist:
            self._save()

    def _save(self) -> None:
        """Persist current state to the JSON file."""
        _logger.debug("Saving mailbox data to %s", self._data_path)
        self._data = MailboxData.model_validate(self.data.model_dump(mode="json"))
        with open(self._data_path, "w") as f:
            json.dump(self.data.model_dump(mode="json"), f, indent=2, default=str)

    def _restore(self, snapshot: dict[str, Any]) -> None:
        """Restore in-memory state from a previously validated snapshot."""
        self._data = MailboxData.model_validate(snapshot)

    def _save_with_rollback(self, snapshot: dict[str, Any]) -> None:
        """Persist current state, restoring the snapshot if validation or writing fails."""
        try:
            self._save()
        except Exception:
            self._restore(snapshot)
            raise

    def _generate_email_id(self) -> str:
        """Generate a new unique email ID."""
        email_id = str(self.data.next_email_id)
        self.data.next_email_id += 1
        return email_id

    def _generate_message_id(self) -> str:
        """Generate an RFC 2822 Message-ID."""
        return f"<{uuid.uuid4()}@mail-mcp.local>"

    def _validate_recipients(self, addresses: str) -> tuple[list[str], list[str]]:
        """Validate recipients and return (valid, invalid) lists.

        Args:
            addresses: Comma-separated email addresses.

        Returns:
            Tuple of (valid_addresses, invalid_addresses).
        """
        valid: list[str] = []
        invalid: list[str] = []
        for raw_addr in addresses.split(","):
            addr = raw_addr.strip()
            if not addr:
                continue
            if self.data.is_valid_recipient(addr):
                valid.append(addr)
            else:
                invalid.append(addr)
        return valid, invalid

    # Email operations

    def get_emails(self, folder: str | None = None, page: int = 1, page_size: int = 20) -> tuple[list[Email], int]:
        """Get emails, optionally filtered by folder.

        Args:
            folder: Folder to filter by (None for all).
            page: Page number (1-indexed).
            page_size: Number of emails per page.

        Returns:
            Tuple of (emails, total_count).
        """
        emails = self.data.emails
        if folder:
            if folder not in self.data.get_all_folder_names():
                raise FolderNotFoundError(folder)
            emails = [e for e in emails if e.folder == folder]

        # Sort by date descending (newest first)
        emails = sorted(emails, key=lambda e: e.date, reverse=True)

        return _paginate(emails, page, page_size)

    def get_email(self, email_id: str) -> Email:
        """Get an email by ID.

        Args:
            email_id: The email ID.

        Returns:
            The email.

        Raises:
            EmailNotFoundError: If email not found.
        """
        for email in self.data.emails:
            if email.email_id == email_id:
                return email
        raise EmailNotFoundError(email_id)

    def read_email(self, email_id: str) -> Email:
        """Get an email and mark it as read.

        Args:
            email_id: The email ID.

        Returns:
            The email (now marked as read).
        """
        email = self.get_email(email_id)
        if not email.is_read:
            snapshot = self.to_json()
            email.is_read = True
            self._save_with_rollback(snapshot)
        return email

    def search_emails(
        self,
        query: str,
        folder: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[Email], int]:
        """Search emails with Gmail-style operator support.

        Accepts:
          - bare words ANDed together (word-AND)
          - `"quoted phrases"` — exact adjacency
          - `from:` / `to:` / `cc:` / `bcc:` — substring match on address field
          - `subject:` — substring match on subject
          - `filename:` — substring match on attachment filename
          - `has:attachment` — messages with attachments
          - `is:unread` / `is:read` / `is:important` — state filters
          - `in:` — exact folder match (e.g. `in:sent`)
          - `label:` — exact label match when labels exist; otherwise folder-style match
          - `before:YYYY/MM/DD`, `after:YYYY/MM/DD` — date range (before is strict,
            after is inclusive)
          - `newer_than:<N>d|m|y`, `older_than:<N>d|m|y` — relative duration
          - `OR` (uppercase) between adjacent terms → OR-alternatives
          - `-token` prefix → negation (e.g. `-spam`, `-from:bob`)

        Parenthesized boolean grouping is not supported; `OR` only joins
        adjacent alternatives.

        Args:
            query: Search query.
            folder: Optional folder to limit search.
            page: Page number (1-indexed).
            page_size: Results per page.

        Returns:
            Tuple of (matching_emails, total_count).
        """
        page, page_size, _ = normalize_search_pagination(page, page_size)
        clauses = _parse_search_query(query)
        if not clauses:
            # Empty/whitespace-only query — return nothing, same as before.
            return _paginate([], page, page_size)

        now = datetime.now(UTC)
        results: list[Email] = []

        for email in self.data.emails:
            if folder and email.folder != folder:
                continue
            if _email_matches(clauses, email, now):
                results.append(email)

        # Sort by date descending
        results = sorted(results, key=lambda e: e.date, reverse=True)

        return _paginate(results, page, page_size)

    def send_email(
        self,
        to: str,
        subject: str,
        body: str,
        html_body: str | None = None,
        cc: str | None = None,
        bcc: str | None = None,
        attachments: list[Attachment] | None = None,
        in_reply_to: str | None = None,
    ) -> Email:
        """Send an email.

        Args:
            to: Recipient(s), comma-separated.
            subject: Email subject.
            body: Plain text body.
            html_body: Optional HTML body.
            cc: Optional CC recipients.
            bcc: Optional BCC recipients.
            attachments: Optional list of attachments.
            in_reply_to: Optional Message-ID of email being replied to.

        Returns:
            The sent email.

        Raises:
            RecipientNotFoundError: If any recipient is invalid.
        """
        # Validate all recipients
        all_recipients = to
        if cc:
            all_recipients += "," + cc
        if bcc:
            all_recipients += "," + bcc

        valid, invalid = self._validate_recipients(all_recipients)
        if invalid:
            raise RecipientNotFoundError(invalid[0])
        if not valid:
            raise RecipientRequiredError("Sent")

        snapshot = self.to_json()
        try:
            now = datetime.now(UTC)
            email = Email(
                email_id=self._generate_email_id(),
                folder="Sent",
                subject=subject,
                from_addr=self.data.mailbox.email,
                to_addr=to,
                cc_addr=cc,
                bcc_addr=bcc,
                date=now,
                message_id=self._generate_message_id(),
                in_reply_to=in_reply_to,
                body_text=body,
                body_html=html_body,
                is_read=True,
                attachments=attachments or [],
            )

            self.data.emails.append(email)

            # Create an INBOX copy if:
            #  - the mailbox owner sent to themselves (direct self-send), or
            #  - any recipient is a group the owner is a member of.
            # Check to + cc so self-CC also lands in INBOX. BCC is not copied so the
            # BCC semantics stay honest.
            mailbox_email = self.data.mailbox.email.lower()
            all_recipient_segments: list[str] = []
            all_recipient_segments.extend(to.split(","))
            if cc:
                all_recipient_segments.extend(cc.split(","))

            inbox_copy_reason: str | None = None
            for raw_addr in all_recipient_segments:
                addr = raw_addr.strip()
                if not addr:
                    continue
                if addr.lower() == mailbox_email:
                    inbox_copy_reason = f"self-send to {addr}"
                    break
                group = self.data.get_group_by_email(addr)
                if group and self.data.is_mailbox_member_of_group(group):
                    inbox_copy_reason = f"group {group.email} membership"
                    break

            if inbox_copy_reason is not None:
                inbox_copy = Email(
                    email_id=self._generate_email_id(),
                    folder="INBOX",
                    subject=email.subject,
                    from_addr=email.from_addr,
                    to_addr=email.to_addr,
                    cc_addr=email.cc_addr,
                    bcc_addr=None,  # BCC not visible
                    date=email.date,
                    message_id=email.message_id,
                    in_reply_to=email.in_reply_to,
                    body_text=email.body_text,
                    body_html=email.body_html,
                    is_read=False,  # Unread in inbox
                    attachments=email.attachments,
                )
                self.data.emails.append(inbox_copy)
                _logger.info("Created inbox copy: %s", inbox_copy_reason)

            self._save_with_rollback(snapshot)
        except Exception:
            self._restore(snapshot)
            raise
        _logger.info("Sent email %s to %s", email.email_id, to)
        return email

    def reply_email(
        self,
        email_id: str,
        body: str,
        html_body: str | None = None,
        reply_all: bool = False,
    ) -> Email:
        """Reply to an email.

        Args:
            email_id: ID of the email to reply to.
            body: Reply body text.
            html_body: Optional HTML body.
            reply_all: If True, reply to all recipients.

        Returns:
            The reply email.
        """
        original = self.get_email(email_id)

        # Determine recipients
        to = original.from_addr
        cc = None
        if reply_all and original.cc_addr:
            # Add original recipients except self
            other_recipients = [
                addr.strip()
                for addr in (original.to_addr + "," + original.cc_addr).split(",")
                if addr.strip().lower() != self.data.mailbox.email.lower()
                and addr.strip().lower() != original.from_addr.lower()
            ]
            if other_recipients:
                cc = ", ".join(other_recipients)

        subject = original.subject
        if not subject.lower().startswith("re:"):
            subject = f"Re: {subject}"

        # Build body with quoted original
        formatted_date = original.date.strftime("%Y-%m-%d %H:%M")
        quoted_body = f"{body}\n\n--- Original Message ---\n"
        quoted_body += f"From: {original.from_addr}\n"
        quoted_body += f"Date: {formatted_date}\n"
        quoted_body += f"Subject: {original.subject}\n\n"
        quoted_body += original.body_text

        return self.send_email(
            to=to,
            subject=subject,
            body=quoted_body,
            html_body=html_body,
            cc=cc,
            in_reply_to=original.message_id,
        )

    def forward_email(
        self,
        email_id: str,
        to: str,
        body: str | None = None,
    ) -> Email:
        """Forward an email.

        Args:
            email_id: ID of the email to forward.
            to: Recipient(s) to forward to, comma-separated.
            body: Optional additional message.

        Returns:
            The forwarded email.
        """
        original = self.get_email(email_id)

        subject = original.subject
        if not subject.lower().startswith("fwd:"):
            subject = f"Fwd: {subject}"

        forward_body = body or ""
        forward_body += "\n\n---------- Forwarded message ---------\n"
        forward_body += f"From: {original.from_addr}\n"
        forward_body += f"Date: {original.date}\n"
        forward_body += f"Subject: {original.subject}\n"
        forward_body += f"To: {original.to_addr}\n"
        forward_body += f"\n{original.body_text}"

        return self.send_email(
            to=to,
            subject=subject,
            body=forward_body,
            attachments=original.attachments,
        )

    def delete_email(self, email_id: str, permanent: bool = False) -> None:
        """Delete an email.

        Args:
            email_id: ID of the email to delete.
            permanent: If True, permanently delete. If False, move to Trash.
        """
        snapshot = self.to_json()
        try:
            self._delete_email_without_save(email_id, permanent=permanent)
            self._save_with_rollback(snapshot)
        except Exception:
            self._restore(snapshot)
            raise

    def _delete_email_without_save(self, email_id: str, permanent: bool = False) -> None:
        """Delete an email without persisting the mutation."""
        email = self.get_email(email_id)

        if permanent or email.folder == "Trash":
            self.data.emails = [e for e in self.data.emails if e.email_id != email_id]
            _logger.info("Permanently deleted email %s", email_id)
        else:
            email.folder = "Trash"
            email.scheduled_time = None
            _logger.info("Moved email %s to Trash", email_id)

    def delete_emails(self, email_ids: list[str], permanent: bool = False) -> BatchEmailMutationResult:
        """Delete multiple emails and persist once after all successful mutations."""
        snapshot = self.to_json()
        deleted: list[str] = []
        errors: list[BatchEmailError] = []

        for email_id in email_ids:
            try:
                self._delete_email_without_save(email_id, permanent=permanent)
                deleted.append(email_id)
            except EmailNotFoundError as exc:
                errors.append(BatchEmailError(email_id=email_id, error=str(exc)))

        if deleted:
            self._save_with_rollback(snapshot)

        return BatchEmailMutationResult(succeeded_ids=deleted, errors=errors)

    def move_email(self, email_id: str, target_folder: str) -> None:
        """Move an email to a different folder.

        Args:
            email_id: ID of the email to move.
            target_folder: Name of the target folder.
        """
        if target_folder not in self.data.get_all_folder_names():
            raise FolderNotFoundError(target_folder)

        snapshot = self.to_json()
        try:
            self._move_email_without_save(email_id, target_folder)
            self._save_with_rollback(snapshot)
        except Exception:
            self._restore(snapshot)
            raise

    def _move_email_without_save(self, email_id: str, target_folder: str) -> None:
        """Move an email without persisting the mutation."""
        email = self.get_email(email_id)
        if target_folder == "Scheduled" and email.folder != "Scheduled":
            raise ScheduledFolderError()
        if target_folder not in INCOMPLETE_RECIPIENT_FOLDERS and not any(
            [email.to_addr.strip(), email.cc_addr, email.bcc_addr]
        ):
            raise RecipientRequiredError(target_folder)
        if email.folder == "Scheduled" and target_folder != "Scheduled":
            email.scheduled_time = None
        email.folder = target_folder
        _logger.info("Moved email %s to %s", email_id, target_folder)

    def move_emails(self, email_ids: list[str], target_folder: str) -> BatchEmailMutationResult:
        """Move multiple emails and persist once after all successful mutations."""
        if target_folder not in self.data.get_all_folder_names():
            error = str(FolderNotFoundError(target_folder))
            return BatchEmailMutationResult(
                succeeded_ids=[],
                errors=[BatchEmailError(email_id=email_id, error=error) for email_id in email_ids],
            )

        snapshot = self.to_json()
        moved: list[str] = []
        errors: list[BatchEmailError] = []
        for email_id in email_ids:
            try:
                self._move_email_without_save(email_id, target_folder)
                moved.append(email_id)
            except (EmailNotFoundError, RecipientRequiredError, ScheduledFolderError) as exc:
                errors.append(BatchEmailError(email_id=email_id, error=str(exc)))

        if moved:
            self._save_with_rollback(snapshot)

        return BatchEmailMutationResult(succeeded_ids=moved, errors=errors)

    def mark_emails(
        self,
        email_ids: list[str],
        is_read: bool | None = None,
        is_important: bool | None = None,
    ) -> BatchEmailMutationResult:
        """Mark emails with read/important status.

        Args:
            email_ids: List of email IDs to mark.
            is_read: Set read status (None to leave unchanged).
            is_important: Set important status (None to leave unchanged).

        Returns:
            IDs marked and per-email errors.
        """
        if is_read is None and is_important is None:
            raise ValueError("At least one of is_read or is_important must be provided")

        snapshot = self.to_json()
        marked: list[str] = []
        errors: list[BatchEmailError] = []
        for email_id in email_ids:
            try:
                self._mark_email_without_save(email_id, is_read=is_read, is_important=is_important)
                marked.append(email_id)
            except EmailNotFoundError as exc:
                errors.append(BatchEmailError(email_id=email_id, error=str(exc)))

        if marked:
            self._save_with_rollback(snapshot)
            _logger.info("Marked %d emails", len(marked))

        return BatchEmailMutationResult(succeeded_ids=marked, errors=errors)

    def _mark_email_without_save(
        self,
        email_id: str,
        is_read: bool | None = None,
        is_important: bool | None = None,
    ) -> None:
        """Mark one email without persisting the mutation."""
        email = self.get_email(email_id)
        if is_read is not None:
            email.is_read = is_read
        if is_important is not None:
            email.is_important = is_important

    # Folder operations

    def get_folders(self) -> list[dict[str, Any]]:
        """Get all folders with message counts.

        Returns:
            List of folder info dicts with name, total, unread.
        """
        folder_names = self.data.get_all_folder_names()
        result: list[dict[str, Any]] = []

        for name in sorted(folder_names):
            emails_in_folder = [e for e in self.data.emails if e.folder == name]
            unread = sum(1 for e in emails_in_folder if not e.is_read)
            result.append(
                {
                    "name": name,
                    "total": len(emails_in_folder),
                    "unread": unread,
                    "is_system": name in SYSTEM_FOLDERS,
                }
            )

        return result

    def create_folder(self, folder_name: str) -> None:
        """Create a new custom folder.

        Args:
            folder_name: Name of the folder to create.

        Raises:
            FolderExistsError: If folder already exists.
        """
        if folder_name in self.data.get_all_folder_names():
            raise FolderExistsError(folder_name)

        folder = Folder(name=folder_name)
        snapshot = self.to_json()
        self.data.folders.append(folder)
        self._save_with_rollback(snapshot)
        _logger.info("Created folder %s", folder_name)

    def delete_folder(self, folder_name: str) -> None:
        """Delete a custom folder.

        Args:
            folder_name: Name of the folder to delete.

        Raises:
            SystemFolderError: If trying to delete a system folder.
            FolderNotFoundError: If folder doesn't exist.
        """
        if folder_name in SYSTEM_FOLDERS:
            raise SystemFolderError(folder_name)

        for i, folder in enumerate(self.data.folders):
            if folder.name == folder_name:
                snapshot = self.to_json()
                # Move emails to INBOX
                for email in self.data.emails:
                    if email.folder == folder_name:
                        email.folder = "INBOX"
                del self.data.folders[i]
                self._save_with_rollback(snapshot)
                _logger.info("Deleted folder %s", folder_name)
                return

        raise FolderNotFoundError(folder_name)

    def get_unread_count(self, folder: str | None = None) -> dict[str, int]:
        """Get unread count for folder(s).

        Args:
            folder: Specific folder, or None for all.

        Returns:
            Dict mapping folder name to unread count.
        """
        result: dict[str, int] = {}

        if folder:
            if folder not in self.data.get_all_folder_names():
                raise FolderNotFoundError(folder)
            unread = sum(1 for e in self.data.emails if e.folder == folder and not e.is_read)
            result[folder] = unread
        else:
            for name in self.data.get_all_folder_names():
                unread = sum(1 for e in self.data.emails if e.folder == name and not e.is_read)
                result[name] = unread

        return result

    def get_mailbox_stats(self) -> dict[str, Any]:
        """Get overall mailbox statistics.

        Returns:
            Dict with various statistics.
        """
        total_emails = len(self.data.emails)
        total_unread = sum(1 for e in self.data.emails if not e.is_read)
        total_important = sum(1 for e in self.data.emails if e.is_important)
        total_drafts = sum(1 for e in self.data.emails if e.folder == "Drafts")
        total_contacts = len(self.data.contacts)

        folder_stats = self.get_folders()

        return {
            "mailbox": {
                "email": self.data.mailbox.email,
                "name": self.data.mailbox.name,
            },
            "total_emails": total_emails,
            "total_unread": total_unread,
            "total_important": total_important,
            "total_drafts": total_drafts,
            "total_contacts": total_contacts,
            "folders": folder_stats,
        }

    # Draft operations (drafts are emails in the "Drafts" folder)

    def save_draft(
        self,
        subject: str = "",
        body: str = "",
        html_body: str | None = None,
        to: str | None = None,
        cc: str | None = None,
        bcc: str | None = None,
    ) -> Email:
        """Save a new draft.

        Args:
            subject: Draft subject.
            body: Draft body.
            html_body: Optional HTML body.
            to: Recipients.
            cc: CC recipients.
            bcc: BCC recipients.

        Returns:
            The created draft (as an Email in Drafts folder).
        """
        snapshot = self.to_json()
        try:
            now = datetime.now(UTC)
            email_id = self._generate_email_id()
            draft = Email(
                email_id=email_id,
                folder="Drafts",
                subject=subject,
                from_addr=self.data.mailbox.email,
                to_addr=to or "",
                cc_addr=cc,
                bcc_addr=bcc,
                date=now,
                message_id=self._generate_message_id(),
                body_text=body,
                body_html=html_body,
                is_read=True,
            )
            self.data.emails.append(draft)
            self._save_with_rollback(snapshot)
        except Exception:
            self._restore(snapshot)
            raise
        _logger.info("Saved draft %s", draft.email_id)
        return draft

    def get_drafts(self, page: int = 1, page_size: int = 20) -> tuple[list[Email], int]:
        """Get all drafts with pagination.

        Args:
            page: Page number (1-indexed).
            page_size: Drafts per page.

        Returns:
            Tuple of (drafts, total_count).
        """
        drafts = [e for e in self.data.emails if e.folder == "Drafts"]
        drafts = sorted(drafts, key=lambda d: d.date, reverse=True)
        return _paginate(drafts, page, page_size)

    def get_draft(self, draft_id: str) -> Email:
        """Get a draft by ID.

        Args:
            draft_id: The draft ID (email_id).

        Returns:
            The draft.

        Raises:
            DraftNotFoundError: If draft not found.
        """
        for email in self.data.emails:
            if email.folder == "Drafts" and email.email_id == draft_id:
                return email
        raise DraftNotFoundError(draft_id)

    def update_draft(
        self,
        draft_id: str,
        subject: str | None = None,
        body: str | None = None,
        html_body: str | None = None,
        to: str | None = None,
        cc: str | None = None,
        bcc: str | None = None,
    ) -> Email:
        """Update an existing draft.

        Args:
            draft_id: ID of the draft to update.
            subject: New subject (None to keep).
            body: New body (None to keep).
            html_body: New HTML body (None to keep).
            to: New recipients (None to keep).
            cc: New CC (None to keep).
            bcc: New BCC (None to keep).

        Returns:
            The updated draft.
        """
        draft = self.get_draft(draft_id)
        snapshot = self.to_json()

        try:
            if subject is not None:
                draft.subject = subject
            if body is not None:
                draft.body_text = body
            if html_body is not None:
                draft.body_html = html_body
            if to is not None:
                draft.to_addr = to
            if cc is not None:
                draft.cc_addr = cc
            if bcc is not None:
                draft.bcc_addr = bcc

            draft.date = datetime.now(UTC)
            self._save_with_rollback(snapshot)
        except Exception:
            self._restore(snapshot)
            raise
        _logger.info("Updated draft %s", draft_id)
        return draft

    def delete_draft(self, draft_id: str) -> None:
        """Delete a draft.

        Args:
            draft_id: ID of the draft to delete.

        Raises:
            DraftNotFoundError: If draft not found.
        """
        for i, email in enumerate(self.data.emails):
            if email.folder == "Drafts" and email.email_id == draft_id:
                snapshot = self.to_json()
                del self.data.emails[i]
                self._save_with_rollback(snapshot)
                _logger.info("Deleted draft %s", draft_id)
                return
        raise DraftNotFoundError(draft_id)

    # Scheduled email operations

    def schedule_email(
        self,
        to: str,
        subject: str,
        body: str,
        scheduled_time: datetime,
        html_body: str | None = None,
        cc: str | None = None,
        bcc: str | None = None,
        attachments: list[Attachment] | None = None,
    ) -> Email:
        """Schedule an email for later delivery.

        Args:
            to: Recipient(s), comma-separated.
            subject: Email subject.
            body: Plain text body.
            scheduled_time: When to send the email.
            html_body: Optional HTML body.
            cc: Optional CC recipients.
            bcc: Optional BCC recipients.
            attachments: Optional list of attachments.

        Returns:
            The scheduled email (in the Scheduled folder).

        Raises:
            RecipientNotFoundError: If any recipient is invalid.
        """
        # Validate all recipients
        all_recipients = to
        if cc:
            all_recipients += "," + cc
        if bcc:
            all_recipients += "," + bcc

        valid, invalid = self._validate_recipients(all_recipients)
        if invalid:
            raise RecipientNotFoundError(invalid[0])
        if not valid:
            raise RecipientRequiredError("Scheduled")

        snapshot = self.to_json()
        try:
            email = Email(
                email_id=self._generate_email_id(),
                folder="Scheduled",
                subject=subject,
                from_addr=self.data.mailbox.email,
                to_addr=to,
                cc_addr=cc,
                bcc_addr=bcc,
                date=datetime.now(UTC),
                message_id=self._generate_message_id(),
                body_text=body,
                body_html=html_body,
                is_read=True,
                attachments=attachments or [],
                scheduled_time=scheduled_time,
            )

            self.data.emails.append(email)
            self._save_with_rollback(snapshot)
        except Exception:
            self._restore(snapshot)
            raise
        _logger.info("Scheduled email %s for %s", email.email_id, scheduled_time)
        return email

    def get_scheduled_emails(self, page: int = 1, page_size: int = 20) -> tuple[list[Email], int]:
        """Get all scheduled emails with pagination.

        Args:
            page: Page number (1-indexed).
            page_size: Emails per page.

        Returns:
            Tuple of (scheduled_emails, total_count).
        """
        scheduled = [e for e in self.data.emails if e.folder == "Scheduled"]
        scheduled = sorted(scheduled, key=lambda e: e.scheduled_time or e.date)
        return _paginate(scheduled, page, page_size)

    def cancel_scheduled_email(self, email_id: str) -> None:
        """Cancel a scheduled email by removing it.

        Args:
            email_id: ID of the scheduled email to cancel.

        Raises:
            EmailNotFoundError: If email not found or not in Scheduled folder.
        """
        for i, email in enumerate(self.data.emails):
            if email.email_id == email_id and email.folder == "Scheduled":
                snapshot = self.to_json()
                del self.data.emails[i]
                self._save_with_rollback(snapshot)
                _logger.info("Cancelled scheduled email %s", email_id)
                return
        raise EmailNotFoundError(email_id)

    # Contact operations

    def get_contacts(self) -> list[Contact]:
        """Get all person contacts.

        Returns:
            List of contacts sorted by name.
        """
        return sorted(self.data.contacts, key=lambda c: c.name.lower())

    def get_groups(self) -> list[ContactGroup]:
        """Get all contact groups sorted by name."""
        return sorted(self.data.groups, key=lambda group: group.name.lower())

    def search_contacts(self, query: str) -> list[Contact]:
        """Search contacts by name or email (case-insensitive substring match).

        Args:
            query: Search string to match against name and email.

        Returns:
            List of matching contacts sorted by name.
        """
        query_lower = query.lower()
        results = [c for c in self.data.contacts if query_lower in c.name.lower() or query_lower in c.email.lower()]
        return sorted(results, key=lambda c: c.name.lower())

    def search_groups(self, query: str) -> list[ContactGroup]:
        """Search groups by name or email (case-insensitive substring match)."""
        query_lower = query.lower()
        results = [g for g in self.data.groups if query_lower in g.name.lower() or query_lower in g.email.lower()]
        return sorted(results, key=lambda group: group.name.lower())

    def add_contact(self, email: str, name: str) -> Contact:
        """Add a new contact to the address book.

        Args:
            email: Email address of the contact.
            name: Display name.

        Returns:
            The created contact.

        Raises:
            ContactExistsError: If a contact with this email already exists.
        """
        if self.data.get_contact_by_email(email) is not None or self.data.get_group_by_email(email) is not None:
            raise ContactExistsError(email)

        contact = Contact(email=email, name=name)
        snapshot = self.to_json()
        self.data.contacts.append(contact)
        self._save_with_rollback(snapshot)
        _logger.info("Added contact %s <%s>", name, email)
        return contact

    def edit_contact(
        self,
        email: str,
        name: str | None = None,
    ) -> Contact:
        """Update an existing contact.

        Args:
            email: Email address of the contact to update (lookup key).
            name: New display name (None to keep current).

        Returns:
            The updated contact.

        Raises:
            ContactNotFoundError: If no contact with this email exists.
        """
        contact = self.data.get_contact_by_email(email)
        if contact is None:
            raise ContactNotFoundError(email)

        snapshot = self.to_json()
        try:
            if name is not None:
                contact.name = name

            self._save_with_rollback(snapshot)
        except Exception:
            self._restore(snapshot)
            raise
        _logger.info("Updated contact <%s>", email)
        return contact

    def delete_contact(self, email: str) -> None:
        """Remove a contact from the address book.

        Args:
            email: Email address of the contact to remove.

        Raises:
            ContactNotFoundError: If no contact with this email exists.
        """
        containing_groups = [
            group.email
            for group in self.data.groups
            if any(member.lower() == email.lower() for member in group.members)
        ]
        if containing_groups:
            raise ContactInUseError(email, containing_groups)

        for i, contact in enumerate(self.data.contacts):
            if contact.email.lower() == email.lower():
                snapshot = self.to_json()
                del self.data.contacts[i]
                self._save_with_rollback(snapshot)
                _logger.info("Deleted contact <%s>", email)
                return
        raise ContactNotFoundError(email)

    def add_group(self, email: str, name: str, members: list[str]) -> ContactGroup:
        """Add a new addressable contact group."""
        if self.data.get_contact_by_email(email) is not None or self.data.get_group_by_email(email) is not None:
            raise GroupExistsError(email)

        group = ContactGroup(email=email, name=name, members=members)
        snapshot = self.to_json()
        self.data.groups.append(group)
        self._save_with_rollback(snapshot)
        _logger.info("Added group %s <%s>", name, email)
        return group

    def edit_group(
        self,
        email: str,
        name: str | None = None,
        members: list[str] | None = None,
    ) -> ContactGroup:
        """Update an existing contact group."""
        group = self.data.get_group_by_email(email)
        if group is None:
            raise GroupNotFoundError(email)

        snapshot = self.to_json()
        try:
            if name is not None:
                group.name = name
            if members is not None:
                group.members = members

            self._save_with_rollback(snapshot)
        except Exception:
            self._restore(snapshot)
            raise
        _logger.info("Updated group <%s>", email)
        return group

    def delete_group(self, email: str) -> None:
        """Remove a contact group."""
        for i, group in enumerate(self.data.groups):
            if group.email.lower() == email.lower():
                snapshot = self.to_json()
                del self.data.groups[i]
                self._save_with_rollback(snapshot)
                _logger.info("Deleted group <%s>", email)
                return
        raise GroupNotFoundError(email)

    # Attachment operations

    def get_attachment(self, email_id: str, filename: str) -> Attachment:
        """Get an attachment from an email.

        Args:
            email_id: ID of the email.
            filename: Name of the attachment file.

        Returns:
            The attachment.

        Raises:
            EmailNotFoundError: If email not found.
            AttachmentNotFoundError: If attachment not found.
        """
        email = self.get_email(email_id)
        for attachment in email.attachments:
            if attachment.filename == filename:
                return attachment
        raise AttachmentNotFoundError(email_id, filename)
