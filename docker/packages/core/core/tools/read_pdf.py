import asyncio
import io
from typing import Any

from pypdf import PdfReader

from core.tools.sandbox import (
    DEFAULT_TIMEOUT_SECONDS,
    run_in_sandbox,
)


async def readPDF(file_path: str) -> dict[str, Any]:
    """Extract text from a PDF file inside the sandbox."""
    # The subprocess read and pypdf parsing both block; run the whole body in a
    # worker thread so the event loop stays free for other tool calls.
    return await asyncio.to_thread(_read_pdf_sync, file_path)


def _read_pdf_sync(file_path: str) -> dict[str, Any]:
    result = run_in_sandbox(["cat", "--", file_path], DEFAULT_TIMEOUT_SECONDS, text=False)
    if result["returncode"] != 0:
        stderr = result.get("stderr", b"")
        if isinstance(stderr, bytes):
            stderr = stderr.decode("utf-8", errors="replace")
        return {
            "pages": [],
            "page_count": 0,
            "stderr": stderr or result.get("error", ""),
            "returncode": result["returncode"],
            "file_path": file_path,
        }

    try:
        reader = PdfReader(io.BytesIO(result["stdout"]))
        if reader.is_encrypted:
            try:
                reader.decrypt("")
            except Exception:
                return {
                    "pages": [],
                    "page_count": 0,
                    "stderr": "Encrypted PDF: unable to decrypt",
                    "returncode": 1,
                    "file_path": file_path,
                }
            if reader.is_encrypted:
                return {
                    "pages": [],
                    "page_count": 0,
                    "stderr": "Encrypted PDF: unable to decrypt",
                    "returncode": 1,
                    "file_path": file_path,
                }

        pages: list[str] = []
        for page in reader.pages:
            text = page.extract_text() or ""
            pages.append(text)

        return {
            "pages": pages,
            "page_count": len(pages),
            "stderr": "",
            "returncode": 0,
            "file_path": file_path,
        }
    except Exception as e:
        return {
            "pages": [],
            "page_count": 0,
            "stderr": str(e),
            "returncode": 1,
            "file_path": file_path,
        }
