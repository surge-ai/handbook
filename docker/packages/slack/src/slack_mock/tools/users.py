"""User tool handlers."""

from __future__ import annotations

from typing import Any

from slack_mock.models import SlackState
from slack_mock.state import get_bot_user_id, get_state, get_user, is_admin_mode, mutate_state
from slack_mock.tools.common import model_dump, now_seconds, parse_cursor


def get_users(cursor: str | None = None, limit: int = 100) -> dict[str, Any]:
    all_users = list(get_state().users.values())
    normalized_limit = min(limit or 100, 200)
    start_index = parse_cursor(cursor)
    paginated = all_users[start_index : start_index + normalized_limit]
    next_index = start_index + normalized_limit
    return {
        "ok": True,
        "members": model_dump(paginated),
        "response_metadata": {"next_cursor": str(next_index) if next_index < len(all_users) else ""},
    }


def get_user_profile(user_id: str) -> dict[str, Any]:
    user = get_user(user_id)
    if user is None:
        return {"ok": False, "error": "user_not_found", "profile": {}}
    return {"ok": True, "profile": user.profile.model_dump(mode="json", by_alias=True, exclude_none=True)}


def set_user_status(
    status_text: str,
    status_emoji: str | None = None,
    user_id: str | None = None,
) -> dict[str, Any]:
    target_user_id = user_id or get_bot_user_id()
    user = get_user(target_user_id)
    if user is None:
        return {"ok": False, "error": "user_not_found", "profile": {}}
    if target_user_id != get_bot_user_id() and not is_admin_mode():
        return {"ok": False, "error": "cant_update_profile", "profile": {}}

    def _set_status(state: SlackState):
        user = state.users[target_user_id]
        user.profile.status_text = status_text
        if status_emoji is not None:
            user.profile.status_emoji = status_emoji
        return user.profile

    profile = mutate_state(_set_status)
    return {"ok": True, "profile": profile.model_dump(mode="json", by_alias=True, exclude_none=True)}


def get_user_presence(user_id: str) -> dict[str, Any]:
    user = get_user(user_id)
    if user is None:
        return {
            "ok": False,
            "error": "user_not_found",
            "presence": "",
            "online": False,
            "auto_away": False,
            "manual_away": False,
        }
    is_active = not user.deleted
    return {
        "ok": True,
        "presence": "active" if is_active else "away",
        "online": is_active,
        "auto_away": False,
        "manual_away": not is_active,
        "connection_count": 1 if is_active else 0,
        "last_activity": now_seconds(),
    }
