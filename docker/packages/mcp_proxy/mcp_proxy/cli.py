"""MCP proxy CLI entrypoint.

Most configuration is read from HANDBOOK_* environment variables.
See scripts/start.sh for the translation from legacy CLI args to env
vars.

Each server's ``setup`` hook runs automatically before the server starts,
as part of the ``mcp`` command's startup (see ``commands/mcp.py``); there
is no separate setup entrypoint.
"""

import argparse
import sys


def main():
    parser = argparse.ArgumentParser(prog="mcp-proxy")
    subparsers = parser.add_subparsers(dest="command")

    mcp_parser = subparsers.add_parser("mcp", help="Start the MCP proxy server")
    mcp_parser.add_argument("--method", help="Transport method (stdio, sse, http)", default=None)
    mcp_parser.add_argument("--port", type=int, help="Port for the MCP server")
    subparsers.add_parser("gen", help="Generate mcp-tools.generated.json for all servers")

    args = parser.parse_args()

    if args.command == "mcp":
        from mcp_proxy.commands.mcp import run

        run(method=args.method, port=args.port)
    elif args.command == "gen":
        from mcp_proxy.commands.gen import run

        run()
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
