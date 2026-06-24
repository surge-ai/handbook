"""Proxy auth token capture for core.

The proxy injects ``MCP_PROXY_TOKEN`` into core's env so the viewer's
``ProxyTokenMiddleware`` can authenticate inbound non-MCP requests.

This code captures the token at import time, makes it available via ``get_proxy_token()``,
and pops it from ``os.environ`` with ``@capture_proxy_token``, so it doesn't reach the agent's bash/python.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from functools import wraps

_TOKEN: str = ""


def capture_proxy_token[**P, R](fn: Callable[P, R]) -> Callable[P, R]:
    """Decorator: before running ``fn``, pop ``MCP_PROXY_TOKEN`` from
    ``os.environ`` and stash it for later reads via :func:`get_proxy_token`.

    Apply this to ``core.server.main()`` — the first thing that runs
    when core starts. The pop happens before any tool can be invoked
    so subprocesses can't recover the token from ``/proc/$PPID/environ``.
    """

    @wraps(fn)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        global _TOKEN
        _TOKEN = os.environ.pop("MCP_PROXY_TOKEN", "")
        return fn(*args, **kwargs)

    return wrapper


def get_proxy_token() -> str:
    """Return the captured proxy auth token, or ``""`` if not yet captured."""
    return _TOKEN
