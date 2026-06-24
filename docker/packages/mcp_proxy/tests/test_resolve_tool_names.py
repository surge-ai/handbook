"""Tests for resolve_tool_transforms in mcp_proxy.service."""

from __future__ import annotations

import json

import pytest

from mcp_proxy.service import McpConfig, McpService


def _make_cfg(tmp_path, tools: list[str], toolsets: dict, name: str | None = None) -> McpService:
    """Write mcp.json + mcp-tools.generated.json and return an McpService."""
    mcp_json: dict = {
        "run": {"command": "python", "args": ["-m", "fake"]},
        "toolsets": toolsets,
    }
    if name is not None:
        mcp_json["name"] = name
    (tmp_path / "mcp.json").write_text(json.dumps(mcp_json))
    tools_data = {
        "schema_version": "v1",
        "tools": [{"name": t, "description": "", "inputSchema": {}} for t in tools],
        "toolsets": toolsets,
    }
    (tmp_path / "mcp-tools.generated.json").write_text(json.dumps(tools_data))
    return McpService(McpConfig.from_mcp_json(tmp_path, mcp_json))


def _enabled_tools(transforms: dict) -> dict[str, str]:
    """Return {original_name: new_name} for enabled tools only."""
    return {name: t.name for name, t in transforms.items() if t.enabled and t.name}


def _disabled_tools(transforms: dict) -> set[str]:
    """Return set of original names for disabled tools."""
    return {name for name, t in transforms.items() if not t.enabled}


TOOLS = ["echo", "readFile", "writeFile", "bash"]
TOOLSETS = {
    "read": ["echo", "readFile"],
    "write": ["writeFile", "bash"],
}


def test_empty_tool_sets_exposes_all_tools(tmp_path):
    """An empty filter (or unset env var) means 'expose every introspected tool'."""
    cfg = _make_cfg(tmp_path, TOOLS, TOOLSETS)
    result = cfg.resolve_tool_transforms([])
    assert set(_enabled_tools(result).keys()) == set(TOOLS)
    assert _disabled_tools(result) == set()


def test_namespaced_read_filters_correctly(tmp_path):
    pkg = tmp_path / "slack"
    pkg.mkdir()
    cfg = _make_cfg(pkg, TOOLS, TOOLSETS)
    result = cfg.resolve_tool_transforms(["slack_read"])
    assert set(_enabled_tools(result).keys()) == {"echo", "readFile"}
    assert _disabled_tools(result) == {"writeFile", "bash"}


def test_namespaced_write_filters_correctly(tmp_path):
    pkg = tmp_path / "slack"
    pkg.mkdir()
    cfg = _make_cfg(pkg, TOOLS, TOOLSETS)
    result = cfg.resolve_tool_transforms(["slack_write"])
    assert set(_enabled_tools(result).keys()) == {"writeFile", "bash"}
    assert _disabled_tools(result) == {"echo", "readFile"}


def test_multiple_namespaced_toolsets_are_unioned(tmp_path):
    pkg = tmp_path / "slack"
    pkg.mkdir()
    cfg = _make_cfg(pkg, TOOLS, TOOLSETS)
    result = cfg.resolve_tool_transforms(["slack_read", "slack_write"])
    assert set(_enabled_tools(result).keys()) == set(TOOLS)
    assert _disabled_tools(result) == set()


def test_tools_are_namespaced(tmp_path):
    pkg = tmp_path / "slack"
    pkg.mkdir()
    cfg = _make_cfg(pkg, TOOLS, TOOLSETS)
    result = cfg.resolve_tool_transforms(["slack_read"])
    for original, new_name in _enabled_tools(result).items():
        assert new_name == f"slack__{original}"


