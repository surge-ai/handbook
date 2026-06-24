import base64
import io
import os
import platform
import shlex
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from mcp.types import AudioContent, BlobResourceContents, EmbeddedResource, ImageContent, TextContent
from PIL import Image
from pypdf import PdfReader, PdfWriter

from core.tools import list_files as list_files_mod
from core.tools import read_pdf as read_pdf_mod
from core.tools import sandbox
from core.tools import write_file as write_file_mod
from core.tools.bash import bash
from core.tools.list_files import listFiles
from core.tools.read_file import DEFAULT_READ_LIMIT_BYTES, readFile
from core.tools.read_media import readMedia
from core.tools.read_pdf import readPDF
from core.tools.write_file import writeFile


class ReadFileTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_workdir = sandbox.WORKDIR
        self.sandbox_dir = tempfile.mkdtemp(prefix="syntara-read-test-")
        self.workdir = os.path.join(self.sandbox_dir, "workdir")
        os.makedirs(self.workdir, exist_ok=True)
        sandbox.WORKDIR = self.workdir

    def tearDown(self):
        self.temp_dir.cleanup()
        sandbox.WORKDIR = self.original_workdir
        # Clean up sandbox
        if os.path.exists(self.sandbox_dir):
            shutil.rmtree(self.sandbox_dir)

    async def test_read_file_relative_path(self):
        # Create a file in the sandbox workdir
        test_file = os.path.join(self.workdir, "test.txt")
        with open(test_file, "w") as f:
            f.write("hello world")

        result = await readFile("test.txt")
        assert result["returncode"] == 0
        assert result["content"] == "hello world"

    async def test_read_file_absolute_path_within_workdir(self):
        # Absolute paths that point inside the configured workdir resolve
        # naturally — same code path production exercises with `/workdir/foo`.
        test_file = os.path.join(self.workdir, "test.txt")
        with open(test_file, "w") as f:
            f.write("hello from sandbox")

        result = await readFile(test_file)
        assert result["returncode"] == 0
        assert result["content"] == "hello from sandbox"

    async def test_read_file_nonexistent_file(self):
        result = await readFile("nonexistent.txt")
        assert result["returncode"] == 1
        assert "No such file" in result["stderr"]

    async def test_read_file_returns_error_on_non_utf8(self):
        """Binary / non-UTF-8 files must return a structured error, not raise."""
        path = os.path.join(self.workdir, "binary.bin")
        with open(path, "wb") as f:
            f.write(b"\xff\xfe\x00raw bytes")

        result = await readFile("binary.bin")
        assert result["returncode"] == 1
        assert result["content"] == ""
        assert result["stderr"] != ""

    @unittest.skipIf(
        platform.system() != "Linux",
        "host-path pass-through validated against /etc/hostname — Linux-specific",
    )
    async def test_read_file_passes_through_to_host_path(self):
        """Absolute paths outside the sandbox pass through; filesystem enforces access.

        In production, the core server runs as a non-privileged user; readable host
        files (like /etc/hostname) come back, unreadable ones return Permission denied.
        """
        result = await readFile("/etc/hostname")
        assert result["returncode"] == 0
        assert result["content"]  # non-empty — container/host has a hostname

    @unittest.skipIf(
        os.getuid() == 0,
        "permission-denied propagation requires a non-root test user",
    )
    async def test_read_file_propagates_permission_denied(self):
        """When the filesystem denies access, the error surfaces in stderr."""
        path = os.path.join(self.workdir, "locked.txt")
        with open(path, "w") as f:
            f.write("secret")
        os.chmod(path, 0)
        try:
            result = await readFile("locked.txt")
            assert result["returncode"] == 1
            assert "denied" in result["stderr"].lower() or "permission" in result["stderr"].lower()
        finally:
            os.chmod(path, 0o644)


def _pdf_bytes(n_pages: int, *, user_password: str | None = None, owner_password: str | None = None) -> bytes:
    writer = PdfWriter()
    for _ in range(n_pages):
        writer.add_blank_page(width=72, height=72)
    if user_password is not None or owner_password is not None:
        writer.encrypt(user_password=user_password or "", owner_password=owner_password)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


