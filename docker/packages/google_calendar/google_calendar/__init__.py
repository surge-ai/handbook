"""Google Calendar MCP server package."""

from __future__ import annotations

import argparse
import logging
import os

from .server import mcp


def main() -> None:
    parser = argparse.ArgumentParser(description="Google Calendar MCP Server")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    parser.add_argument("--agent-workspace", help="Agent workspace path used to resolve persistent calendar state")
    args = parser.parse_args()

    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO)

    from .async_tool_guard import assert_tools_async

    assert_tools_async(mcp)

    from .state import init_state

    init_state(args.agent_workspace)

    port = os.environ.get("PORT")
    if port:
        from .viewer import run_http_server

        run_http_server(mcp, int(port))
    else:
        mcp.run()


__all__ = ["main", "mcp"]
