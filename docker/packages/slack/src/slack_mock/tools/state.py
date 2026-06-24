"""State import/export tool handlers."""

from __future__ import annotations

from typing import Any

from slack_mock.models import SlackMockState, SlackState, SlackWorkspacesState
from slack_mock.state import list_workspaces as list_loaded_workspaces
from slack_mock.state import state_from_json, state_to_json


def export_state() -> SlackMockState:
    exported = state_to_json()
    if "workspaces" in exported:
        return SlackWorkspacesState.model_validate(exported)
    return SlackState.model_validate(exported)


def import_state(state: dict[str, Any]) -> dict[str, bool]:
    state_from_json(state)
    return {"ok": True}


def list_workspaces() -> dict[str, Any]:
    return list_loaded_workspaces()
