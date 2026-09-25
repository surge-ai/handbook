import asyncio
from typing import Annotated, Any

from pydantic import Field

from core.tools.sandbox import DEFAULT_TIMEOUT_SECONDS, run_in_sandbox

# Emit NUL-terminated records "d <name>\0" or "f <name>\0". NUL separation
# preserves filenames containing newlines, which are valid POSIX. dotglob
# includes hidden files (matching os.listdir); nullglob makes empty dirs
# produce no output.
_LIST_SCRIPT = (
    "shopt -s dotglob nullglob; "
    'if [ ! -e "$1" ]; then printf "No such directory: %s" "$1" >&2; exit 1; fi; '
    'if [ ! -d "$1" ]; then printf "Not a directory: %s" "$1" >&2; exit 1; fi; '
    'cd -- "$1" || exit 1; '
    "for e in *; do "
    'if [ -d "$e" ]; then printf "d %s\\0" "$e"; '
    'else printf "f %s\\0" "$e"; fi; '
    "done"
)


async def listFiles(
    directory: Annotated[
        str,
        Field(
            description=(
                "Directory to list, relative to the sandbox root. Use /workdir/ prefix or an "
                "absolute path within the sandbox to be explicit. Defaults to the sandbox root."
            ),
        ),
    ] = ".",
) -> dict[str, Any]:
    """List files and subdirectories in a directory inside the sandbox."""
    # Binary mode so non-UTF-8 filenames don't raise UnicodeDecodeError before
    # we get a chance to handle them. We decode each name individually below.
    # Offload the blocking subprocess call to a worker thread so it doesn't stall
    # the event loop; the fast result parsing below stays on the loop.
    result = await asyncio.to_thread(
        run_in_sandbox,
        ["bash", "-c", _LIST_SCRIPT, "--", directory],
        DEFAULT_TIMEOUT_SECONDS,
        text=False,
    )

    stderr_bytes = result.get("stderr") or b""
    stderr_str = stderr_bytes.decode("utf-8", errors="replace") if isinstance(stderr_bytes, bytes) else stderr_bytes

    if result["returncode"] != 0:
        return {
            "directory": directory,
            "files": [],
            "directories": [],
            "returncode": result["returncode"],
            "stderr": stderr_str or result.get("error", ""),
        }

    files: list[str] = []
    directories: list[str] = []
    for entry in result["stdout"].split(b"\x00"):
        if not entry:
            continue
        kind, _, name_bytes = entry.partition(b" ")
        name = name_bytes.decode("utf-8", errors="replace")
        if kind == b"d":
            directories.append(name)
        elif kind == b"f":
            files.append(name)

    files.sort()
    directories.sort()

    return {
        "directory": directory,
        "files": files,
        "directories": directories,
        "returncode": 0,
        "stderr": "",
    }
