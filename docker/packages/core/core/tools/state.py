"""Standard state tools: export_state and import_state.

Delegates to the shared state codec in ``core.state_codec`` so the MCP tools
and any future file-based loader read/write the same canonical format.
"""

from __future__ import annotations

from typing import Any

from core import state_codec


async def export_state() -> dict[str, list[dict[str, Any]]]:
    """Export the full core state as JSON.

    Round-trips with import_state. State is keyed by snake_case entity-class
    name (e.g. ``project``, ``employee``, ``slack_message``).
    """
    return state_codec.state_to_json()


async def import_state(state: state_codec.SyntaraState) -> dict:
    """Replace the full core state with the provided JSON.

    For synthetic-data injection and test setup. Round-trips with export_state.
    """
    state_codec.state_from_json(state.model_dump(exclude_unset=True))
    return {"ok": True}
