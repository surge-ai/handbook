import asyncio
import os
from typing import Annotated

from pydantic import Field

from core.tools import sandbox
from read_file_safe import (
    DEFAULT_READ_LIMIT_BYTES,
    HEADER_SNIFF_BYTES,
    ReadFileSafeResult,
    assemble_read_result,
    error_result,
)


async def readFile(
    file_path: Annotated[
        str,
        Field(
            description="Path to the file. Use /workdir/ prefix for sandbox files, or an absolute path within the sandbox."
        ),
    ],
    offset: Annotated[
        int,
        Field(
            description="Byte offset to start reading from. Use 0 for the beginning or a previous call's next_offset to continue.",
            ge=0,
        ),
    ] = 0,
    limit: Annotated[
        int | None,
        Field(
            description=(
                f"Maximum number of bytes to read. Defaults to {DEFAULT_READ_LIMIT_BYTES}. "
                "Pass null to read from offset to end of file."
            ),
            ge=1,
        ),
    ] = DEFAULT_READ_LIMIT_BYTES,
) -> ReadFileSafeResult:
    """Read a file from the sandbox, with optional offset/limit (bytes) for paginating large files."""
    workdir = os.path.realpath(sandbox.WORKDIR)
    # os.path.join discards the workdir prefix if file_path is absolute (e.g.
    # "/etc/hostname" or "/workdir/foo" in production where /workdir is real),
    # so absolute paths pass through to the host and FS permissions enforce
    # access.
    resolved_path = os.path.join(workdir, file_path)

    # The core server runs as root, so we must NOT open() agent-supplied
    # paths in-process — that would read /app, /opt/venv, etc. Read the bytes as
    # the unprivileged sandbox user (filesystem perms gate access, TOCTOU-safe),
    # then run the normal decode/binary-sniff/pagination on what came back.
    # The read spawns a subprocess and blocks; offload to a worker thread to
    # keep the event loop free.
    def _read() -> ReadFileSafeResult:
        try:
            total_bytes, header, raw, start = sandbox.agent_read_window(
                resolved_path, offset=offset, limit=limit, sniff=HEADER_SNIFF_BYTES
            )
        except sandbox.AgentReadError as e:
            return error_result(file_path, offset, str(e))
        return assemble_read_result(file_path, total_bytes, header, raw, start, limit)

    return await asyncio.to_thread(_read)
