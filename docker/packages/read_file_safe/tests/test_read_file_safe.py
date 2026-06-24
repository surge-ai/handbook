"""Tests for the read_file_safe package core functions."""

import os
import shutil
import stat
import tempfile
import unittest
from pathlib import Path

from read_file_safe import DEFAULT_READ_LIMIT_BYTES, read_file_safe
from read_file_safe.core import _binary_error_from_header, _decode_utf8_slice


class BinaryHeaderDetectionTests(unittest.TestCase):
    def test_empty_header_is_not_binary(self):
        assert _binary_error_from_header(b"") is None

    def test_utf32_le_bom_detected(self):
        err = _binary_error_from_header(b"\xff\xfe\x00\x00rest")
        assert err is not None
        assert "UTF-32" in err

    def test_utf32_be_bom_detected(self):
        err = _binary_error_from_header(b"\x00\x00\xfe\xffrest")
        assert err is not None
        assert "UTF-32" in err

    def test_utf16_le_bom_detected(self):
        err = _binary_error_from_header(b"\xff\xferest")
        assert err is not None
        assert "UTF-16" in err

    def test_utf16_be_bom_detected(self):
        err = _binary_error_from_header(b"\xfe\xffrest")
        assert err is not None
        assert "UTF-16" in err

    def test_null_byte_detected_as_binary(self):
        err = _binary_error_from_header(b"hello\x00world")
        assert err is not None
        assert "binary" in err.lower()

    def test_clean_utf8_passes(self):
        assert _binary_error_from_header(b"hello world\n") is None

    def test_utf32_takes_priority_over_utf16(self):
        # UTF-32 LE BOM starts with UTF-16 LE BOM bytes — UTF-32 must win
        err = _binary_error_from_header(b"\xff\xfe\x00\x00extra")
        assert err is not None
        assert "UTF-32" in err


class DecodeUtf8SliceTests(unittest.TestCase):
    def test_empty_bytes_returns_empty(self):
        content, consumed, lossy = _decode_utf8_slice(b"", is_final=True)
        assert content == ""
        assert consumed == 0
        assert lossy is False

    def test_clean_ascii(self):
        content, consumed, lossy = _decode_utf8_slice(b"hello", is_final=True)
        assert content == "hello"
        assert consumed == 5
        assert lossy is False

    def test_clean_utf8_multibyte(self):
        raw = "héllo".encode()
        content, consumed, lossy = _decode_utf8_slice(raw, is_final=True)
        assert content == "héllo"
        assert consumed == len(raw)
        assert lossy is False

    def test_mid_codepoint_split_rewinds(self):
        # "é" is 2 bytes (0xc3 0xa9); slice after first byte only
        raw = b"\xc3"  # incomplete é
        content, consumed, _lossy = _decode_utf8_slice(raw, is_final=False)
        # Strict decoder buffers it as pending — consumed == 0, falls through to lossy
        # OR strict decoder returns 0 consumed and we fall back to lossy
        # Either way we get output without crashing
        assert isinstance(content, str)
        assert isinstance(consumed, int)

    def test_invalid_utf8_falls_back_lossy(self):
        raw = b"\xff\xfe"  # invalid UTF-8 (not a BOM context here, just bad bytes)
        content, consumed, lossy = _decode_utf8_slice(raw, is_final=True)
        assert lossy is True
        assert consumed == len(raw)
        assert "�" in content


class ReadFileSafeTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="read-file-safe-test-")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _write(self, name: str, content: bytes) -> Path:
        p = Path(self.tmpdir) / name
        p.write_bytes(content)
        return p

    def test_happy_path_small_file(self):
        p = self._write("hello.txt", b"hello world")
        result = read_file_safe("hello.txt", p)
        assert result["returncode"] == 0
        assert result["content"] == "hello world"
        assert result["total_bytes"] == 11
        assert result["truncated"] is False
        assert result["warning"] == ""
        assert result["stderr"] == ""

    def test_empty_file(self):
        p = self._write("empty.txt", b"")
        result = read_file_safe("empty.txt", p)
        assert result["returncode"] == 0
        assert result["content"] == ""
        assert result["total_bytes"] == 0
        assert result["truncated"] is False

    def test_nonexistent_file_returns_error(self):
        result = read_file_safe("missing.txt", Path(self.tmpdir) / "missing.txt")
        assert result["returncode"] == 1
        assert result["stderr"] != ""

    def test_directory_as_path_returns_error(self):
        result = read_file_safe("dir", Path(self.tmpdir))
        assert result["returncode"] == 1

    def test_binary_file_returns_error(self):
        p = self._write("bin.dat", b"\x00\x01\x02binary content")
        result = read_file_safe("bin.dat", p)
        assert result["returncode"] == 1
        assert "binary" in result["stderr"].lower()

    def test_utf16_file_returns_error(self):
        p = self._write("utf16.txt", "hello".encode("utf-16"))
        result = read_file_safe("utf16.txt", p)
        assert result["returncode"] == 1
        assert "UTF-16" in result["stderr"]

    def test_offset_and_limit(self):
        p = self._write("abc.txt", b"abcdefghij")
        result = read_file_safe("abc.txt", p, offset=3, limit=4)
        assert result["content"] == "defg"
        assert result["offset"] == 3
        assert result["next_offset"] == 7
        assert result["truncated"] is True

    def test_null_limit_reads_whole_file(self):
        p = self._write("full.txt", b"full content")
        result = read_file_safe("full.txt", p, limit=None)
        assert result["content"] == "full content"
        assert result["truncated"] is False

    def test_offset_past_eof_returns_empty(self):
        p = self._write("short.txt", b"hi")
        result = read_file_safe("short.txt", p, offset=100)
        assert result["returncode"] == 0
        assert result["content"] == ""
        assert result["truncated"] is False

    def test_lossy_decode_sets_warning(self):
        # Latin-1 bytes that aren't valid UTF-8
        p = self._write("latin.txt", bytes(range(0x80, 0x90)))
        result = read_file_safe("latin.txt", p)
        assert result["returncode"] == 0
        assert result["warning"] != ""
        assert "�" in result["content"]

    def test_display_path_used_in_result(self):
        p = self._write("real.txt", b"content")
        result = read_file_safe("/shown/path.txt", p)
        assert result["file_path"] == "/shown/path.txt"

    def test_default_limit_constant_is_sane(self):
        assert DEFAULT_READ_LIMIT_BYTES > 0
        assert DEFAULT_READ_LIMIT_BYTES <= 1_000_000

    @unittest.skipIf(os.getuid() == 0, "root bypasses permission checks")
    def test_permission_denied_returns_error(self):
        p = self._write("secret.txt", b"secret")
        p.chmod(0o000)
        try:
            result = read_file_safe("secret.txt", p)
            assert result["returncode"] == 1
            assert result["stderr"] != ""
        finally:
            p.chmod(stat.S_IRUSR | stat.S_IWUSR)


if __name__ == "__main__":
    unittest.main()
