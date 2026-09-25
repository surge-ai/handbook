"""Message tool handlers."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from pydantic import Field

from slack_mock.models import SlackMessage, SlackMessageEdited, SlackMessageType
from slack_mock.state import (
    add_message,
    add_thread_reply,
    find_message,
    generate_timestamp,
    get_bot_user_id,
    get_channel,
    get_channel_messages,
    get_state,
    is_admin_mode,
    update_message,
)
from slack_mock.state import (
    delete_message as delete_message_from_state,
)
from slack_mock.state import (
    get_thread_replies as get_thread_replies_from_state,
)
from slack_mock.tools.common import empty_message, message_payload, model_dump


def post_message(channel_id: str, text: str) -> dict[str, Any]:
    channel = get_channel(channel_id)
    if channel is None:
        return {"ok": False, "error": "channel_not_found", "channel": channel_id, "ts": "", "message": empty_message()}
    ts = generate_timestamp()
    message = SlackMessage(
        type=SlackMessageType.MESSAGE, user=get_bot_user_id(), text=text, ts=ts, team=channel.context_team_id
    )
    add_message(channel_id, message)
    return {"ok": True, "channel": channel_id, "ts": ts, "message": message_payload(message)}


def reply_to_thread(channel_id: str, thread_ts: str, text: str) -> dict[str, Any]:
    channel = get_channel(channel_id)
    if channel is None:
        return {"ok": False, "error": "channel_not_found", "channel": channel_id, "ts": "", "message": empty_message()}
    parent = find_message(channel_id, thread_ts)
    if parent is None:
        return {"ok": False, "error": "thread_not_found", "channel": channel_id, "ts": "", "message": empty_message()}
    ts = generate_timestamp()
    message = SlackMessage(
        type=SlackMessageType.MESSAGE,
        user=get_bot_user_id(),
        text=text,
        ts=ts,
        thread_ts=thread_ts,
        parent_user_id=parent.user,
        team=channel.context_team_id,
    )
    add_thread_reply(channel_id, thread_ts, message)
    return {"ok": True, "channel": channel_id, "ts": ts, "message": message_payload(message)}


def get_channel_history(channel_id: str, limit: int = 10) -> dict[str, Any]:
    if get_channel(channel_id) is None:
        return {"ok": False, "error": "channel_not_found", "messages": [], "has_more": False}
    all_messages = get_channel_messages(channel_id)
    top_level = [message for message in all_messages if not message.thread_ts or message.thread_ts == message.ts]
    sorted_messages = sorted(top_level, key=lambda message: float(message.ts), reverse=True)
    limited = sorted_messages[: limit or 10]
    return {"ok": True, "messages": model_dump(limited), "has_more": len(sorted_messages) > (limit or 10)}


def get_thread_replies(channel_id: str, thread_ts: str) -> dict[str, Any]:
    if get_channel(channel_id) is None:
        return {"ok": False, "error": "channel_not_found", "messages": [], "has_more": False}
    messages = get_thread_replies_from_state(channel_id, thread_ts)
    if not messages:
        return {"ok": False, "error": "thread_not_found", "messages": [], "has_more": False}
    return {"ok": True, "messages": model_dump(messages), "has_more": False}


def _normalize_filter_value(value: str) -> str:
    return value.strip().lstrip("#@").lower()


def _parse_date_range(value: str) -> tuple[float, float] | None:
    trimmed = value.strip()
    try:
        if re.match(r"^\d{4}$", trimmed):
            year = int(trimmed)
            start = datetime(year, 1, 1, tzinfo=UTC)
            end = datetime(year + 1, 1, 1, tzinfo=UTC)
        elif re.match(r"^\d{4}-\d{2}$", trimmed):
            year, month = [int(part) for part in trimmed.split("-")]
            start = datetime(year, month, 1, tzinfo=UTC)
            end = datetime(year + (month // 12), (month % 12) + 1, 1, tzinfo=UTC)
        elif re.match(r"^\d{4}-\d{2}-\d{2}$", trimmed):
            year, month, day = [int(part) for part in trimmed.split("-")]
            start = datetime(year, month, day, tzinfo=UTC)
            end = start + timedelta(days=1)
        else:
            parsed = datetime.fromisoformat(trimmed.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=UTC)
            start = parsed
            end = parsed + timedelta(days=1)
    except ValueError:
        return None
    return start.timestamp(), end.timestamp()


def _parse_date_start(value: str) -> float | None:
    trimmed = value.strip()
    if not trimmed:
        return None
    if re.match(r"^\d+(\.\d+)?$", trimmed):
        return float(trimmed)
    date_range = _parse_date_range(trimmed)
    return date_range[0] if date_range else None


def _parse_search_query(query: str) -> dict[str, Any]:
    parsed: dict[str, Any] = {
        "textTokens": [],
        "channelFilters": [],
        "userFilters": [],
        "hasFilters": [],
        "warnings": [],
    }

    def add_warning(message: str) -> None:
        if message not in parsed["warnings"]:
            parsed["warnings"].append(message)

    for match in re.finditer(r'"([^"]+)"|\S+', query):
        was_quoted = match.group(1) is not None
        raw = (match.group(1) if was_quoted else match.group(0)).strip()
        if not raw:
            continue
        token = raw.lower()
        if was_quoted:
            parsed["textTokens"].append(token)
        elif token.startswith("in:"):
            value = _normalize_filter_value(raw[3:])
            if value:
                parsed["channelFilters"].append(value)
            else:
                add_warning("Empty in: filter was ignored.")
        elif token.startswith("from:"):
            raw_value = raw[5:].strip()
            value = _normalize_filter_value(raw_value)
            if value == "me":
                add_warning("from:me is unsupported because caller identity is not available; it will match no users.")
            if value:
                parsed["userFilters"].append({"value": value, "handle_only": raw_value.startswith("@")})
            else:
                add_warning("Empty from: filter was ignored.")
        elif token.startswith("after:"):
            bound = _parse_date_start(raw[6:])
            if bound is not None:
                parsed["after"] = max(parsed.get("after", float("-inf")), bound)
            else:
                add_warning(f"Invalid after: date '{raw[6:]}'. Use a Unix timestamp or YYYY[-MM[-DD]].")
        elif token.startswith("before:"):
            bound = _parse_date_start(raw[7:])
            if bound is not None:
                parsed["before"] = min(parsed.get("before", float("inf")), bound)
            else:
                add_warning(f"Invalid before: date '{raw[7:]}'. Use a Unix timestamp or YYYY[-MM[-DD]].")
        elif token.startswith("during:"):
            date_range = _parse_date_range(raw[7:])
            if date_range:
                parsed["after"] = max(parsed.get("after", float("-inf")), date_range[0])
                parsed["before"] = min(parsed.get("before", float("inf")), date_range[1])
            else:
                add_warning(f"Invalid during: date '{raw[7:]}'. Use YYYY, YYYY-MM, YYYY-MM-DD, or a parseable date.")
        elif token.startswith("has:"):
            value = _normalize_filter_value(raw[4:])
            if value:
                if value not in {"link", "reaction", "star", "pin", "pinned"}:
                    add_warning(f"Unsupported has: value '{value}'. Supported values: link, reaction, star, pin.")
                parsed["hasFilters"].append(value)
            else:
                add_warning("Empty has: filter was ignored.")
        elif re.match(r"^[a-z_]+:", raw, flags=re.I) and not re.match(r"^(https?|mailto):", raw, flags=re.I):
            operator = raw[: raw.index(":") + 1]
            add_warning(f"Unsupported Slack search operator '{operator}'; it will be treated as a text token.")
            parsed["textTokens"].append(token)
        else:
            parsed["textTokens"].append(token)
    return parsed


def _channel_matches_filter(channel_id: str, channel_name: str, filter_value: str) -> bool:
    return channel_id.lower() == filter_value or channel_name.lower() == filter_value


def _user_matches_filter(user_id: str | None, user_name: str, filter_value: dict[str, Any]) -> bool:
    value = filter_value["value"]
    if not user_id or value == "me":
        return False
    user = get_state().users.get(user_id)
    if value == user_id.lower():
        return True
    if filter_value.get("handle_only"):
        return bool(user and user.name.casefold() == value)
    substrings = [
        user.name if user else None,
        user.real_name if user else None,
        user.profile.display_name if user else None,
        user.profile.real_name if user else None,
        user.profile.email if user else None,
        user_name,
    ]
    lowered = [str(value).lower() for value in substrings if value]
    return any(filter_value["value"] in value for value in lowered)


def _normalize_search_limit(limit: int | None, warnings: list[str]) -> int:
    value = 20 if limit is None else limit
    if value > 100:
        warnings.append("limit exceeds the maximum of 100; using 100.")
        return 100
    return value


def _normalize_search_cursor(cursor: str | None, warnings: list[str]) -> int:
    if not cursor:
        return 0
    try:
        parsed = int(cursor)
    except ValueError:
        warnings.append(f"Invalid cursor '{cursor}'; using the first page.")
        return 0
    if parsed < 0:
        warnings.append("cursor must be non-negative; using the first page.")
        return 0
    return parsed


def _message_has_filter(message: SlackMessage, channel_id: str, filter_value: str) -> bool:
    if filter_value == "reaction":
        return bool(message.reactions)
    if filter_value == "star":
        return bool(message.is_starred)
    if filter_value in {"pin", "pinned"}:
        return bool(message.pinned_to and channel_id in message.pinned_to)
    if filter_value == "link":
        return bool(
            any(
                attachment.title_link or attachment.author_link or attachment.image_url or attachment.thumb_url
                for attachment in message.attachments or []
            )
            or any(file.url_private or file.permalink for file in message.files or [])
            or re.search(r"https?://", message.text, flags=re.I)
        )
    return False


def search_messages(
    query: str,
    channel_id: str | None = None,
    limit: Annotated[
        int | None,
        Field(ge=0, description="Maximum number of matches to return. Values above 100 are capped."),
    ] = None,
    cursor: str | None = None,
) -> dict[str, Any]:
    state = get_state()
    if not query or not query.strip():
        return {
            "ok": False,
            "error": "missing_query",
            "messages": {"matches": [], "total": 0},
            "response_metadata": {"warnings": []},
        }

    parsed = _parse_search_query(query)
    warnings = parsed["warnings"]
    has_structured = (
        any(parsed[key] for key in ("channelFilters", "userFilters", "hasFilters"))
        or "after" in parsed
        or "before" in parsed
    )
    if not parsed["textTokens"] and not has_structured:
        return {
            "ok": False,
            "error": "missing_query",
            "messages": {"matches": [], "total": 0},
            "response_metadata": {"warnings": warnings},
        }

    normalized_limit = _normalize_search_limit(limit, warnings)
    start_index = _normalize_search_cursor(cursor, warnings)

    if channel_id:
        if channel_id not in state.channels:
            return {"ok": False, "error": "channel_not_found", "messages": {"matches": [], "total": 0}}
        channel_ids = [channel_id]
    else:
        channel_ids = list(state.channels)

    if parsed["channelFilters"]:
        if channel_id:
            scoped = state.channels[channel_id]
            conflicts = all(
                not _channel_matches_filter(channel_id, scoped.name, filter_value)
                for filter_value in parsed["channelFilters"]
            )
            if conflicts:
                return {
                    "ok": False,
                    "error": "channel_scope_conflict",
                    "messages": {"matches": [], "total": 0},
                    "response_metadata": {"warnings": warnings},
                }
        channel_ids = [
            cid
            for cid in channel_ids
            if any(
                _channel_matches_filter(cid, state.channels[cid].name, filter_value)
                for filter_value in parsed["channelFilters"]
            )
        ]

    matches: list[dict[str, Any]] = []
    for cid in channel_ids:
        channel = state.channels[cid]
        for message in get_channel_messages(cid):
            user = state.users.get(message.user) if message.user else None
            display_name = user.profile.display_name if user else ""
            real_name = user.real_name if user else ""
            user_name = user.name if user else ""
            rendered_user_name = display_name or real_name or user_name or message.user or "Unknown"
            haystack = "\n".join(
                value for value in [message.text, display_name or "", real_name or "", user_name or ""] if value
            ).lower()
            ts = float(message.ts)
            if "after" in parsed and ts < parsed["after"]:
                continue
            if "before" in parsed and ts >= parsed["before"]:
                continue
            if parsed["userFilters"] and not any(
                _user_matches_filter(
                    message.user, display_name or real_name or user_name or message.user or "", filter_value
                )
                for filter_value in parsed["userFilters"]
            ):
                continue
            if parsed["hasFilters"] and not all(
                _message_has_filter(message, cid, filter_value) for filter_value in parsed["hasFilters"]
            ):
                continue
            if all(token in haystack for token in parsed["textTokens"]):
                matches.append(
                    {
                        "channelId": cid,
                        "channelName": channel.name,
                        "message": message,
                        "displayName": display_name or real_name or user_name or message.user or "Unknown",
                        "username": user_name,
                        "userName": rendered_user_name,
                    }
                )

    matches.sort(key=lambda item: float(item["message"].ts), reverse=True)
    total = len(matches)
    paginated = matches[start_index : start_index + normalized_limit]
    next_index = start_index + normalized_limit
    return {
        "ok": True,
        "messages": {
            "matches": [
                {
                    "channel": {"id": item["channelId"], "name": item["channelName"]},
                    "ts": item["message"].ts,
                    "text": item["message"].text,
                    "user": item["message"].user or "",
                    "username": item["username"],
                    "display_name": item["displayName"],
                    "user_name": item["userName"],
                    "thread_ts": item["message"].thread_ts,
                    "reply_count": item["message"].reply_count,
                    "reactions": model_dump(item["message"].reactions),
                    "permalink": item["message"].permalink,
                }
                for item in paginated
            ],
            "total": total,
        },
        "response_metadata": {"next_cursor": str(next_index) if next_index < total else "", "warnings": warnings},
    }


def edit_message(channel_id: str, ts: str, text: str) -> dict[str, Any]:
    if get_channel(channel_id) is None:
        return {
            "ok": False,
            "error": "channel_not_found",
            "channel": channel_id,
            "ts": ts,
            "text": "",
            "message": empty_message(),
        }
    message = find_message(channel_id, ts)
    if message is None:
        return {
            "ok": False,
            "error": "message_not_found",
            "channel": channel_id,
            "ts": ts,
            "text": "",
            "message": empty_message(),
        }
    if not is_admin_mode() and message.user and message.user != get_bot_user_id():
        return {
            "ok": False,
            "error": "cant_update_message",
            "channel": channel_id,
            "ts": ts,
            "text": "",
            "message": empty_message(),
        }

    def _edit(msg: SlackMessage) -> None:
        msg.text = text
        msg.edited = SlackMessageEdited(user=get_bot_user_id(), ts=generate_timestamp())

    update_message(channel_id, ts, _edit)
    updated = find_message(channel_id, ts)
    if updated is None:
        return {
            "ok": False,
            "error": "message_not_found",
            "channel": channel_id,
            "ts": ts,
            "text": "",
            "message": empty_message(),
        }
    return {"ok": True, "channel": channel_id, "ts": ts, "text": updated.text, "message": message_payload(updated)}


def delete_message(channel_id: str, ts: str) -> dict[str, Any]:
    if get_channel(channel_id) is None:
        return {"ok": False, "error": "channel_not_found", "channel": channel_id, "ts": ts}
    message = find_message(channel_id, ts)
    if message is None:
        return {"ok": False, "error": "message_not_found", "channel": channel_id, "ts": ts}
    if not is_admin_mode() and message.user and message.user != get_bot_user_id():
        return {"ok": False, "error": "cant_delete_message", "channel": channel_id, "ts": ts}
    delete_message_from_state(channel_id, ts)
    return {"ok": True, "channel": channel_id, "ts": ts}