def test_unnamespaced_package_exposes_bare_tool_names(tmp_path):
    """When mcp.json sets namespaced=false, tools are exposed without a prefix."""
    mcp_json = {
        "run": {"command": "python", "args": ["-m", "fake"]},
        "namespaced": False,
        "toolsets": TOOLSETS,
    }
    (tmp_path / "mcp.json").write_text(json.dumps(mcp_json))
    tools_data = {
        "schema_version": "v1",
        "tools": [{"name": t, "description": "", "inputSchema": {}} for t in TOOLS],
        "toolsets": TOOLSETS,
    }
    (tmp_path / "mcp-tools.generated.json").write_text(json.dumps(tools_data))
    cfg = McpService(McpConfig.from_mcp_json(tmp_path, mcp_json))
    result = cfg.resolve_tool_transforms([])
    for original, new_name in _enabled_tools(result).items():
        assert new_name == original


def test_no_toolsets_in_mcp_json_exposes_all(tmp_path):
    """When mcp.json has no toolsets key, all tools are exposed regardless of filter."""
    cfg = _make_cfg(tmp_path, TOOLS, {})
    result = cfg.resolve_tool_transforms(["whatever"])
    assert set(_enabled_tools(result).keys()) == set(TOOLS)
    assert _disabled_tools(result) == set()


def test_unknown_toolset_disables_all(tmp_path):
    """A namespaced toolset that doesn't exist on this package disables every tool."""
    pkg = tmp_path / "slack"
    pkg.mkdir()
    cfg = _make_cfg(pkg, TOOLS, TOOLSETS)
    result = cfg.resolve_tool_transforms(["slack_nonexistent"])
    assert _enabled_tools(result) == {}
    assert _disabled_tools(result) == set(TOOLS)


def test_other_packages_namespace_does_not_match(tmp_path):
    """A toolset namespaced to a different package is ignored — matches no tools."""
    pkg = tmp_path / "slack"
    pkg.mkdir()
    cfg = _make_cfg(pkg, TOOLS, TOOLSETS)
    result = cfg.resolve_tool_transforms(["jira_read"])
    assert _enabled_tools(result) == {}
    assert _disabled_tools(result) == set(TOOLS)


def test_missing_generated_json_raises(tmp_path):
    mcp_json = {"run": {"command": "python", "args": []}, "toolsets": TOOLSETS}
    (tmp_path / "mcp.json").write_text(json.dumps(mcp_json))
    cfg = McpService(McpConfig.from_mcp_json(tmp_path, mcp_json))
    with pytest.raises(FileNotFoundError, match=r"mcp-tools\.generated\.json not found"):
        cfg.resolve_tool_transforms(["slack_read"])


# ---------------------------------------------------------------------------
# State toolsets are reachable when explicitly requested. The "all" / "_all"
# special case (with its hidden-by-default semantics for state/grading tools)
# was removed when the auto-aggregated cross-package toolsets were dropped.
# ---------------------------------------------------------------------------


STATEFUL_TOOLS = ["echo", "readFile", "export_state", "import_state"]
STATEFUL_TOOLSETS = {
    "read": ["echo", "readFile"],
    "state": ["export_state", "import_state"],
}


def test_namespaced_state_request_exposes_state(tmp_path):
    pkg = tmp_path / "slack"
    pkg.mkdir()
    cfg = _make_cfg(pkg, STATEFUL_TOOLS, STATEFUL_TOOLSETS)
    result = cfg.resolve_tool_transforms(["slack_state"])
    assert set(_enabled_tools(result).keys()) == {"export_state", "import_state"}


def test_namespaced_read_does_not_expose_state(tmp_path):
    pkg = tmp_path / "slack"
    pkg.mkdir()
    cfg = _make_cfg(pkg, STATEFUL_TOOLS, STATEFUL_TOOLSETS)
    result = cfg.resolve_tool_transforms(["slack_read"])
    assert set(_enabled_tools(result).keys()) == {"echo", "readFile"}
    assert _disabled_tools(result) == {"export_state", "import_state"}


def test_combined_namespaced_toolsets_union(tmp_path):
    pkg = tmp_path / "slack"
    pkg.mkdir()
    cfg = _make_cfg(pkg, STATEFUL_TOOLS, STATEFUL_TOOLSETS)
    result = cfg.resolve_tool_transforms(["slack_read", "slack_state"])
    assert set(_enabled_tools(result).keys()) == set(STATEFUL_TOOLS)
