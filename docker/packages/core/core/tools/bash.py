import asyncio
from typing import Annotated, Any

from pydantic import Field

from core.tools.sandbox import DEFAULT_TIMEOUT_SECONDS, MAX_TIMEOUT_SECONDS, run_in_sandbox


async def bash(
    command: Annotated[str, Field(description="The bash command to execute")],
    timeout_seconds: Annotated[
        int | None,
        Field(
            description=f"Timeout in seconds (default {DEFAULT_TIMEOUT_SECONDS}, max {MAX_TIMEOUT_SECONDS})",
            ge=1,
            le=MAX_TIMEOUT_SECONDS,
        ),
    ] = None,
) -> dict[str, Any]:
    """Execute a bash command in an isolated directory."""
    timeout = timeout_seconds or DEFAULT_TIMEOUT_SECONDS
    # run_in_sandbox blocks (subprocess.run) for up to `timeout` seconds; offload
    # to a worker thread so a slow command doesn't stall the event loop and block
    # other concurrent tool calls. Validation already ran on the loop.
    return await asyncio.to_thread(run_in_sandbox, ["bash", "-c", command], timeout)
