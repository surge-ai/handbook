import asyncio
import base64
import io
import mimetypes
import os
import re
from typing import Annotated
from urllib.parse import quote

from fastmcp.tools import ToolResult
from mcp.types import AudioContent, BlobResourceContents, EmbeddedResource, ImageContent, TextContent
from PIL import Image, ImageOps
from pydantic import AnyUrl, Field
from pypdf import PdfReader, PdfWriter

from core.tools import sandbox

MAX_MEDIA_FILE_BYTES = 20 * 1024 * 1024
# PDFs pass through unmodified, and Anthropic caps the total request at 32 MB
# base64 — beyond 10 MiB a single document plus history risks killing the run.
MAX_PDF_FILE_BYTES = 10 * 1024 * 1024
# Larger PDFs/page counts must be read in page ranges (mirrors Claude Code's Read tool).
MAX_PDF_PAGES_PER_READ = 20
# Anthropic rejects images over 2000px on either edge once a request carries
# >20 images, and downscales to ~2576px server-side anyway.
MAX_IMAGE_DIMENSION_PX = 2000
# Keeps the base64 form under the strictest per-image provider cap (5 MB).
MAX_ENCODED_IMAGE_BYTES = int(3.75 * 1024 * 1024)
JPEG_QUALITY_LADDER = (85, 70, 55)

SUPPORTED_IMAGE_MIME_TYPES = {
    "image/gif",
    "image/jpeg",
    "image/png",
    "image/webp",
}
PDF_MIME_TYPE = "application/pdf"
# Pinned per extension to Gemini's documented inlineData mimes — Python's
# mimetypes would guess non-canonical types like audio/x-wav, video/quicktime.
AV_MIME_TYPES_BY_EXTENSION = {
    ".wav": "audio/wav",
    ".mp3": "audio/mp3",
    ".aif": "audio/aiff",
    ".aiff": "audio/aiff",
    ".aac": "audio/aac",
    ".ogg": "audio/ogg",
    ".flac": "audio/flac",
    ".mp4": "video/mp4",
    ".mpeg": "video/mpeg",
    ".mpg": "video/mpeg",
    ".mov": "video/mov",
    ".avi": "video/avi",
    ".flv": "video/x-flv",
    ".webm": "video/webm",
    ".wmv": "video/wmv",
    ".3gp": "video/3gpp",
    ".3gpp": "video/3gpp",
}
SUPPORTED_AUDIO_MIME_TYPES = {m for m in AV_MIME_TYPES_BY_EXTENSION.values() if m.startswith("audio/")}
SUPPORTED_MEDIA_MIME_TYPES = SUPPORTED_IMAGE_MIME_TYPES | {PDF_MIME_TYPE} | set(AV_MIME_TYPES_BY_EXTENSION.values())
WORKDIR_PREFIX = "/workdir/"


def _error_result(*, file_path: str, mime_type: str | None, stderr: str) -> ToolResult:
    label = mime_type or "unknown"
    return ToolResult(
        content=[
            TextContent(
                type="text",
                text=f"readMedia failed for {file_path} ({label}): {stderr}",
            )
        ]
    )


def _media_mime_type(file_path: str) -> str | None:
    _root, ext = os.path.splitext(file_path)
    av_mime = AV_MIME_TYPES_BY_EXTENSION.get(ext.lower())
    if av_mime is not None:
        return av_mime
    mime_type, _encoding = mimetypes.guess_type(file_path)
    if mime_type in SUPPORTED_MEDIA_MIME_TYPES:
        return mime_type
    return None


def _resolve_read_path(file_path: str, workdir: str) -> str:
    if file_path.startswith(WORKDIR_PREFIX):
        # lstrip("/") handles "/workdir//foo" — without it, the leading slash on
        # the remainder makes os.path.join discard workdir and escape the sandbox.
        return os.path.join(workdir, file_path.removeprefix(WORKDIR_PREFIX).lstrip("/"))
    if file_path == "/workdir":
        return workdir

    # os.path.join discards the workdir prefix if file_path is absolute (e.g.
    # "/etc/hostname"), so absolute paths pass through to the host and FS
    # permissions enforce access.
    return os.path.join(workdir, file_path)


def _workdir_relative_path(resolved_path: str, workdir: str) -> str | None:
    # realpath resolves "../" segments so paths that escape workdir don't get
    # treated as inside it via a raw commonpath check.
    canonical = os.path.realpath(resolved_path)
    try:
        if os.path.commonpath([canonical, workdir]) != workdir:
            return None
    except ValueError:
        return None

    rel_path = os.path.relpath(canonical, workdir)
    return "" if rel_path == "." else rel_path


