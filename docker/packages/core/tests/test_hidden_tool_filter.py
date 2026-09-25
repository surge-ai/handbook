"""Verify HiddenToolFilter: hidden tools excluded from list_tools but still callable."""

import asyncio

from fastmcp import Client, FastMCP
from fastmcp.server.middleware import Middleware
from fastmcp.tools.function_tool import FunctionTool


class HiddenToolFilter(Middleware):
    async def on_list_tools(self, context, call_next):
        tool_list = await call_next(context)
        return [t for t in tool_list if "hidden" not in (t.tags or set())]


def _build_app() -> FastMCP:
    app = FastMCP("test")

    def visible_tool() -> str:
        """A normal tool."""
        return "visible"

    def hidden_tool() -> str:
        """A hidden tool."""
        return "hidden"

    app.add_tool(FunctionTool.from_function(fn=visible_tool, name="visible_tool"))
    app.add_tool(FunctionTool.from_function(fn=hidden_tool, name="hidden_tool", tags={"hidden"}))
    app.add_middleware(HiddenToolFilter())
    return app


async def main():
    app = _build_app()
    async with Client(app) as client:
        # 1. Hidden tool should NOT appear in list_tools
        tools = await client.list_tools()
        tool_names = [t.name for t in tools]
        assert "visible_tool" in tool_names, f"visible_tool missing from {tool_names}"
        assert "hidden_tool" not in tool_names, f"hidden_tool should be hidden but found in {tool_names}"
        print("PASS: hidden tool excluded from list_tools")

        # 2. Hidden tool should still be callable
        result = await client.call_tool("hidden_tool", {})
        assert result.content[0].text == "hidden", f"unexpected result: {result}"
        print("PASS: hidden tool still callable via call_tool")

    print("\nAll checks passed.")


if __name__ == "__main__":
    asyncio.run(main())
