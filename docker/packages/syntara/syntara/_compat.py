"""Shared bits for the temporary ``syntara``→``core`` compatibility shim.

``syntara`` was renamed to ``core`` (tools are now exposed un-namespaced as
``bash``/``readFile``/… and ``executePython`` was dropped). This whole package
exists only to keep pre-rename projects working while they migrate: it re-
exposes the old ``syntara`` server (namespaced ``syntara__*`` tools, legacy
toolset names, ``executePython``) by forwarding every call to ``core``.

Every forwarded call logs a deprecation warning (grep service logs for
``DEPRECATED syntara compat``) so we can see which old features are still in use
and chase down the last callers.

REMOVE AFTER 2026-06-18. Checklist:
  1. Delete the ``packages/syntara`` directory.
  2. Drop ``packages/syntara`` from the root ``pyproject.toml`` workspace.members.
  3. Remove the ``"syntara"`` entries from ``mcp_proxy/viewer.py`` name/icon maps.
  4. Remove the both-syntara-and-core guard in ``mcp_proxy.commands.mcp``
     (grep ``COMPAT_SHIM_NAME``).
"""

from __future__ import annotations

import functools
import inspect
import logging
import os
from collections.abc import Callable
from typing import Any

logger = logging.getLogger("syntara.compat")

REMOVAL_DATE = "2026-06-18"


def _log_forward(tool: str) -> None:
    logger.warning(
        "DEPRECATED syntara compat: tool %r called (forwarding to core). seed=%s. The 'syntara' "
        "compatibility package is removed after %s — migrate to the 'core' tool/toolset names.",
        tool,
        os.environ.get("WORLDBENCH_SEED", "?"),
        REMOVAL_DATE,
    )


def forwarding(name: str, core_fn: Callable[..., Any]) -> Callable[..., Any]:
    """Wrap *core_fn* so each call logs a deprecation warning, then delegates.

    ``functools.wraps`` preserves the wrapped function's signature and docstring
    so the MCP tool schema introspected from this wrapper is identical to
    core's. The exposed tool name is still set explicitly at registration time.

    The wrapper is a coroutine so the registered tool is async — core's tools are
    now coroutines, and FastMCP must run validation on the event loop rather than
    a worker threadpool (see ``async_tool_guard``).
    """

    @functools.wraps(core_fn)
    async def wrapper(*args, **kwargs):
        _log_forward(name)
        result = core_fn(*args, **kwargs)
        if inspect.isawaitable(result):
            result = await result
        return result

    wrapper.__name__ = name
    return wrapper