def _display_path(file_path: str, resolved_path: str, workdir: str) -> str:
    rel_path = _workdir_relative_path(resolved_path, workdir)
    if rel_path is not None:
        return "/workdir" if rel_path == "" else f"/workdir/{rel_path}"
    return file_path


def _resource_uri(display_path: str) -> AnyUrl:
    return AnyUrl(f"file://{quote(display_path, safe='/')}")


def _has_alpha(img: Image.Image) -> bool:
    if img.mode in ("RGBA", "LA", "PA"):
        return True
    return img.mode == "P" and "transparency" in img.info


def _encode_image(img: Image.Image, output_format: str, quality: int) -> bytes:
    buf = io.BytesIO()
    if output_format == "JPEG":
        img.save(buf, format="JPEG", quality=quality)
    else:
        img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def _parse_page_range(pages: str) -> tuple[int, int] | None:
    """Parse a 1-indexed page range like "3" or "1-10". Returns None if malformed."""
    match = re.fullmatch(r"(\d+)(?:-(\d+))?", pages.strip())
    if not match:
        return None
    start = int(match.group(1))
    end = int(match.group(2)) if match.group(2) else start
    if start < 1 or end < start:
        return None
    return start, end


def _normalize_image(raw: bytes) -> tuple[bytes, str, list[str]] | str:
    """Make image bytes safe to send to a model provider.

    Detects the real format from the bytes (a mime type that mismatches the
    content is a fatal provider error), downscales anything over
    MAX_IMAGE_DIMENSION_PX on the long edge, and re-encodes oversized payloads
    under MAX_ENCODED_IMAGE_BYTES. Returns (bytes, mime_type, notes) on success
    or an error message string.
    """
    try:
        img = Image.open(io.BytesIO(raw))
        width, height = img.size
        actual_format = img.format
        # Force a full decode now: Image.open() is lazy, so a valid header over a
        # truncated/corrupt body otherwise slips through the pass-through branch
        # below and emits ImageContent the model later rejects. load() raises here
        # on a bad body, turning it into a graceful parse error.
        img.load()
    except Exception:
        return "file content is not a parseable image (corrupt file, or extension does not match content?)"

    actual_mime = Image.MIME.get(actual_format or "", None)
    notes: list[str] = []

    # Phone photos carry an EXIF Orientation tag that providers honor only on the
    # original bytes; once we re-encode, that metadata is dropped, so bake the
    # rotation into the pixels. Values 2-8 mean a transform is needed (1/absent is
    # a no-op); when one applies we must re-encode to emit the rotated result
    # rather than passing the unrotated raw bytes through.
    needs_orientation_fix = img.getexif().get(0x0112, 1) not in (0, 1)
    if needs_orientation_fix:
        img = ImageOps.exif_transpose(img)
        width, height = img.size
        notes.append("applied EXIF orientation")

    needs_resize = max(width, height) > MAX_IMAGE_DIMENSION_PX
    needs_reencode = (
        needs_resize
        or needs_orientation_fix
        or actual_mime not in SUPPORTED_IMAGE_MIME_TYPES
        or len(raw) > MAX_ENCODED_IMAGE_BYTES
    )
    if not needs_reencode:
        assert actual_mime is not None
        return raw, actual_mime, notes

    try:
        if getattr(img, "is_animated", False):
            img.seek(0)
            img.load()
            notes.append("animated image flattened to its first frame")

        if needs_resize:
            img.thumbnail((MAX_IMAGE_DIMENSION_PX, MAX_IMAGE_DIMENSION_PX), Image.Resampling.LANCZOS)
            notes.append(f"resized from {width}x{height} to {img.width}x{img.height} to fit model image limits")

        # Lossless sources stay PNG when they fit the byte budget (keeps text
        # crisp for transcription); photos and anything oversized go to JPEG.
        lossless_source = actual_format in ("PNG", "GIF", "BMP", "TIFF")
        encoded: bytes | None = None
        output_mime = "image/png"
        if lossless_source:
            png_img = img
            encoded = _encode_image(png_img, "PNG", 0)
            if len(encoded) > MAX_ENCODED_IMAGE_BYTES:
                encoded = None

        if encoded is None:
            jpeg_img = img
            if _has_alpha(jpeg_img):
                # JPEG has no alpha channel; composite onto white.
                background = Image.new("RGB", jpeg_img.size, (255, 255, 255))
                background.paste(jpeg_img.convert("RGBA"), mask=jpeg_img.convert("RGBA").split()[-1])
                jpeg_img = background
            elif jpeg_img.mode != "RGB":
                jpeg_img = jpeg_img.convert("RGB")

            for quality in JPEG_QUALITY_LADDER:
                encoded = _encode_image(jpeg_img, "JPEG", quality)
                if len(encoded) <= MAX_ENCODED_IMAGE_BYTES:
                    break
            while encoded is not None and len(encoded) > MAX_ENCODED_IMAGE_BYTES and min(jpeg_img.size) > 200:
                jpeg_img = jpeg_img.resize(
                    (max(1, int(jpeg_img.width * 0.75)), max(1, int(jpeg_img.height * 0.75))),
                    Image.Resampling.LANCZOS,
                )
                encoded = _encode_image(jpeg_img, "JPEG", JPEG_QUALITY_LADDER[-1])
            output_mime = "image/jpeg"

        if encoded is None or len(encoded) > MAX_ENCODED_IMAGE_BYTES:
            return "could not compress image under the per-image size limit"
    except Exception as e:
        return f"failed to convert image for model consumption: {e}"

    if output_mime != actual_mime:
        notes.append(f"re-encoded from {actual_mime or 'unknown format'} to {output_mime}")
    return encoded, output_mime, notes


