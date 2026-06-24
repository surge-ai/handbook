import asyncio
from typing import Any

from core.tools.sandbox import (
    DEFAULT_TIMEOUT_SECONDS,
    run_in_sandbox,
)

_WRITE_SCRIPT = 'd=$(dirname -- "$1"); mkdir -p -- "${d:-.}" && tee -- "$1" >/dev/null'


async def writeFile(file_path: str, content: str) -> dict[str, Any]:
    """Write content to a file in an isolated sandbox."""
    # Offload the blocking subprocess write to a worker thread.
    result = await asyncio.to_thread(
        run_in_sandbox,
        ["bash", "-c", _WRITE_SCRIPT, "--", file_path],
        DEFAULT_TIMEOUT_SECONDS,
        input=content,
    )
    return {
        "stdout": result.get("stdout", ""),
        "stderr": result.get("stderr") or result.get("error", ""),
        "returncode": result["returncode"],
        "file_path": file_path,
    }
