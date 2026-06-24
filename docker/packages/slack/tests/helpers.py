from __future__ import annotations

from typing import Any

from slack_mock.state import state_from_json


def seed_state(data: dict[str, Any]) -> None:
    defaults = {
        "bot_user_id": "U_MOCK_BOT",
        "users": {
            "U_MOCK_BOT": {
                "id": "U_MOCK_BOT",
                "name": "bot",
                "real_name": "Mock Bot",
                "profile": {"display_name": "bot", "status_text": "", "status_emoji": ""},
                "deleted": False,
            }
        },
        "channels": {},
        "messages": {},
        "counters": {"channelId": 1000, "fileId": 1000},
    }
    merged = {**defaults, **data}
    merged["users"] = {**defaults["users"], **data.get("users", {})}
    merged["counters"] = {**defaults["counters"], **data.get("counters", {})}
    state_from_json(merged)
