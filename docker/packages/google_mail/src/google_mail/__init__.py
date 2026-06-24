"""Google Mail mock MCP server for RL environment training."""

from __future__ import annotations

import argparse
import logging
import os

from .server import mcp

__all__ = ["main", "mcp"]


def main():
    parser = argparse.ArgumentParser(description="Google Mail MCP Server")
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug logging",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO)

    from .async_tool_guard import assert_tools_async

    assert_tools_async(mcp)

    from google_mail.server import init_state

    init_state()

    port = os.environ.get("PORT")
    if port:
        from google_mail.viewer import run_http_server

        run_http_server(mcp, int(port))
    else:
        mcp.run()


if __name__ == "__main__":
    main()
