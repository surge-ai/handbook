"""Shopify mock MCP server."""

from __future__ import annotations

import argparse
import logging
import os

from .server import mcp
from .state import init_state

__all__ = ["main", "mcp"]


def main() -> None:
    parser = argparse.ArgumentParser(description="Shopify Mock MCP Server")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    parser.add_argument("--agent-workspace", help="Agent workspace path used to resolve persistent Shopify state")
    args = parser.parse_args()

    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO)

    from .async_tool_guard import assert_tools_async

    assert_tools_async(mcp)

    init_state(args.agent_workspace)

    port = os.environ.get("PORT")
    if port:
        from .viewer import run_http_server

        run_http_server(mcp, int(port))
    else:
        mcp.run()
