"""Collaboration tool handlers for comments, watchers, and attachments."""

from __future__ import annotations

import base64
import mimetypes
from typing import Any

from jira_mock.models import AccountId, Base64String, JiraAttachment, JiraComment, JiraWatches
from jira_mock.state import get_next_attachment_id, get_next_comment_id, get_state, save_state
from jira_mock.tools.common import IssueKey, _adf, _current_user, _dump, _now, _require_user, ensure_watches


def add_comment(issue_key: IssueKey, comment: str) -> dict[str, Any]:
    """Add a comment to a Jira issue."""
    state = get_state()
    if issue_key not in state.issues:
        raise ValueError(f"Issue {issue_key} not found")
    now = _now()
    data = {
        "id": str(get_next_comment_id()),
        "author": _dump(_current_user()),
        "body": _adf(comment),
        "created": now,
        "updated": now,
    }
    jira_comment = JiraComment.model_validate(data)
    state.comments.setdefault(issue_key, []).append(jira_comment)
    save_state()
    return _dump(jira_comment)


def add_watcher(issue_key: IssueKey, account_id: AccountId) -> dict[str, str]:
    """Add a watcher to a Jira issue."""
    issue = get_state().issues.get(issue_key)
    if issue is None:
        raise ValueError(f"Issue {issue_key} not found")
    watcher = _require_user(account_id)
    watches = ensure_watches(issue)
    if any(watcher.accountId == account_id for watcher in watches.watchers):
        return {"message": f"User {account_id} is already watching {issue_key}"}
    watches.watchers.append(watcher)
    watches.watchCount = len(watches.watchers)
    issue.fields.updated = _now()
    save_state()
    return {"message": f"Added watcher {account_id} to {issue_key}"}


def remove_watcher(issue_key: IssueKey, account_id: AccountId) -> dict[str, str]:
    """Remove a watcher from a Jira issue."""
    issue = get_state().issues.get(issue_key)
    if issue is None:
        raise ValueError(f"Issue {issue_key} not found")
    watches = ensure_watches(issue)
    before = len(watches.watchers)
    remaining_watchers = [watcher for watcher in watches.watchers if watcher.accountId != account_id]
    if len(remaining_watchers) == before:
        raise ValueError(f"User {account_id} is not watching {issue_key}")
    issue.fields.watches = JiraWatches(
        watchCount=len(remaining_watchers),
        isWatching=watches.isWatching,
        watchers=remaining_watchers,
    )
    issue.fields.updated = _now()
    save_state()
    return {"message": f"Removed watcher {account_id} from {issue_key}"}


def get_watchers(issue_key: IssueKey) -> dict[str, Any]:
    """Get all watchers of a Jira issue."""
    issue = get_state().issues.get(issue_key)
    if issue is None:
        raise ValueError(f"Issue {issue_key} not found")
    watches = ensure_watches(issue)
    return _dump(watches)


def add_attachment(
    issue_key: IssueKey, filename: str, content_base64: Base64String, mime_type: str | None = None
) -> dict[str, Any]:
    """Attach a base64-encoded file to a Jira issue."""
    issue = get_state().issues.get(issue_key)
    if issue is None:
        raise ValueError(f"Issue {issue_key} not found")
    try:
        size = len(base64.b64decode(content_base64, validate=True))
    except Exception as exc:
        raise ValueError("content_base64 must be valid base64") from exc
    mime = mime_type or mimetypes.guess_type(filename)[0] or "application/octet-stream"
    now = _now()
    attachment_id = str(get_next_attachment_id())
    attachment = JiraAttachment.model_validate(
        {
            "id": attachment_id,
            "filename": filename,
            "author": _dump(_current_user()),
            "created": now,
            "size": size,
            "mimeType": mime,
            "content": content_base64,
        }
    )
    issue.fields.attachment = issue.fields.attachment or []
    issue.fields.attachment.append(attachment)
    issue.fields.updated = now
    save_state()
    return _dump(attachment)


def get_attachments(issue_key: IssueKey) -> dict[str, Any]:
    """List all attachments on a Jira issue."""
    issue = get_state().issues.get(issue_key)
    if issue is None:
        raise ValueError(f"Issue {issue_key} not found")
    attachments = []
    for attachment in issue.fields.attachment or []:
        data = _dump(attachment)
        data.pop("content", None)
        attachments.append(data)
    return {"attachments": attachments, "total": len(attachments)}
