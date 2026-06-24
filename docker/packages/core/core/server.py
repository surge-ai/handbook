"""Core standalone MCP server entry point.

This module exposes core's tools as an MCP server that can be launched as a
subprocess and proxied by mcp_proxy or any other MCP proxy.
"""

import os

from fastmcp import FastMCP
from fastmcp.tools.function_tool import FunctionTool

import core.tools as tools
from core._token import capture_proxy_token
from core.async_tool_guard import assert_tools_async
from core.privilege import ensure_workdir
from core.tools import sandbox
from core.viewer import run_http_server


def build_app() -> FastMCP:
    """Build a FastMCP app exposing all core tools."""
    app = FastMCP("core")
    for tool_name in sorted(tools.__all__):
        tool_fn = getattr(tools, tool_name, None)
        if callable(tool_fn):
            # output_schema=None matches the shipped mcp-tools.generated.json,
            # which has never advertised output schemas.
            app.add_tool(FunctionTool.from_function(fn=tool_fn, name=tool_name, output_schema=None))
    return app


@capture_proxy_token
def main() -> None:
    # The server intentionally keeps running as root: it needs to read the
    # locked-down /app tree, and a root server lets us close /opt/venv to uid
    # 1000. Privilege is dropped per agent command instead — see
    # core.tools.sandbox._privilege_drop_kwargs (the chokepoint every
    # bash/file-tool subprocess flows through).
    ensure_workdir()
    # Land the running server in the agent's workdir so any tool that doesn't
    # pass cwd= explicitly resolves relative paths under /workdir. Skip when
    # the dir doesn't exist (CI / local dev where /workdir isn't provisioned).
    if os.path.isdir(sandbox.WORKDIR):
        os.chdir(sandbox.WORKDIR)
    app = build_app()
    assert_tools_async(app)

    port = os.environ.get("PORT")
    if port:
        run_http_server(app, int(port))
    else:
        app.run(show_banner=False)


if __name__ == "__main__":
    main()
