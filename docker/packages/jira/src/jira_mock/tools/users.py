"""User tool handlers."""

from __future__ import annotations

from typing import Any

from pydantic import EmailStr

from jira_mock.models import AccountId, JiraTimeZone, JiraUser, ShortNameString
from jira_mock.state import get_state, save_state
from jira_mock.tools.common import LimitArg, StartAtArg, _current_user, _dump, require_admin


def create_user(
    account_id: AccountId,
    display_name: ShortNameString,
    email_address: EmailStr | None = None,
    active: bool = True,
    time_zone: JiraTimeZone | None = "America/New_York",
) -> dict[str, Any]:
    """Create a Jira user. Requires is_admin=true."""
    require_admin()
    state = get_state()
    if account_id in state.users:
        raise ValueError(f"User {account_id} already exists")
    user = JiraUser(
        accountId=account_id,
        accountType="atlassian",
        emailAddress=email_address,
        displayName=display_name,
        active=active,
        timeZone=time_zone,
    )
    state.users[account_id] = user
    save_state()
    return _dump(user)


def get_users(
    query: str = "",
    active: bool | None = None,
    startAt: StartAtArg = 0,
    limit: LimitArg = 10,
) -> dict[str, Any]:
    """Get Jira users, optionally filtered by query text or active status."""
    users = list(get_state().users.values())
    if active is not None:
        users = [user for user in users if user.active is active]
    if query.strip():
        lowered = query.lower()
        users = [
            user
            for user in users
            if lowered in user.accountId.lower()
            or lowered in user.displayName.lower()
            or (user.emailAddress is not None and lowered in user.emailAddress.lower())
        ]
    return {
        "startAt": startAt,
        "maxResults": limit,
        "total": len(users),
        "values": _dump(users[startAt : startAt + limit]),
    }


def get_current_user() -> dict[str, Any]:
    """Get the Jira user whose account is currently authenticated for tool calls."""
    return _dump(_current_user())