async def readMedia(
    file_path: Annotated[
        str,
        Field(
            description=(
                "Path to an image (gif/jpeg/png/webp), PDF, audio (wav/mp3/aiff/aac/ogg/flac), or video "
                "(mp4/mpeg/mov/avi/flv/webm/wmv/3gpp) file. "
                "Use /workdir/ prefix for sandbox files, or an absolute path within the sandbox. "
                "Returns multimodal MCP content; images are automatically downscaled/re-encoded to fit "
                "model limits (max 2000px on the long edge). Audio/video require a model with native "
                "audio/video support (e.g. Gemini) — other models see only a text placeholder. "
                "Use readFile for text and readPDF for PDF text extraction."
            )
        ),
    ],
    pages: Annotated[
        str | None,
        Field(
            description=(
                "PDF page range to read, 1-indexed, e.g. '3' or '1-10' (max 20 pages per read). "
                "Required for PDFs over 20 pages or 10 MiB. Ignored for images."
            )
        ),
    ] = None,
) -> ToolResult:
    """Read an image, PDF, audio, or video file and return it as multimodal MCP content (base64-encoded, capped at 20 MiB; images auto-resized to model limits, large PDFs read in page ranges)."""
    # File read, base64 encode, and image/PDF re-encoding all block; run in a worker thread.
    return await asyncio.to_thread(_read_media_sync, file_path, pages)


def _read_pdf_sync(*, file_path: str, display_path: str, raw: bytes, file_size: int, pages: str | None) -> ToolResult:
    try:
        reader = PdfReader(io.BytesIO(raw))
        # is_encrypted is true even for owner-restricted PDFs that carry only
        # permission flags (no-print etc.) and open silently in any viewer.
        # decrypt("") succeeds for those; only a real user password fails it.
        if reader.is_encrypted and not reader.decrypt(""):
            return _error_result(
                file_path=file_path,
                mime_type=PDF_MIME_TYPE,
                stderr="PDF is password-protected (requires a password to open)",
            )
        total_pages = len(reader.pages)
    except Exception:
        return _error_result(file_path=file_path, mime_type=PDF_MIME_TYPE, stderr="file content is not a valid PDF")

    if pages is None:
        if file_size > MAX_PDF_FILE_BYTES or total_pages > MAX_PDF_PAGES_PER_READ:
            return _error_result(
                file_path=file_path,
                mime_type=PDF_MIME_TYPE,
                stderr=(
                    f"PDF is too large to attach whole ({total_pages} pages, {file_size} bytes; "
                    f"limits: {MAX_PDF_PAGES_PER_READ} pages, {MAX_PDF_FILE_BYTES} bytes). "
                    f"Pass pages='1-{min(total_pages, MAX_PDF_PAGES_PER_READ)}' to read a page range, "
                    "or use readPDF to extract the text."
                ),
            )
        blob = raw
        page_note = ""
    else:
        page_range = _parse_page_range(pages)
        if page_range is None:
            return _error_result(
                file_path=file_path,
                mime_type=PDF_MIME_TYPE,
                stderr=f"Invalid pages value {pages!r}. Use a 1-indexed page or range, e.g. '3' or '1-10'.",
            )
        start, end = page_range
        if start > total_pages:
            return _error_result(
                file_path=file_path,
                mime_type=PDF_MIME_TYPE,
                stderr=f"Page {start} is out of range: the PDF has {total_pages} pages.",
            )
        end = min(end, total_pages)
        if end - start + 1 > MAX_PDF_PAGES_PER_READ:
            return _error_result(
                file_path=file_path,
                mime_type=PDF_MIME_TYPE,
                stderr=f"Page range {start}-{end} spans more than {MAX_PDF_PAGES_PER_READ} pages. Read it in smaller chunks.",
            )
        try:
            writer = PdfWriter()
            for index in range(start - 1, end):
                writer.add_page(reader.pages[index])
            buf = io.BytesIO()
            writer.write(buf)
            blob = buf.getvalue()
        except Exception as e:
            return _error_result(
                file_path=file_path, mime_type=PDF_MIME_TYPE, stderr=f"failed to extract pages {start}-{end}: {e}"
            )
        if len(blob) > MAX_PDF_FILE_BYTES:
            return _error_result(
                file_path=file_path,
                mime_type=PDF_MIME_TYPE,
                stderr=(
                    f"Pages {start}-{end} are still {len(blob)} bytes (limit {MAX_PDF_FILE_BYTES}). "
                    "Read a smaller range, or use readPDF to extract the text."
                ),
            )
        page_note = f" (pages {start}-{end} of {total_pages})"

    return ToolResult(
        content=[
            TextContent(
                type="text",
                text=f"Read {display_path} as {PDF_MIME_TYPE} ({len(blob)} bytes){page_note}.",
            ),
            EmbeddedResource(
                type="resource",
                resource=BlobResourceContents(
                    uri=_resource_uri(display_path),
                    mimeType=PDF_MIME_TYPE,
                    blob=base64.b64encode(blob).decode("ascii"),
                ),
            ),
        ]
    )


