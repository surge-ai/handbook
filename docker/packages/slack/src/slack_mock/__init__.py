"""Slack mock MCP server."""

from __future__ import annotations

import argparse
import logging
import os

from .async_tool_guard import assert_tools_async
from .server import mcp
from .state import init_state

__all__ = ["main", "mcp"]


def main() -> None:
    assert_tools_async(mcp)

    parser = argparse.ArgumentParser(description="Slack mock MCP Server")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    args = parser.parse_args()

    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO)

    init_state()

    port = os.environ.get("PORT")
    if port:
        from slack_mock.viewer import run_http_server

        run_http_server(mcp, int(port))
    else:
        mcp.run()


if __name__ == "__main__":
    main()
