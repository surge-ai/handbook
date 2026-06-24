"""Safe file reading: binary format detection, incremental UTF-8 decode with lossy fallback,
and byte-offset pagination. Never raises — all errors are returned as structured dicts."""

from __future__ import annotations

import codecs
import os
from pathlib import Path
from typing import TypedDict


class ReadFileSafeResult(TypedDict):
    content: str
    returncode: int
    file_path: str
    offset: int
    next_offset: int
    total_bytes: int
    truncated: bool
    warning: str
    stderr: str


DEFAULT_READ_LIMIT_BYTES = 200_000
HEADER_SNIFF_BYTES = 8_192

_LOSSY_DECODE_WARNING = (
    "Some bytes could not be decoded as UTF-8 and were replaced with '\\N{REPLACEMENT CHARACTER}' (U+FFFD) "
    "characters. The file may be binary or use a non-UTF-8 encoding — check the "
    "content, and consider using readPDF or bash with `file <path>` / `iconv` "
    "if it looks garbled."
)


def error_result(display_path: str, offset: int, message: str) -> ReadFileSafeResult:
    return ReadFileSafeResult(
        content="",
        returncode=1,
        file_path=display_path,
        offset=offset,
        next_offset=offset,
        total_bytes=0,
        truncated=False,
        warning="",
        stderr=message,
    )


def _binary_error_from_header(header: bytes) -> str | None:
    """Return a user-facing error if the header indicates a non-UTF-8 binary format."""
    if not header:
        return None
    if header[:4] in (b"\xff\xfe\x00\x00", b"\x00\x00\xfe\xff"):
        return (
            "File is UTF-32 encoded; readFile only supports UTF-8. "
            "Convert first via bash, e.g.: "
            "`iconv -f UTF-32 -t UTF-8 <path> > <path>.utf8`"
        )
    if header[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return (
            "File is UTF-16 encoded; readFile only supports UTF-8. "
            "Convert first via bash, e.g.: "
            "`iconv -f UTF-16 -t UTF-8 <path> > <path>.utf8`"
        )
    if b"\x00" in header:
        return (
            "File appears to be binary (null bytes in header). readFile is text-only. "
            "Use readPDF for PDFs, or bash with e.g. `xxd` / `file <path>` "
            "if you need raw bytes or to identify the format."
        )
    return None


def _decode_utf8_slice(raw: bytes, is_final: bool) -> tuple[str, int, bool]:
    """Decode one UTF-8 page. Returns (content, bytes_consumed, lossy).

    Two-stage strategy:
      1. Strict incremental decode — rewinds cleanly at mid-codepoint boundaries.
      2. Lossy fallback — kicks in when strict fails; invalid bytes become U+FFFD.
    """
    if not raw:
        return "", 0, False

    decoder = codecs.getincrementaldecoder("utf-8")(errors="strict")
    try:
        content = decoder.decode(raw, final=is_final)
        pending, _ = decoder.getstate()
        consumed = len(raw) - len(pending)
        if consumed > 0:
            return content, consumed, False
    except UnicodeDecodeError:
        pass

    return raw.decode("utf-8", errors="replace"), len(raw), True


def assemble_read_result(
    display_path: str,
    total_bytes: int,
    header: bytes,
    raw: bytes,
    start: int,
    limit: int | None,
) -> ReadFileSafeResult:
    """Build a ``ReadFileSafeResult`` from already-read bytes.

    Pure (no I/O): the caller supplies ``total_bytes``, the ``header`` (first
    ``HEADER_SNIFF_BYTES`` for binary detection) and ``raw`` (the slice at
    ``start`` of length ``limit``). Split out from :func:`read_file_safe` so a
    caller that must perform the actual read under a different privilege (e.g.
    syntara reading as the unprivileged sandbox user) can reuse the decoding /
    binary-sniff / pagination logic without this module doing the ``open()``.
    """
    binary_err = _binary_error_from_header(header)
    if binary_err:
        return error_result(display_path, start, binary_err)

    is_final = limit is None or len(raw) < limit
    content, consumed, lossy = _decode_utf8_slice(raw, is_final)
    next_offset = start + consumed

    return ReadFileSafeResult(
        content=content,
        returncode=0,
        file_path=display_path,
        offset=start,
        next_offset=next_offset,
        total_bytes=total_bytes,
        truncated=next_offset < total_bytes,
        warning=_LOSSY_DECODE_WARNING if lossy else "",
        stderr="",
    )


def read_file_safe(
    display_path: str,
    resolved_path: Path | str,
    offset: int = 0,
    limit: int | None = DEFAULT_READ_LIMIT_BYTES,
) -> ReadFileSafeResult:
    """Read a file at an already-resolved absolute path.

    `display_path` is used only for reporting (returned as `file_path` in the result).
    The caller is responsible for path resolution and security checks.
    """
    resolved_path = Path(resolved_path)
    try:
        total_bytes = os.path.getsize(resolved_path)
        start = min(offset, total_bytes)

        with open(resolved_path, "rb") as f:
            header = f.read(min(HEADER_SNIFF_BYTES, total_bytes))
            f.seek(start)
            raw = f.read() if limit is None else f.read(limit)

        return assemble_read_result(display_path, total_bytes, header, raw, start, limit)
    except Exception as e:
        return error_result(display_path, offset, str(e))
