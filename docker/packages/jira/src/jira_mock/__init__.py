"""Jira mock MCP server."""

from __future__ import annotations

import argparse
import logging
import os

from .server import mcp
from .state import init_state, set_agent_workspace


def main() -> None:
    parser = argparse.ArgumentParser(description="Jira Mock MCP Server")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    parser.add_argument("--agent-workspace", help="Agent workspace path used to resolve persistent Jira state")
    args = parser.parse_args()

    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO)

    if args.agent_workspace:
        set_agent_workspace(args.agent_workspace)

    init_state()

    from .async_tool_guard import assert_tools_async

    assert_tools_async(mcp)

    port = os.environ.get("PORT")
    if port:
        from jira_mock.viewer import run_http_server

        run_http_server(mcp, int(port))
    else:
        mcp.run()


if __name__ == "__main__":
    main()
