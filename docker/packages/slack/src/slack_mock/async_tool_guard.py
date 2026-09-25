"""Boot-time guard: every registered MCP tool must be async.

Sync (`def`) tools make FastMCP run pydantic argument validation in an anyio
worker threadpool. Under concurrent calls the shared pydantic-core validator is
re-entered across threads and panics with ``pyo3_runtime.PanicException:
dictionary changed size during iteration``, which tears down the
StreamableHTTP task group and turns every later request into a 500. Async
(`async def`) tools validate on the single-threaded event loop and are safe.

This guard runs at server boot (and therefore during ``mcp-proxy gen``, which
boots every server): a non-conformant package fails fast instead of shipping a
server that can crash under load. The same check is intentionally duplicated in
each package because the bundled prod images install only that package's own
dependencies, so there is no shared runtime module to import.
"""

from __future__ import annotations

import asyncio
import inspect
from functools import partial
from typing import Any


def _unwrap(fn: Any) -> Any:
    while isinstance(fn, partial):
        fn = fn.func
    return fn


def find_sync_tools(mcp: Any) -> list[str]:
    """Return the names of registered tools whose function is not a coroutine."""
    # mcp.server.fastmcp.FastMCP exposes a synchronous tool manager.
    manager = getattr(mcp, "_tool_manager", None)
    if manager is not None and hasattr(manager, "list_tools"):
        return sorted(t.name for t in manager.list_tools() if not inspect.iscoroutinefunction(_unwrap(t.fn)))

    # fastmcp.FastMCP exposes an async API.
    async def _collect() -> list[str]:
        sync: list[str] = []
        for descriptor in await mcp.list_tools():
            tool = await mcp.get_tool(descriptor.name)
            if not inspect.iscoroutinefunction(_unwrap(tool.fn)):
                sync.append(descriptor.name)
        return sorted(sync)

    return asyncio.run(_collect())


def assert_tools_async(mcp: Any) -> None:
    """Raise if any registered tool is synchronous."""
    sync = find_sync_tools(mcp)
    if sync:
        raise RuntimeError(
            "MCP tools must be async (`async def`). Sync tools run pydantic argument "
            "validation in a worker threadpool and can trigger a pydantic-core "
            "'dictionary changed size during iteration' panic under concurrent calls, "
            "which kills the server. Make these tools async: " + ", ".join(sync)
        )