def _read_media_sync(file_path: str, pages: str | None = None) -> ToolResult:
    workdir = os.path.realpath(sandbox.WORKDIR)
    resolved_path = _resolve_read_path(file_path, workdir)
    mime_type = _media_mime_type(file_path)
    if mime_type is None:
        return _error_result(
            file_path=file_path,
            mime_type=None,
            stderr=f"Unsupported media type. Supported: {sorted(SUPPORTED_MEDIA_MIME_TYPES)}",
        )

    # Read the bytes through the unprivileged sandbox reader so the open() runs
    # as the sandbox user (the core server itself is root). Filesystem
    # permissions — not the server's uid — gate what the agent can reach, so an
    # absolute path to /app, /opt/venv, /__modal, etc. fails with EACCES exactly
    # as it would in the agent's own shell. The reader also rejects non-regular
    # files (a FIFO/device under /workdir would otherwise block a worker until
    # the timeout) via an O_NONBLOCK + S_ISREG check on the opened fd, and
    # limit=MAX+1 bounds the read so an oversized file isn't slurped whole.
    try:
        file_size, _header, raw, _start = sandbox.agent_read_window(
            resolved_path, offset=0, limit=MAX_MEDIA_FILE_BYTES + 1, sniff=0
        )
    except sandbox.AgentReadError as e:
        return _error_result(file_path=file_path, mime_type=mime_type, stderr=str(e))

    if file_size > MAX_MEDIA_FILE_BYTES:
        return _error_result(
            file_path=file_path,
            mime_type=mime_type,
            stderr=(
                f"File too large for media read: {file_size} bytes exceeds {MAX_MEDIA_FILE_BYTES} bytes. "
                "Shrink it first (e.g. downscale the image, or for a PDF use readPDF / render individual pages)."
            ),
        )

    display_path = _display_path(file_path, resolved_path, workdir)

    if mime_type in SUPPORTED_IMAGE_MIME_TYPES:
        normalized = _normalize_image(raw)
        if isinstance(normalized, str):
            return _error_result(file_path=file_path, mime_type=mime_type, stderr=normalized)
        data, actual_mime, notes = normalized

        suffix = f" ({'; '.join(notes)})" if notes else ""
        text = TextContent(
            type="text",
            text=f"Read {display_path} as {actual_mime} ({len(data)} bytes).{suffix}",
        )
        return ToolResult(
            content=[
                text,
                ImageContent(type="image", data=base64.b64encode(data).decode("ascii"), mimeType=actual_mime),
            ]
        )

    encoded = base64.b64encode(raw).decode("ascii")
    text = TextContent(
        type="text",
        text=f"Read {display_path} as {mime_type} ({file_size} bytes).",
    )

    if mime_type in SUPPORTED_AUDIO_MIME_TYPES:
        return ToolResult(
            content=[
                text,
                AudioContent(type="audio", data=encoded, mimeType=mime_type),
            ]
        )

    if mime_type == PDF_MIME_TYPE:
        return _read_pdf_sync(file_path=file_path, display_path=display_path, raw=raw, file_size=file_size, pages=pages)

    # Video: MCP has no dedicated content type, so embed as a blob
    # resource; runners forward supported mime types as native model content.
    return ToolResult(
        content=[
            text,
            EmbeddedResource(
                type="resource",
                resource=BlobResourceContents(
                    uri=_resource_uri(display_path),
                    mimeType=mime_type,
                    blob=encoded,
                ),
            ),
        ]
    )
