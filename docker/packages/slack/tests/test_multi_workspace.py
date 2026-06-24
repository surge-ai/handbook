from __future__ import annotations

import pytest

from slack_mock.server import get_channel_history, list_workspaces, post_message, search_messages
from slack_mock.state import get_active_workspace_id, state_from_json, state_to_json


def _workspace_state(message_text: str) -> dict:
    return {
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
        "channels": {"C1": {"id": "C1", "name": "general", "context_team_id": "T_MOCK"}},
        "messages": {"C1": [{"ts": "1700000001.000", "text": message_text, "user": "U_MOCK_BOT", "type": "message"}]},
    }


@pytest.mark.asyncio
async def test_workspace_selector_routes_reads_and_writes_independently() -> None:
    state_from_json(
        {
            "workspaces": {
                "default": _workspace_state("default workspace"),
                "acme": _workspace_state("acme workspace"),
            }
        }
    )

    listed = await list_workspaces()
    assert listed["ok"] is True
    assert listed["total"] == 2
    assert {workspace["workspace_id"] for workspace in listed["workspaces"]} == {"default", "acme"}

    default_history = await get_channel_history("C1")
    acme_history = await get_channel_history("C1", workspace_id="acme")
    assert default_history["messages"][0]["text"] == "default workspace"
    assert acme_history["messages"][0]["text"] == "acme workspace"

    posted = await post_message("C1", "acme follow-up", workspace_id="acme")
    assert posted["ok"] is True

    exported = state_to_json()
    assert len(exported["workspaces"]["default"]["messages"]["C1"]) == 1
    assert len(exported["workspaces"]["acme"]["messages"]["C1"]) == 2

    assert (await search_messages("acme", workspace_id="acme"))["messages"]["total"] == 2
    assert (await search_messages("acme", workspace_id="default"))["messages"]["total"] == 0


@pytest.mark.asyncio
async def test_failed_workspace_write_does_not_mutate_other_workspaces() -> None:
    state_from_json(
        {
            "workspaces": {
                "default": _workspace_state("default workspace"),
                "acme": _workspace_state("acme workspace"),
            }
        }
    )

    before = state_to_json()
    failed = await post_message("C_DOES_NOT_EXIST", "should not post", workspace_id="acme")

    assert failed["ok"] is False
    assert failed["error"] == "channel_not_found"
    assert state_to_json() == before


@pytest.mark.asyncio
async def test_state_import_resets_active_workspace() -> None:
    state_from_json(
        {
            "workspaces": {
                "default": _workspace_state("default workspace"),
                "acme": _workspace_state("acme workspace"),
            }
        }
    )
    await get_channel_history("C1", workspace_id="acme")
    assert get_active_workspace_id() == "acme"

    state_from_json({"workspaces": {"default": _workspace_state("reset default workspace")}})

    assert get_active_workspace_id() == "default"
    assert (await get_channel_history("C1"))["messages"][0]["text"] == "reset default workspace"


@pytest.mark.asyncio
async def test_omitted_workspace_uses_active_non_default_workspace() -> None:
    state_from_json({"workspaces": {"acme": _workspace_state("acme only workspace")}})

    assert get_active_workspace_id() == "acme"
    assert (await get_channel_history("C1"))["messages"][0]["text"] == "acme only workspace"

    posted = await post_message("C1", "acme follow-up")

    assert posted["ok"] is True
    assert state_to_json()["workspaces"]["acme"]["messages"]["C1"][-1]["text"] == "acme follow-up"
