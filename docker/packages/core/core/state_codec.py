"""State codec for core.

Core uses a file-backed sandbox with no database entities to snapshot.
``export_state``/``import_state`` exist for uniformity with the other MCP
servers (the proxy validates every server has them); round-trip is
trivially empty.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict


class CoreState(BaseModel):
    """Empty state — core has no DB to snapshot."""

    model_config = ConfigDict(extra="allow")


def state_to_json() -> dict[str, Any]:
    return {}


def state_from_json(data: dict[str, Any]) -> None:
    _ = data