class ReadMediaTests(unittest.IsolatedAsyncioTestCase):
    PNG_BYTES = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
    )
    PDF_BYTES = _pdf_bytes(1)

    def setUp(self):
        self.original_workdir = sandbox.WORKDIR
        self.sandbox_dir = tempfile.mkdtemp(prefix="core-readmedia-test-")
        self.workdir = os.path.join(self.sandbox_dir, "workdir")
        os.makedirs(self.workdir, exist_ok=True)
        sandbox.WORKDIR = self.workdir

    def tearDown(self):
        sandbox.WORKDIR = self.original_workdir
        shutil.rmtree(self.sandbox_dir, ignore_errors=True)

    def _write(self, name: str, data: bytes) -> str:
        path = os.path.join(self.workdir, name)
        with open(path, "wb") as f:
            f.write(data)
        return path

    async def test_returns_image_content_for_png(self):
        self._write("chart.png", self.PNG_BYTES)
        result = await readMedia("/workdir/chart.png")
        image = next(block for block in result.content if isinstance(block, ImageContent))
        assert image.mimeType == "image/png"
        assert base64.b64decode(image.data) == self.PNG_BYTES

    async def test_returns_pdf_resource_for_visual_pdf(self):
        self._write("report.pdf", self.PDF_BYTES)
        result = await readMedia("report.pdf")
        resource = next(block for block in result.content if isinstance(block, EmbeddedResource))
        assert resource.resource.mimeType == "application/pdf"
        assert isinstance(resource.resource, BlobResourceContents)
        assert base64.b64decode(resource.resource.blob) == self.PDF_BYTES

    async def test_returns_audio_content_for_mp3(self):
        audio_bytes = b"\xff\xfb\x90\x00" + b"\x00" * 64  # minimal mp3-ish payload
        self._write("song.mp3", audio_bytes)
        result = await readMedia("/workdir/song.mp3")
        audio = next(block for block in result.content if isinstance(block, AudioContent))
        assert audio.mimeType == "audio/mp3"
        assert base64.b64decode(audio.data) == audio_bytes

    async def test_returns_wav_with_canonical_mime(self):
        # mimetypes guesses audio/x-wav, which Gemini doesn't document; the
        # extension map must pin the canonical mime instead.
        self._write("note.wav", b"RIFF\x00\x00\x00\x00WAVE")
        result = await readMedia("note.wav")
        audio = next(block for block in result.content if isinstance(block, AudioContent))
        assert audio.mimeType == "audio/wav"

    async def test_returns_video_resource_for_mp4(self):
        video_bytes = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64
        self._write("clip.mp4", video_bytes)
        result = await readMedia("clip.mp4")
        resource = next(block for block in result.content if isinstance(block, EmbeddedResource))
        assert resource.resource.mimeType == "video/mp4"
        assert isinstance(resource.resource, BlobResourceContents)
        assert base64.b64decode(resource.resource.blob) == video_bytes

    async def test_rejects_unsupported_mime_type(self):
        # .txt isn't multimodal — readMedia should report the unsupported type
        # rather than silently passing through to a text read.
        self._write("notes.txt", b"hello")
        result = await readMedia("notes.txt")
        text = next(block for block in result.content if isinstance(block, TextContent))
        assert "Unsupported" in text.text
        assert not any(isinstance(block, ImageContent | EmbeddedResource) for block in result.content)

    async def test_rejects_file_larger_than_cap(self):
        # Patch the cap down so we don't have to actually allocate 20 MiB.
        self._write("big.png", self.PNG_BYTES)
        with mock.patch("core.tools.read_media.MAX_MEDIA_FILE_BYTES", 1):
            result = await readMedia("big.png")
        text = next(block for block in result.content if isinstance(block, TextContent))
        assert "too large" in text.text.lower()

    async def test_workdir_prefix_with_extra_slash_does_not_escape(self):
        # `/workdir//foo.png` is a malformed-but-valid sandbox path. Without
        # normalization, os.path.join discards the workdir and reads
        # /foo.png on the host.
        self._write("inside.png", self.PNG_BYTES)
        host_outside = os.path.join(self.sandbox_dir, "inside.png")
        with open(host_outside, "wb") as f:
            f.write(b"\x00" * 64)  # different content — would be picked up if escape happens

        result = await readMedia("/workdir//inside.png")
        image = next(block for block in result.content if isinstance(block, ImageContent))
        assert base64.b64decode(image.data) == self.PNG_BYTES

    @staticmethod
    def _png_bytes(width: int, height: int) -> bytes:
        buf = io.BytesIO()
        Image.new("RGB", (width, height), (200, 30, 30)).save(buf, format="PNG")
        return buf.getvalue()

    async def test_oversized_image_is_resized_to_dimension_cap(self):
        self._write("huge.png", self._png_bytes(3000, 1500))
        result = await readMedia("huge.png")
        image = next(block for block in result.content if isinstance(block, ImageContent))
        resized = Image.open(io.BytesIO(base64.b64decode(image.data)))
        assert resized.size == (2000, 1000)  # aspect ratio preserved
        text = next(block for block in result.content if isinstance(block, TextContent))
        assert "resized from 3000x1500" in text.text

    async def test_mismatched_extension_returns_actual_mime(self):
        # A PNG named .jpeg previously got mimeType image/jpeg — a fatal provider error.
        self._write("actually_png.jpeg", self._png_bytes(32, 32))
        result = await readMedia("actually_png.jpeg")
        image = next(block for block in result.content if isinstance(block, ImageContent))
        assert image.mimeType == "image/png"

    async def test_unparseable_image_returns_graceful_error(self):
        self._write("garbage.png", b"\x00\x01\x02 not an image")
        result = await readMedia("garbage.png")
        text = next(block for block in result.content if isinstance(block, TextContent))
        assert "not a parseable image" in text.text
        assert not any(isinstance(block, ImageContent) for block in result.content)

    async def test_truncated_image_returns_graceful_error(self):
        # A valid header over a cut-off body: Image.open() succeeds lazily, so
        # without a forced decode this small image slips through unvalidated and
        # emits ImageContent the model later rejects.
        full = self._png_bytes(64, 64)
        self._write("truncated.png", full[: len(full) // 2])
        result = await readMedia("truncated.png")
        text = next(block for block in result.content if isinstance(block, TextContent))
        assert "not a parseable image" in text.text
        assert not any(isinstance(block, ImageContent) for block in result.content)

    @staticmethod
    def _exif_rotated_jpeg(width: int, height: int, orientation: int) -> bytes:
        # A landscape image tagged "rotate 90° CW" (orientation 6) displays as
        # portrait; providers honor that only on the original bytes, so a re-encode
        # must bake the rotation into the pixels.
        img = Image.new("RGB", (width, height), (10, 120, 200))
        exif = img.getexif()
        exif[0x0112] = orientation  # 0x0112 = Orientation tag
        buf = io.BytesIO()
        img.save(buf, format="JPEG", exif=exif)
        return buf.getvalue()

    async def test_exif_orientation_is_applied_on_reencode(self):
        # Oversized so it goes through the re-encode path; orientation 6 swaps the
        # 3000x1500 source to 1500x3000 before the dimension cap downscales it.
        self._write("portrait.jpg", self._exif_rotated_jpeg(3000, 1500, orientation=6))
        result = await readMedia("portrait.jpg")
        image = next(block for block in result.content if isinstance(block, ImageContent))
        out = Image.open(io.BytesIO(base64.b64decode(image.data)))
        # Rotation applied -> taller than wide; no orientation tag left on output.
        assert out.height > out.width
        assert out.getexif().get(0x0112) in (None, 1)
        text = next(block for block in result.content if isinstance(block, TextContent))
        assert "EXIF orientation" in text.text

    async def test_pdf_page_range_is_extracted(self):
        self._write("doc.pdf", _pdf_bytes(3))
        result = await readMedia("doc.pdf", pages="2")
        resource = next(block for block in result.content if isinstance(block, EmbeddedResource))
        assert isinstance(resource.resource, BlobResourceContents)
        extracted = PdfReader(io.BytesIO(base64.b64decode(resource.resource.blob)))
        assert len(extracted.pages) == 1
        text = next(block for block in result.content if isinstance(block, TextContent))
        assert "pages 2-2 of 3" in text.text

    async def test_pdf_over_page_limit_requires_pages(self):
        self._write("long.pdf", _pdf_bytes(21))
        result = await readMedia("long.pdf")
        text = next(block for block in result.content if isinstance(block, TextContent))
        assert "pages='1-20'" in text.text
        assert not any(isinstance(block, EmbeddedResource) for block in result.content)

    async def test_pdf_over_byte_cap_returns_error_with_guidance(self):
        self._write("big.pdf", self.PDF_BYTES)
        with mock.patch("core.tools.read_media.MAX_PDF_FILE_BYTES", 1):
            result = await readMedia("big.pdf")
        text = next(block for block in result.content if isinstance(block, TextContent))
        assert "too large" in text.text.lower()
        assert "readPDF" in text.text

    async def test_pdf_invalid_page_range_returns_error(self):
        self._write("doc.pdf", _pdf_bytes(3))
        result = await readMedia("doc.pdf", pages="abc")
        text = next(block for block in result.content if isinstance(block, TextContent))
        assert "Invalid pages" in text.text

    async def test_non_pdf_content_with_pdf_extension_is_rejected(self):
        self._write("fake.pdf", b"not a pdf at all")
        result = await readMedia("fake.pdf")
        text = next(block for block in result.content if isinstance(block, TextContent))
        assert "not a valid PDF" in text.text

    async def test_owner_restricted_pdf_with_empty_user_password_is_read(self):
        # Owner-only restrictions (no-print etc.) leave is_encrypted true but open
        # silently in any viewer; decrypt("") succeeds, so readMedia must embed it.
        self._write("restricted.pdf", _pdf_bytes(2, owner_password="owner-secret"))
        result = await readMedia("restricted.pdf")
        resource = next(block for block in result.content if isinstance(block, EmbeddedResource))
        assert isinstance(resource.resource, BlobResourceContents)
        assert resource.resource.mimeType == "application/pdf"

    async def test_password_protected_pdf_returns_error(self):
        # A real user password can't be opened without it, so reject gracefully.
        self._write("locked.pdf", _pdf_bytes(2, user_password="open-sesame"))
        result = await readMedia("locked.pdf")
        text = next(block for block in result.content if isinstance(block, TextContent))
        assert "password-protected" in text.text
        assert not any(isinstance(block, EmbeddedResource) for block in result.content)

    async def test_traversal_outside_workdir_does_not_get_workdir_display_path(self):
        # A relative path that escapes workdir via ../ must not be relabeled as
        # /workdir/... in the response (would otherwise mislead consumers about
        # the resource's provenance).
        outside_path = os.path.join(self.sandbox_dir, "outside.png")
        with open(outside_path, "wb") as f:
            f.write(self.PNG_BYTES)

        traversal = "../outside.png"
        result = await readMedia(traversal)
        text = next(block for block in result.content if isinstance(block, TextContent))
        assert "/workdir/" not in text.text
        # Embedded resource URI (when present) should also not claim /workdir
        for block in result.content:
            if isinstance(block, EmbeddedResource) and isinstance(block.resource, BlobResourceContents):
                assert "/workdir" not in str(block.resource.uri)


class ReadFileOffsetLimitTests(unittest.IsolatedAsyncioTestCase):
    """Pagination behavior for readFile's offset/limit args."""

    def setUp(self):
        self.original_workdir = sandbox.WORKDIR
        self.sandbox_dir = tempfile.mkdtemp(prefix="core-readfile-test-")
        self.workdir = Path(self.sandbox_dir) / "workdir"
        self.workdir.mkdir()
        sandbox.WORKDIR = str(self.workdir)

    def tearDown(self):
        sandbox.WORKDIR = self.original_workdir
        shutil.rmtree(self.sandbox_dir, ignore_errors=True)

    def _write(self, name: str, content: str) -> str:
        path = self.workdir / name
        path.write_text(content, encoding="utf-8")
        return name

    async def test_reads_default_limit_from_offset_zero(self):
        path = self._write("big.txt", "a" * (DEFAULT_READ_LIMIT_BYTES * 2))
        result = await readFile(file_path=path)
        assert result["content"] == "a" * DEFAULT_READ_LIMIT_BYTES
        assert result["offset"] == 0
        assert result["next_offset"] == DEFAULT_READ_LIMIT_BYTES
        assert result["truncated"] is True
        assert result["total_bytes"] == DEFAULT_READ_LIMIT_BYTES * 2

    async def test_reads_explicit_offset_and_limit(self):
        path = self._write("seq.txt", "abcdefghij" * 1000)
        result = await readFile(file_path=path, offset=5, limit=10)
        assert result["content"] == "fghijabcde"
        assert result["offset"] == 5
        assert result["next_offset"] == 15
        assert result["truncated"] is True

    async def test_null_limit_reads_entire_file(self):
        content = "hello world"
        path = self._write("small.txt", content)
        result = await readFile(file_path=path, limit=None)
        assert result["content"] == content
        assert result["truncated"] is False

    async def test_offset_past_end_returns_empty_not_truncated(self):
        path = self._write("short.txt", "hi")
        result = await readFile(file_path=path, offset=1000)
        assert result["content"] == ""
        assert result["truncated"] is False

    async def test_read_past_end_clamps_gracefully(self):
        path = self._write("five.txt", "hello")
        result = await readFile(file_path=path, offset=3, limit=100)
        assert result["content"] == "lo"
        assert result["truncated"] is False
        assert result["next_offset"] == 5

    async def test_pagination_covers_full_file(self):
        content = "x" * 55_000
        path = self._write("paginate.txt", content)
        collected = ""
        offset = 0
        chunk = 20_000
        for _ in range(10):
            result = await readFile(file_path=path, offset=offset, limit=chunk)
            collected += result["content"]
            offset = result["next_offset"]
            if not result["truncated"]:
                break
        assert collected == content

    async def test_default_read_returns_full_small_file(self):
        path = self._write("compat.txt", "y" * 100)
        result = await readFile(file_path=path)
        assert result["returncode"] == 0
        assert result["content"] == "y" * 100  # under limit, full file returned

    async def test_utf8_boundary_split_rewinds_cleanly(self):
        # Emoji "😀" is 4 bytes in UTF-8. A limit that lands mid-emoji should
        # rewind next_offset so the split char is picked up intact on the next
        # read — no replacement chars in the reassembled content.
        content = "hi😀bye"  # bytes: 'hi' (2) + emoji (4) + 'bye' (3) = 9 bytes
        path = self._write("unicode.txt", content)

        first = await readFile(file_path=path, offset=0, limit=4)  # 2 bytes into emoji
        assert "�" not in first["content"]
        assert first["content"] == "hi"
        assert first["next_offset"] == 2  # rewound past the split emoji bytes
        assert first["truncated"] is True

        second = await readFile(file_path=path, offset=first["next_offset"], limit=None)
        assert "�" not in second["content"]
        assert second["content"] == "😀bye"
        assert second["truncated"] is False

    async def test_binary_file_is_rejected(self):
        path = self.workdir / "binary.bin"
        path.write_bytes(b"PK\x03\x04\x00\x00mock zip header")
        result = await readFile(file_path="binary.bin")
        assert result["returncode"] == 1
        assert "binary" in result["stderr"].lower()

    async def test_utf16_bom_gets_specific_error(self):
        path = self.workdir / "utf16.txt"
        path.write_bytes(b"\xff\xfeH\x00e\x00l\x00l\x00o\x00")
        result = await readFile(file_path="utf16.txt")
        assert result["returncode"] == 1
        assert "UTF-16" in result["stderr"]

    async def test_utf32_bom_gets_specific_error(self):
        path = self.workdir / "utf32.txt"
        path.write_bytes(b"\xff\xfe\x00\x00H\x00\x00\x00")
        result = await readFile(file_path="utf32.txt")
        assert result["returncode"] == 1
        assert "UTF-32" in result["stderr"]

    async def test_binary_detection_consistent_across_slices(self):
        # File with a null byte in the header — every slice should see the
        # same rejection regardless of offset (sniff is header-based, not
        # per-slice).
        path = self.workdir / "consistent.bin"
        path.write_bytes(b"\x00" + b"text content " * 1000)
        assert (await readFile(file_path="consistent.bin", offset=0))["returncode"] == 1
        assert (await readFile(file_path="consistent.bin", offset=500))["returncode"] == 1

    async def test_binary_without_null_bytes_falls_back_to_lossy(self):
        # Binary payloads without null bytes or a BOM escape the header sniff
        # and land in the decode stage. Lossy fallback keeps them readable
        # (with '�' chars) instead of hard-failing, with a warning attached.
        path = self.workdir / "no-null.bin"
        path.write_bytes(bytes((i % 255) + 1 for i in range(4096)))
        result = await readFile(file_path="no-null.bin")
        assert result["returncode"] == 0
        assert result["warning"]  # populated — signals lossy decode
        assert "�" in result["content"]

    async def test_small_limit_mid_char_advances_with_warning(self):
        # If offset/limit lands inside a codepoint, the tool must not soft-lock.
        # Falls back to lossy decode so pagination advances, and flags the
        # lossy read via the warning field.
        path = self.workdir / "jp.txt"
        path.write_bytes("日本語".encode())  # 9 bytes
        result = await readFile(file_path="jp.txt", offset=1, limit=2)
        assert result["returncode"] == 0
        assert result["warning"]
        assert result["next_offset"] > result["offset"]  # made progress

    async def test_invalid_utf8_falls_back_to_lossy_decode(self):
        # A single malformed byte shouldn't make the rest of a file unreadable.
        # Lossy decode returns usable content with '�' in the bad spot, plus a
        # warning so the agent can see decoding wasn't clean.
        path = self.workdir / "bad.txt"
        path.write_bytes(b"hello\xc3\x28world")  # \xc3\x28 is invalid UTF-8
        result = await readFile(file_path="bad.txt")
        assert result["returncode"] == 0
        assert result["warning"]
        assert "hello" in result["content"]
        assert "world" in result["content"]

    async def test_clean_utf8_has_no_warning(self):
        # Happy path: valid UTF-8 reads should have an empty warning field so
        # agents can trust the content without inspecting it.
        path = self._write("clean.txt", "plain ASCII plus 日本語 plus 😀")
        result = await readFile(file_path=path)
        assert result["returncode"] == 0
        assert result["warning"] == ""

    async def test_truncated_utf8_at_eof_falls_back_to_lossy(self):
        # A text file whose final bytes are an incomplete multi-byte codepoint
        # (e.g. truncated during write). Reading the whole file must not error
        # — it should surface the readable prefix and flag the tail via warning.
        path = self.workdir / "truncated.txt"
        path.write_bytes(b"hello\xe2\x82")  # "\xe2\x82" starts € but is truncated
        result = await readFile(file_path="truncated.txt", limit=None)
        assert result["returncode"] == 0
        assert result["warning"]
        assert "hello" in result["content"]
        assert result["truncated"] is False  # we did reach EOF

    async def test_error_result_has_same_shape_as_success(self):
        path = self._write("ok.txt", "hello")
        success = await readFile(file_path=path)
        failure = await readFile(file_path="does-not-exist.txt")
        assert success["returncode"] == 0
        assert failure["returncode"] == 1
        assert set(success.keys()) == set(failure.keys())


class WriteFileTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.original_workdir = sandbox.WORKDIR
        self.sandbox_dir = tempfile.mkdtemp(prefix="syntara-write-test-")
        self.workdir = os.path.join(self.sandbox_dir, "workdir")
        os.makedirs(self.workdir, exist_ok=True)
        sandbox.WORKDIR = self.workdir

    def tearDown(self):
        sandbox.WORKDIR = self.original_workdir
        if os.path.exists(self.sandbox_dir):
            shutil.rmtree(self.sandbox_dir)

    async def test_write_file_relative_path(self):
        result = await writeFile("output.txt", "test content")
        assert result["returncode"] == 0

        # Verify file was written
        with open(os.path.join(self.workdir, "output.txt")) as f:
            assert f.read() == "test content"

    async def test_write_file_absolute_path_within_workdir(self):
        # Absolute paths inside the configured workdir resolve naturally —
        # same code path production exercises with `/workdir/foo`.
        path = os.path.join(self.workdir, "output.txt")
        result = await writeFile(path, "sandbox content")
        assert result["returncode"] == 0

        with open(path) as f:
            assert f.read() == "sandbox content"

    async def test_write_file_creates_subdirectories(self):
        result = await writeFile("subdir/nested/file.txt", "nested content")
        assert result["returncode"] == 0

        with open(os.path.join(self.workdir, "subdir/nested/file.txt")) as f:
            assert f.read() == "nested content"

    async def test_write_file_routes_through_run_in_sandbox(self):
        """IO must go through run_in_sandbox so the sandbox cwd/HOME apply."""
        with mock.patch.object(
            write_file_mod,
            "run_in_sandbox",
            wraps=write_file_mod.run_in_sandbox,
        ) as spy:
            result = await writeFile("routed.txt", "body")

        assert result["returncode"] == 0
        with open(os.path.join(self.workdir, "routed.txt")) as f:
            assert f.read() == "body"
        assert spy.call_count == 1
        assert spy.call_args.args[0][0] == "bash"
        assert spy.call_args.kwargs["input"] == "body"

    @unittest.skipIf(
        os.getuid() == 0,
        "permission-denied propagation requires a non-root test user",
    )
    async def test_write_file_propagates_permission_denied(self):
        """Writes that the filesystem denies surface a non-zero returncode + stderr."""
        locked_parent = os.path.join(self.workdir, "locked")
        os.makedirs(locked_parent)
        os.chmod(locked_parent, 0o555)  # noqa: S103 — deliberately read-only to exercise EPERM
        try:
            result = await writeFile("locked/newfile.txt", "x")
            assert result["returncode"] != 0
            assert result["stderr"]
        finally:
            os.chmod(locked_parent, 0o755)  # noqa: S103 — restore perms for cleanup

    # ── writeFile no longer has its own path-prefix check ──
    # The original `_denies_write` rejected any `..` / absolute path that
    # resolved outside WORKDIR. It was defense-in-depth from the proot
    # transition, but the only paths the model uid could actually reach
    # outside WORKDIR (/home/model, /tmp) are also reachable through
    # bash, and a planted `~/.bashrc` doesn't fire on non-interactive
    # `bash -c` subprocesses — so the check was guarding nothing real.
    #
    # The contract now: writeFile writes wherever the kernel says it can.
    # These tests document that contract — positive cases prove the check is
    # gone, negative cases prove path tricks can't sneak past FS perms.

    async def test_write_file_parent_traversal_obeys_fs_perms(self):
        """`..` paths now land wherever the FS permits — sibling of workdir is fine."""
        outside = os.path.join(self.sandbox_dir, "escape-permitted.txt")
        result = await writeFile("../escape-permitted.txt", "ok")
        assert result["returncode"] == 0, result
        with open(outside) as f:
            assert f.read() == "ok"

    async def test_write_file_absolute_path_outside_workdir_obeys_fs_perms(self):
        """Absolute paths outside WORKDIR succeed when the FS permits."""
        outside = os.path.join(self.sandbox_dir, "absolute-permitted.txt")
        result = await writeFile(outside, "ok")
        assert result["returncode"] == 0, result
        with open(outside) as f:
            assert f.read() == "ok"

    @unittest.skipIf(
        os.getuid() == 0,
        "FS-perm enforcement requires a non-root test user",
    )
    async def test_write_file_path_tricks_into_locked_dir_denied_by_fs(self):
        """Path tricks (relative, absolute, double-dot, double-slash) all hit
        the same FS permission check on the resolved target. None of them
        bypass it just because they took a clever route."""
        locked = os.path.join(self.sandbox_dir, "locked-by-fs")
        os.makedirs(locked)
        os.chmod(locked, 0o555)  # noqa: S103 — read-only by design
        try:
            # All of these resolve to a file under `locked`; the kernel denies
            # the write regardless of the syntactic flavor.
            tricks = [
                os.path.join(locked, "via-absolute.txt"),  # plain absolute
                "../locked-by-fs/via-relative.txt",  # `..` traversal
                "./../locked-by-fs/via-dotdot.txt",  # leading `./`
                "..//locked-by-fs//via-double-slash.txt",  # double slashes
                "subdir/../../locked-by-fs/via-up-then-into.txt",  # bounce
            ]
            for trick in tricks:
                result = await writeFile(trick, "should fail")
                assert result["returncode"] != 0, f"unexpectedly wrote via {trick!r}: {result}"
                assert result["stderr"], f"empty stderr for {trick!r}: {result}"
                target = os.path.join(locked, os.path.basename(trick))
                assert not os.path.exists(target), f"file slipped through via {trick!r}: {target}"
        finally:
            os.chmod(locked, 0o755)  # noqa: S103 — restore for cleanup

    @unittest.skipIf(
        os.getuid() == 0,
        "FS-perm enforcement requires a non-root test user",
    )
    async def test_write_file_through_symlink_obeys_target_perms(self):
        """A symlink under WORKDIR pointing at a locked dir doesn't launder
        the write — open(2) follows the link and the kernel checks the target."""
        locked = os.path.join(self.sandbox_dir, "locked-target")
        os.makedirs(locked)
        os.chmod(locked, 0o555)  # noqa: S103
        sym = os.path.join(self.workdir, "via-symlink")
        os.symlink(locked, sym)
        try:
            result = await writeFile("via-symlink/sneaky.txt", "should fail")
            assert result["returncode"] != 0, result
            assert not os.path.exists(os.path.join(locked, "sneaky.txt"))
        finally:
            os.chmod(locked, 0o755)  # noqa: S103


class ListFilesTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.original_workdir = sandbox.WORKDIR
        self.sandbox_dir = tempfile.mkdtemp(prefix="syntara-list-test-")
        self.workdir = os.path.join(self.sandbox_dir, "workdir")
        os.makedirs(self.workdir, exist_ok=True)
        sandbox.WORKDIR = self.workdir

    def tearDown(self):
        sandbox.WORKDIR = self.original_workdir
        if os.path.exists(self.sandbox_dir):
            shutil.rmtree(self.sandbox_dir)

    async def test_list_files_default_lists_workdir_root(self):
        with open(os.path.join(self.workdir, "a.txt"), "w") as f:
            f.write("a")
        with open(os.path.join(self.workdir, "b.txt"), "w") as f:
            f.write("b")
        os.makedirs(os.path.join(self.workdir, "sub"))

        result = await listFiles()
        assert result["returncode"] == 0
        assert result["files"] == ["a.txt", "b.txt"]
        assert result["directories"] == ["sub"]

    async def test_list_files_relative_path(self):
        os.makedirs(os.path.join(self.workdir, "sub"))
        with open(os.path.join(self.workdir, "sub", "inner.txt"), "w") as f:
            f.write("hi")

        result = await listFiles("sub")
        assert result["returncode"] == 0
        assert result["files"] == ["inner.txt"]
        assert result["directories"] == []

    async def test_list_files_absolute_path_within_workdir(self):
        os.makedirs(os.path.join(self.workdir, "sub"))
        with open(os.path.join(self.workdir, "sub", "inner.txt"), "w") as f:
            f.write("hi")

        result = await listFiles(os.path.join(self.workdir, "sub"))
        assert result["returncode"] == 0
        assert result["files"] == ["inner.txt"]

    async def test_list_files_sorted(self):
        for name in ["zeta", "alpha", "mid"]:
            with open(os.path.join(self.workdir, name), "w") as f:
                f.write("")
        for name in ["zdir", "adir"]:
            os.makedirs(os.path.join(self.workdir, name))

        result = await listFiles()
        assert result["files"] == ["alpha", "mid", "zeta"]
        assert result["directories"] == ["adir", "zdir"]

    async def test_list_files_empty_directory(self):
        result = await listFiles()
        assert result["returncode"] == 0
        assert result["files"] == []
        assert result["directories"] == []

    async def test_list_files_nonexistent_directory(self):
        result = await listFiles("nonexistent")
        assert result["returncode"] == 1
        assert "No such directory" in result["stderr"]

    async def test_list_files_on_a_file_returns_error(self):
        with open(os.path.join(self.workdir, "a.txt"), "w") as f:
            f.write("a")
        result = await listFiles("a.txt")
        assert result["returncode"] == 1
        assert "Not a directory" in result["stderr"]

    async def test_list_files_preserves_newline_in_name(self):
        """POSIX filenames may contain newlines; NUL-delimited protocol must preserve them."""
        weird = "a\nb.txt"
        with open(os.path.join(self.workdir, weird), "w") as f:
            f.write("x")
        with open(os.path.join(self.workdir, "normal.txt"), "w") as f:
            f.write("y")

        result = await listFiles()
        assert result["returncode"] == 0
        assert weird in result["files"]
        assert "normal.txt" in result["files"]

    async def test_list_files_handles_non_utf8_name(self):
        """Non-UTF-8 filenames from the sandbox must not crash; they decode with replacement.

        macOS APFS refuses to create files with non-UTF-8 bytes, so simulate the
        sandbox output directly rather than creating such a file on disk.
        """
        fake_stdout = b"f bad-\xff-name.txt\x00d dir-\xfe\x00"
        fake_result = {"stdout": fake_stdout, "stderr": b"", "returncode": 0}

        with mock.patch.object(list_files_mod, "run_in_sandbox", return_value=fake_result):
            result = await listFiles()

        assert result["returncode"] == 0
        assert len(result["files"]) == 1
        assert result["files"][0].startswith("bad-")
        assert result["files"][0].endswith("-name.txt")
        assert len(result["directories"]) == 1
        assert result["directories"][0].startswith("dir-")

    async def test_list_files_routes_through_run_in_sandbox(self):
        """IO must go through run_in_sandbox so proot isolation applies on prod."""
        with open(os.path.join(self.workdir, "a.txt"), "w") as f:
            f.write("a")
        os.makedirs(os.path.join(self.workdir, "sub"))

        with mock.patch.object(
            list_files_mod,
            "run_in_sandbox",
            wraps=list_files_mod.run_in_sandbox,
        ) as spy:
            result = await listFiles()

        assert result["returncode"] == 0
        assert result["files"] == ["a.txt"]
        assert result["directories"] == ["sub"]
        assert spy.call_count == 1
        assert spy.call_args.args[0][0] == "bash"


class ReadPDFTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.original_workdir = sandbox.WORKDIR
        self.sandbox_dir = tempfile.mkdtemp(prefix="syntara-read-pdf-test-")
        self.workdir = os.path.join(self.sandbox_dir, "workdir")
        os.makedirs(self.workdir, exist_ok=True)
        sandbox.WORKDIR = self.workdir

    def tearDown(self):
        sandbox.WORKDIR = self.original_workdir
        if os.path.exists(self.sandbox_dir):
            shutil.rmtree(self.sandbox_dir)

    async def test_read_pdf_pages(self):
        from pypdf import PdfWriter

        test_file = os.path.join(self.workdir, "sample.pdf")
        writer = PdfWriter()
        writer.add_blank_page(width=72, height=72)
        with open(test_file, "wb") as f:
            writer.write(f)

        result = await readPDF("sample.pdf")
        assert result["returncode"] == 0
        assert result["page_count"] == 1
        assert len(result["pages"]) == 1

    async def test_read_pdf_routes_through_run_in_sandbox(self):
        """IO must go through run_in_sandbox (binary mode) so the sandbox cwd/HOME apply."""
        from pypdf import PdfWriter

        test_file = os.path.join(self.workdir, "routed.pdf")
        writer = PdfWriter()
        writer.add_blank_page(width=72, height=72)
        with open(test_file, "wb") as f:
            writer.write(f)

        with mock.patch.object(
            read_pdf_mod,
            "run_in_sandbox",
            wraps=read_pdf_mod.run_in_sandbox,
        ) as spy:
            result = await readPDF("routed.pdf")

        assert result["returncode"] == 0
        assert spy.call_count == 1
        assert spy.call_args.args[0][0] == "cat"
        assert spy.call_args.kwargs.get("text") is False


class ExecuteBashTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.original_workdir = sandbox.WORKDIR
        self.sandbox_dir = tempfile.mkdtemp(prefix="syntara-exec-bash-test-")
        os.makedirs(self.sandbox_dir, exist_ok=True)
        sandbox.WORKDIR = self.sandbox_dir

    def tearDown(self):
        sandbox.WORKDIR = self.original_workdir
        if os.path.exists(self.sandbox_dir):
            shutil.rmtree(self.sandbox_dir)

    async def test_execute_bash_runs_python(self):
        python_bin = shlex.quote(sys.executable)
        result = await bash(f"{python_bin} -c \"print('hello')\"")
        assert result["returncode"] == 0
        assert result["stdout"].strip() == "hello"

    async def test_execute_bash_custom_timeout_succeeds(self):
        """Command completes within custom timeout"""
        result = await bash("echo done", timeout_seconds=5)
        assert result["returncode"] == 0
        assert result["stdout"].strip() == "done"

    async def test_execute_bash_custom_timeout_fails(self):
        """Command exceeds custom timeout - returns error dict instead of raising"""
        result = await bash("sleep 5", timeout_seconds=1)
        assert result["returncode"] == -1
        assert "timed out after 1 seconds" in result["error"]

    async def test_execute_bash_default_timeout(self):
        """Command uses default timeout (120s) - test it's applied"""
        from core.tools.bash import DEFAULT_TIMEOUT_SECONDS

        assert DEFAULT_TIMEOUT_SECONDS == 120
        # Quick command should succeed with default timeout
        result = await bash("echo test")
        assert result["returncode"] == 0


class TimeoutHandlingTests(unittest.IsolatedAsyncioTestCase):
    """Regression tests for timeout handling in sandbox.

    The MCP server crashed in production when subprocess.TimeoutExpired
    propagated uncaught through FastMCP's tool runner, causing double-response
    assertions (AssertionError: Request already responded to) that killed the
    server process. These tests verify that timeouts return error dicts.
    """

    def setUp(self):
        self.original_workdir = sandbox.WORKDIR
        self.sandbox_dir = tempfile.mkdtemp(prefix="syntara-timeout-test-")
        os.makedirs(self.sandbox_dir, exist_ok=True)
        sandbox.WORKDIR = self.sandbox_dir

    def tearDown(self):
        sandbox.WORKDIR = self.original_workdir
        if os.path.exists(self.sandbox_dir):
            shutil.rmtree(self.sandbox_dir)

    def test_timeout_returns_dict_not_exception(self):
        """run_in_sandbox must return a dict on timeout, never raise."""
        from core.tools.sandbox import run_in_sandbox

        result = run_in_sandbox(["sleep", "10"], timeout=1)
        assert isinstance(result, dict)
        assert result["returncode"] == -1
        assert "timed out" in result["error"]

    async def test_timeout_includes_partial_stdout(self):
        """Partial output captured before timeout is returned."""
        result = await bash(
            "echo partial; sleep 10",
            timeout_seconds=2,
        )
        assert result["returncode"] == -1
        assert "timed out" in result["error"]
        # stdout may or may not contain 'partial' depending on OS buffering,
        # but the key invariant is it must be a string, not None
        assert isinstance(result["stdout"], str)
        assert isinstance(result["stderr"], str)

    async def test_timeout_error_message_contains_duration(self):
        """Error message reports the actual timeout duration."""
        result = await bash("sleep 10", timeout_seconds=2)
        assert result["returncode"] == -1
        assert "2 seconds" in result["error"]

    async def test_bash_timeout_does_not_raise(self):
        """bash must not raise TimeoutExpired (production crash scenario)."""
        # This is the exact pattern that crashed the MCP server:
        # a long-running command exceeding the timeout must not raise.
        try:
            result = await bash("sleep 10", timeout_seconds=1)
        except Exception as e:
            self.fail(f"bash raised {type(e).__name__}: {e}")
        assert result["returncode"] == -1


class SandboxWorkdirTests(unittest.IsolatedAsyncioTestCase):
    """run_in_sandbox auto-creates WORKDIR and runs commands there."""

    def setUp(self):
        self.original_workdir = sandbox.WORKDIR
        self.sandbox_dir = tempfile.mkdtemp(prefix="syntara-workdir-test-")
        os.makedirs(self.sandbox_dir, exist_ok=True)
        sandbox.WORKDIR = self.sandbox_dir

    def tearDown(self):
        sandbox.WORKDIR = self.original_workdir
        if os.path.exists(self.sandbox_dir):
            shutil.rmtree(self.sandbox_dir)

    async def test_workdir_created(self):
        # Drop the dir, then trigger sandbox setup — run_in_sandbox should mkdir.
        shutil.rmtree(sandbox.WORKDIR)
        await bash("echo test")
        assert os.path.isdir(sandbox.WORKDIR)

    async def test_home_env_inherited_not_overridden(self):
        # HOME should come from the parent (mcp.json sets HOME=/home/model on the
        # server process); run_in_sandbox no longer overrides it to WORKDIR — the
        # agent's shell needs its real home for dotfiles.
        result = await bash("echo $HOME")
        assert result["returncode"] == 0
        assert result["stdout"].strip() != sandbox.WORKDIR
        assert result["stdout"].strip() == os.environ.get("HOME", "")

    async def test_ls_lists_workdir_contents(self):
        """ls in the sandbox cwd lists files written into WORKDIR."""
        with open(os.path.join(sandbox.WORKDIR, "test.txt"), "w") as f:
            f.write("hello")

        result = await bash("ls")
        assert result["returncode"] == 0
        assert "test.txt" in result["stdout"]

    async def test_mcp_proxy_token_not_visible_to_agent_shell(self):
        with mock.patch.dict(os.environ, {"MCP_PROXY_TOKEN": "secret-do-not-leak"}):
            result = await bash("echo TOKEN=$MCP_PROXY_TOKEN")
        assert result["returncode"] == 0
        assert "secret-do-not-leak" not in result["stdout"]
        assert result["stdout"].strip() == "TOKEN="

    async def test_faketime_shared_stripped_from_agent_shell(self):
        """FAKETIME_SHARED must not reach the sandbox: libfaketime would try to
        open the root-owned /dev/shm semaphore it names and hang the dropped-uid
        child on its first clock read. LD_PRELOAD / FAKETIME are intentionally
        kept so the clock stays faked."""
        with mock.patch.dict(os.environ, {"FAKETIME_SHARED": "/faketime_sem_1 /faketime_shm_1"}):
            result = await bash("echo SHARED=$FAKETIME_SHARED")
        assert result["returncode"] == 0
        assert result["stdout"].strip() == "SHARED="


if __name__ == "__main__":
    unittest.main()
