"""State import/export tool handlers."""

from __future__ import annotations

from typing import Any

from jira_mock.state import list_sites as list_loaded_sites
from jira_mock.state import state_from_json, state_to_json


def export_state() -> dict[str, Any]:
    return state_to_json()


def import_state(state: dict[str, Any]) -> dict[str, bool]:
    state_from_json(state)
    return {"ok": True}


def list_sites() -> dict[str, Any]:
    return list_loaded_sites()
