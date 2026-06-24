"""Repository-level checks for package toolset declarations."""

from __future__ import annotations

import json
from pathlib import Path

STATE_TOOLS = {"export_state", "import_state"}


def test_state_tools_only_appear_in_state_toolsets():
    repo_root = Path(__file__).resolve().parents[3]
    offenders: list[str] = []

    for mcp_json in sorted((repo_root / "packages").glob("*/mcp.json")):
        package = mcp_json.parent.name
        data = json.loads(mcp_json.read_text())
        for toolset, tools in data.get("toolsets", {}).items():
            if toolset == "state":
                continue
            leaked = sorted(STATE_TOOLS.intersection(tools))
            if leaked:
                offenders.append(f"{package}:{toolset} -> {', '.join(leaked)}")

    assert offenders == []
