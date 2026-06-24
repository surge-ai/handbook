"""Tests for the ``--current-time`` fake-clock wiring.

The proxy runs every MCP service under the ``faketime`` wrapper when a
``--current-time`` value is supplied, so the service (and anything it spawns,
notably syntara's executeBash / executePython sandboxes) observes a clock
anchored to that instant and advancing at real wall-clock rate.

Unit tests here run anywhere — they exercise the pure mapping and the argv
assembly without launching a process. The integration test is skipped unless
the ``faketime`` wrapper is actually installed on PATH.
"""

import shutil
import subprocess
import sys
from datetime import UTC, datetime

import pytest

from mcp_proxy.commands import mcp as mcp_cmd
from mcp_proxy.commands.mcp import resolve_faketime_launch, to_faketime_spec
from mcp_proxy.service import HookStep, _service_argv

# ---------------------------------------------------------------------------
# to_faketime_spec — RFC3339/ISO-8601 -> faketime wrapper's absolute timestamp
# ---------------------------------------------------------------------------


def test_spec_utc_zulu():
    assert to_faketime_spec("2025-01-15T09:00:00Z") == "2025-01-15 09:00:00"


def test_spec_naive_assumed_utc():
    assert to_faketime_spec("2025-01-15T09:00:00") == "2025-01-15 09:00:00"


def test_spec_offset_converted_to_utc():
    # 09:00 at +05:00 is 04:00 UTC.
    assert to_faketime_spec("2025-01-15T09:00:00+05:00") == "2025-01-15 04:00:00"


def test_spec_negative_offset_crosses_midnight():
    # 22:00 at -05:00 is 03:00 UTC the next day.
    assert to_faketime_spec("2025-01-15T22:00:00-05:00") == "2025-01-16 03:00:00"


def test_spec_invalid_raises():
    with pytest.raises(ValueError):
        to_faketime_spec("not-a-timestamp")


def test_spec_empty_raises():
    # An explicitly-passed empty --current-time is a bad value, not "no clock":
    # run() routes it here (via `is not None`) so it fails fast rather than
    # silently falling back to the real clock.
    with pytest.raises(ValueError):
        to_faketime_spec("")


# ---------------------------------------------------------------------------
# _service_argv — prepend the faketime wrapper when a prefix is given
# ---------------------------------------------------------------------------


# ``python``/``python3`` resolve to the proxy's own interpreter (see
# service.resolve_command), so the argv carries ``sys.executable``, not the bare
# name. The faketime prefix, when present, wraps that resolved command.
def test_argv_no_prefix():
    step = HookStep(command="python", args=["-m", "server", "--http"])
    assert _service_argv(step, None) == [sys.executable, "-m", "server", "--http"]


def test_argv_empty_prefix_is_noop():
    step = HookStep(command="python", args=["-m", "server"])
    assert _service_argv(step, []) == [sys.executable, "-m", "server"]


def test_argv_with_faketime_prefix():
    step = HookStep(command="python", args=["-m", "server"])
    prefix = ["faketime", "2025-01-15 09:00:00"]
    assert _service_argv(step, prefix) == [
        "faketime",
        "2025-01-15 09:00:00",
        sys.executable,
        "-m",
        "server",
    ]


# ---------------------------------------------------------------------------
# resolve_faketime_launch — command prefix + env, with fail-fast
# ---------------------------------------------------------------------------


def test_resolve_returns_prefix_and_utc_env(monkeypatch):
    monkeypatch.setattr(mcp_cmd.shutil, "which", lambda _name: "/usr/bin/faketime")
    prefix, env = resolve_faketime_launch("2025-01-15T09:00:00Z")
    assert prefix == ["faketime", "2025-01-15 09:00:00"]
    assert env == {"TZ": "UTC"}


def test_resolve_fails_fast_when_wrapper_missing(monkeypatch):
    monkeypatch.setattr(mcp_cmd.shutil, "which", lambda _name: None)
    with pytest.raises(SystemExit):
        resolve_faketime_launch("2025-01-15T09:00:00Z")


def test_resolve_invalid_timestamp_fails(monkeypatch):
    monkeypatch.setattr(mcp_cmd.shutil, "which", lambda _name: "/usr/bin/faketime")
    with pytest.raises(SystemExit):
        resolve_faketime_launch("not-a-timestamp")


# ---------------------------------------------------------------------------
# Integration — only when the faketime wrapper is installed
# ---------------------------------------------------------------------------

_HAS_FAKETIME = shutil.which("faketime") is not None
_SKIP_REASON = "faketime wrapper not installed on PATH"

# 2025-01-15T09:00:00Z
_FAKE_EPOCH = 1736931600
_SPEC = "2025-01-15 09:00:00"


@pytest.mark.skipif(not _HAS_FAKETIME, reason=_SKIP_REASON)
def test_faketime_fakes_date():
    out = subprocess.run(
        ["faketime", _SPEC, "date", "+%s"],
        capture_output=True,
        text=True,
        env={"TZ": "UTC", "PATH": __import__("os").environ["PATH"]},
        timeout=30,
    )
    assert out.returncode == 0, out.stderr
    reported = int(out.stdout.strip())
    # Clock advances from the anchor, so allow a small forward window.
    assert _FAKE_EPOCH <= reported < _FAKE_EPOCH + 60


@pytest.mark.skipif(not _HAS_FAKETIME, reason=_SKIP_REASON)
def test_faketime_fakes_python():
    out = subprocess.run(
        ["faketime", _SPEC, sys.executable, "-c", "import time; print(time.time())"],
        capture_output=True,
        text=True,
        env={"TZ": "UTC", "PATH": __import__("os").environ["PATH"]},
        timeout=30,
    )
    assert out.returncode == 0, out.stderr
    reported = float(out.stdout.strip())
    assert _FAKE_EPOCH <= reported < _FAKE_EPOCH + 60


def test_fake_epoch_matches_spec():
    """Guard the integration constants against drift."""
    dt = datetime(2025, 1, 15, 9, 0, 0, tzinfo=UTC)
    assert int(dt.timestamp()) == _FAKE_EPOCH
