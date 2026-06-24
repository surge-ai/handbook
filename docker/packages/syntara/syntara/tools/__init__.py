"""Legacy ``syntara`` tool surface, forwarding to ``core``.

Every tool here delegates to the corresponding ``core`` tool (logging a
deprecation warning on each call). Two names differ from core:

* ``executeBash`` → core's renamed ``bash``.
* ``executePython`` → removed from core; reimplemented here as
  ``bash("python3 -c <code>")`` so pre-rename callers keep working.

REMOVE with the rest of the ``syntara`` package after 2026-06-18 (see
``syntara._compat``).
"""

from typing import Annotated, Any

from pydantic import Field

from core.tools import echo as _echo
from core.tools import export_state as _export_state
from core.tools import import_state as _import_state
from core.tools import listFiles as _listFiles
from core.tools import prepareGradingContext as _prepareGradingContext
from core.tools import readFile as _readFile
from core.tools import readMedia as _readMedia
from core.tools import readPDF as _readPDF
from core.tools import writeFile as _writeFile
from core.tools.bash import bash as _bash
from core.tools.sandbox import DEFAULT_TIMEOUT_SECONDS, MAX_TIMEOUT_SECONDS, run_in_sandbox
from syntara._compat import _log_forward, forwarding

echo = forwarding("echo", _echo)
listFiles = forwarding("listFiles", _listFiles)
readFile = forwarding("readFile", _readFile)
readMedia = forwarding("readMedia", _readMedia)
readPDF = forwarding("readPDF", _readPDF)
writeFile = forwarding("writeFile", _writeFile)
prepareGradingContext = forwarding("prepareGradingContext", _prepareGradingContext)
export_state = forwarding("export_state", _export_state)
import_state = forwarding("import_state", _import_state)

# executeBash is just core's bash under its old name.
executeBash = forwarding("executeBash", _bash)


async def executePython(
    code: Annotated[str, Field(description="The Python code to execute")],
    timeout_seconds: Annotated[
        int | None,
        Field(
            description=f"Timeout in seconds (default {DEFAULT_TIMEOUT_SECONDS}, max {MAX_TIMEOUT_SECONDS})",
            ge=1,
            le=MAX_TIMEOUT_SECONDS,
        ),
    ] = None,
) -> dict[str, Any]:
    """Execute Python code in an isolated directory.

    ``executePython`` was dropped from core; this shim reimplements it on core's
    sandbox, piping the source to ``python3 -`` via stdin — matching the original
    so large scripts aren't capped by the OS command-line length (``python3 -c
    <code>`` would put the whole program in argv and can hit "Argument list too
    long").
    """
    _log_forward("executePython")
    timeout = timeout_seconds or DEFAULT_TIMEOUT_SECONDS
    return run_in_sandbox(["python3", "-"], timeout, input=code)


__all__ = [
    "echo",
    "executeBash",
    "executePython",
    "export_state",
    "import_state",
    "listFiles",
    "prepareGradingContext",
    "readFile",
    "readMedia",
    "readPDF",
    "writeFile",
]
