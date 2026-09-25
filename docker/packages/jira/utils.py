#!/usr/bin/env python3
"""
Utilities for Jira MCP state management.
Used by preprocess and evaluation scripts.
"""

from pathlib import Path


def get_jira_state_path(agent_workspace: str | Path) -> Path:
    """
    Get the Jira state file path for a given workspace.

    Stores in external_services/ directory NEXT TO the workspace:
    - If workspace is at /workspace/dumps/workspace, stores at /workspace/dumps/external_services/
    - Outside the agent workspace so it can't be read directly via filesystem MCP
    - Inside the dumps mount so it persists to the host
    - Path is deterministic and can be computed by preprocess, MCP, and evaluation
    """
    workspace_path = Path(agent_workspace)
    # Go up one level from workspace and create external_services directory
    external_services_dir = workspace_path.parent / "external_services"
    return external_services_dir / "jira_state.json"
