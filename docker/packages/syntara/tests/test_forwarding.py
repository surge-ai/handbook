"""The syntara shim forwards every tool to core (with deprecation logging).

REMOVE with the rest of the syntara package after 2026-06-18.
"""

from __future__ import annotations

import logging

import pytest

import syntara.tools as tools
from core.tools import sandbox


@pytest.fixture
def workdir(tmp_path, monkeypatch):
    monkeypatch.setattr(sandbox, "WORKDIR", str(tmp_path))
    return tmp_path


def test_exposes_full_legacy_tool_surface():
    assert set(tools.__all__) == {
        "echo",
        "executeBash",
        "executePython",
        "export_state",
        "import_state",
        "listFiles",
        "prepareGradingContext",
        "readFile",
        "readMedia",
        "readPDF",
        "writeFile",
    }


@pytest.mark.asyncio
async def test_echo_forwards_to_core():
    # core.echo returns its message; the shim must return the same thing.
    from core.tools import echo as core_echo

    assert await tools.echo("ping") == await core_echo("ping")


@pytest.mark.asyncio
async def test_executebash_runs_a_command(workdir):
    result = await tools.executeBash("echo hello-from-shim")
    assert result["returncode"] == 0
    assert "hello-from-shim" in result["stdout"]


@pytest.mark.asyncio
async def test_executepython_runs_python(workdir):
    result = await tools.executePython("print(6 * 7)")
    assert result["returncode"] == 0
    assert result["stdout"].strip() == "42"


@pytest.mark.asyncio
async def test_executepython_handles_quotes(workdir):
    # Source goes over stdin, so embedded quotes need no escaping.
    result = await tools.executePython("print('a\"b\\'c')")
    assert result["returncode"] == 0
    assert result["stdout"].strip() == "a\"b'c"


@pytest.mark.asyncio
async def test_executepython_handles_large_script_over_stdin(workdir):
    # The whole point of stdin delivery: a script far larger than the OS argv
    # limit (ARG_MAX ~256 KiB on macOS) must run. `python3 -c <code>` would fail
    # with "Argument list too long" before Python starts.
    big = "A" * 300_000
    result = await tools.executePython(f"print(len('{big}'))")
    assert result["returncode"] == 0, result
    assert result["stdout"].strip() == "300000"


@pytest.mark.asyncio
async def test_forwarded_call_logs_deprecation(workdir, caplog):
    with caplog.at_level(logging.WARNING, logger="syntara.compat"):
        await tools.executeBash("true")
    assert any("DEPRECATED syntara compat" in r.message and "executeBash" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_executepython_logs_under_its_own_name(workdir, caplog):
    with caplog.at_level(logging.WARNING, logger="syntara.compat"):
        await tools.executePython("pass")
    assert any("executePython" in r.message for r in caplog.records)
